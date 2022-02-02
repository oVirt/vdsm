#
# Copyright 2009-2017 Red Hat, Inc.
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA
#
# Refer to the README and COPYING files for full details of the license
#

from __future__ import absolute_import

import os
from glob import iglob, glob
import logging
import threading
import errno
import uuid
import codecs
from functools import partial
from weakref import proxy

import six

from vdsm.common import concurrent
from vdsm.common.config import config
from vdsm.common.panic import panic
from vdsm.storage import blockSD
from vdsm.storage import constants as sc
from vdsm.storage import exception as se
from vdsm.storage import fileSD
from vdsm.storage import fileUtils
from vdsm.storage import guarded
from vdsm.storage import image
from vdsm.storage import mailbox
from vdsm.storage import merge
from vdsm.storage import misc
from vdsm.storage import mount
from vdsm.storage import resourceManager as rm
from vdsm.storage import sd
from vdsm.storage import spwd
from vdsm.storage import xlease
from vdsm.storage.formatconverter import DefaultFormatConverter
from vdsm.storage.sdc import sdCache
from vdsm.storage.securable import secured, unsecured, SecureError

SPM_ACQUIRED = 'SPM'
SPM_CONTEND = 'Contend'
SPM_FREE = 'Free'
SPM_ID_FREE = -1
LVER_INVALID = -1


class DisconnectedPool(object):
    """
    Dummy storage pool used when we are not connected to a storage pool.

    Any access will fail with se.StoragePoolNotConnected.

    This avoids races such as::

        if self._pool:
            # Pool was not None, but now it is None
            self._pool.do_something()

    With this dummy pool, you should simply use the pool::

        self._pool.do_something()

    If the pool is not connected, we raise the correct error.
    """
    def is_connected(self):
        return False

    def __getattr__(self, name):
        raise se.StoragePoolNotConnected


@secured
class StoragePool(object):
    '''
    StoragePool object should be relatively cheap to construct. It should defer
    any heavy lifting activities until the time it is really needed.
    '''

    log = logging.getLogger('storage.storagepool')

    def __init__(self, spUUID, domainMonitor, taskManager):
        self._secured = threading.Event()
        self._formatConverter = DefaultFormatConverter()
        self._domainsToUpgrade = []
        self.lock = threading.RLock()
        self._set_insecure()
        self.spUUID = str(spUUID)
        self.poolPath = os.path.join(sc.REPO_DATA_CENTER, self.spUUID)
        self.id = SPM_ID_FREE
        self.taskMng = taskManager
        self.hsmMailer = None
        self.spmMailer = None
        self.masterDomain = None
        self.spmRole = SPM_FREE
        self.domainMonitor = domainMonitor
        self._upgradeCallback = partial(StoragePool._upgradePoolDomain,
                                        proxy(self))
        self._domainStateCallback = partial(
            StoragePool._domainStateChange, proxy(self))
        self._backend = None

        # The watchdog monitors the SPM lease on the master domain.
        self._watchdog = None

    def __is_secure__(self):
        return self.is_secure()

    @unsecured
    def is_secure(self):
        return self._secured.isSet()

    @unsecured
    def _set_secure(self):
        if not self.is_secure():
            self.log.info("Switching storage pool %s to SECURE mode",
                          self.spUUID)
        self._secured.set()

    @unsecured
    def _set_insecure(self):
        if self.is_secure():
            self.log.info("Switching storage pool %s to INSECURE mode",
                          self.spUUID)
        self._secured.clear()

    @unsecured
    def is_connected(self):
        return True

    @unsecured
    def getSpmStatus(self):
        return self._backend.getSpmStatus()

    @unsecured
    def validateSPM(self):
        if self.spmRole != SPM_ACQUIRED:
            raise se.SpmStatusError(self.spUUID)

    @unsecured
    def validateNotSPM(self):
        if self.spmRole != SPM_FREE:
            raise se.IsSpm(self.spUUID)

    @unsecured
    def setBackend(self, backend):
        self.log.info('updating pool %s backend from type %s instance 0x%x '
                      'to type %s instance 0x%x', self.spUUID,
                      type(self._backend).__name__, id(self._backend),
                      type(backend).__name__, id(backend))
        self._backend = backend

    @unsecured
    def getBackend(self):
        return self._backend

    @unsecured
    def _domainStateChange(self, sdUUID, isValid):
        if not isValid:
            return

        domain = sdCache.produce(sdUUID)
        with rm.acquireResource(sc.STORAGE, self.spUUID, rm.SHARED):
            if sdUUID not in self.getDomains(activeOnly=True):
                self.log.debug("Domain %s is not an active pool domain, "
                               "skipping domain links refresh",
                               sdUUID)
                return
            with rm.acquireResource(sc.STORAGE, sdUUID + "_repo",
                                    rm.EXCLUSIVE):
                self.log.debug("Refreshing domain links for %s", sdUUID)
                self._refreshDomainLinks(domain)

            with rm.acquireResource(sc.STORAGE, sdUUID, rm.EXCLUSIVE):
                # Only SPM should update the domain role
                try:
                    self._maybe_fix_domain_role(domain)
                except SecureError:
                    pass

    def _upgradePoolDomain(self, sdUUID, isValid):
        # This method is called everytime the onDomainStateChange
        # event is emitted, this event is emitted even when a domain goes
        # INVALID if this happens there is nothing for us to do no matter what
        # the domain is
        if not isValid:
            return

        domain = sdCache.produce(sdUUID)
        if sdUUID not in self._domainsToUpgrade:
            return

        self.log.debug("Preparing to upgrade domain %s", sdUUID)

        try:
            # Assumed that the domain can be attached only to one pool
            targetDomVersion = self.masterDomain.getVersion()
        except:
            self.log.error("Error while preparing domain `%s` upgrade", sdUUID,
                           exc_info=True)
            return

        with rm.acquireResource(sc.STORAGE, "upgrade_" + self.spUUID,
                                rm.SHARED):
            # This can never be the master
            # Non data domain should not be upgraded
            # The domain type can never be changed, therefore it can be
            # checked without acquiring the domain lock.
            domClass = domain.getDomainClass()
            if domClass != sd.DATA_DOMAIN:
                self.log.debug("Domain `%s` is not a data domain it is an "
                               "%s domain, not upgrading", sdUUID, domClass)
            else:
                if self._shouldUpgradeDomain(domain, targetDomVersion):
                    with rm.acquireResource(sc.STORAGE, sdUUID, rm.EXCLUSIVE):
                        if sdUUID not in self._domainsToUpgrade:
                            return

                        domain.invalidateMetadata()
                        if self._shouldUpgradeDomain(domain, targetDomVersion):
                            try:
                                self._convertDomain(domain,
                                                    str(targetDomVersion))
                            except:
                                self.log.warn("Could not upgrade domain `%s`",
                                              sdUUID, exc_info=True)
                                return
            try:
                self._domainsToUpgrade.remove(sdUUID)
            except ValueError:
                pass

            self._finalizePoolUpgradeIfNeeded()

    @unsecured
    def _shouldUpgradeDomain(self, domain, targetDomVersion):
        domVersion = domain.getVersion()
        if domVersion < targetDomVersion:
            return True

        if domVersion > targetDomVersion:
            self.log.critical("Found a domain with a more advanced"
                              " version then the master domain")
            return False

        # domVersion == targetDomVersion
        return False

    def _maybe_fix_domain_role(self, dom):
        if (dom.sdUUID != self.masterDomain.sdUUID and
                dom.getDomainRole() == sd.MASTER_DOMAIN):
            self.log.info("Fixing domain %s role to regular", dom.sdUUID)
            self._backend.setDomainRegularRole(dom)

    @unsecured
    def startSpm(self, prevID, prevLVER, maxHostID, expectedDomVersion=None):
        """
        Starts the SPM functionality.

        :param spUUID: The UUID of the storage pool you want to manage with the
                       SPM.
        :type spUUID: UUID
        :param prevID: obsolete
        :param prevLVER: obsolete
        :param maxHostID: The maximun ID of the host.?
        :type maxHostID: int

        .. note::
            if the SPM is already started the function will fail silently.

        :raises: :exc:`storage.exception.MiscOperationInProgress` if
                       called during an already running connection
                       attempt. (makes the fact that it fails silently
                       does not matter very much).
        """
        with self.lock:
            if self.spmRole == SPM_ACQUIRED:
                return True
            # Since we added the lock the following should NEVER happen
            if self.spmRole == SPM_CONTEND:
                raise se.MiscOperationInProgress("spm start %s" % self.spUUID)

            self.updateMonitoringThreads()
            masterDomVersion = self.getVersion()
            # If no specific domain version was specified use current master
            # domain version
            if expectedDomVersion is None:
                expectedDomVersion = masterDomVersion

            if masterDomVersion > expectedDomVersion:
                raise se.CurrentVersionTooAdvancedError(
                    self.masterDomain.sdUUID, curVer=masterDomVersion,
                    expVer=expectedDomVersion)

            try:
                oldlver, oldid = self._backend.getSpmStatus()
            except se.InspectNotSupportedError:
                self.log.info("cluster lock inspect isn't supported. "
                              "proceeding with startSpm()")
                oldlver = LVER_INVALID
            else:
                if int(oldlver) != int(prevLVER) or int(oldid) != int(prevID):
                    self.log.info("expected previd:%s lver:%s got request for "
                                  "previd:%s lver:%s" %
                                  (oldid, oldlver, prevID, prevLVER))

            self.spmRole = SPM_CONTEND

            try:
                # Forcing to acquire the host id (if it's not acquired already)
                self.masterDomain.acquireHostId(self.id)
                self.masterDomain.acquireClusterLock(self.id)
            except:
                self.spmRole = SPM_FREE
                raise

            self.log.debug("spm lock acquired successfully")

            try:
                self.lver = int(oldlver) + 1

                self._backend.setSpmStatus(self.lver, self.id,
                                           __securityOverride=True)

                # Upgrade the master domain now if needed
                # __securityOverride is added by the @secured decorator
                # pylint: disable=unexpected-keyword-arg
                self._upgradePool(expectedDomVersion, __securityOverride=True)

                self.masterDomain.mountMaster()
                self.masterDomain.createMasterTree()
                self.tasksDir = os.path.join(
                    self.poolPath,
                    sc.POOL_MASTER_DOMAIN,
                    sd.MASTER_FS_DIR,
                    sd.TASKS_DIR)

                try:
                    # Make sure backup domain is active
                    # __securityOverride is added by the @secured decorator
                    # pylint: disable=unexpected-keyword-arg
                    self.checkBackupDomain(__securityOverride=True)
                except Exception:
                    self.log.error("Backup domain validation failed",
                                   exc_info=True)

                self.taskMng.loadDumpedTasks(self.tasksDir)

                self.spmRole = SPM_ACQUIRED

                # Once this completes we are running as SPM.
                self._set_secure()

                self._start_watching_spm_lease(self.masterDomain)

                # Mailbox issues SPM commands, therefore we start it AFTER spm
                # commands are allowed to run to prevent a race between the
                # mailbox and the "self._set_secure() call"

                if self.masterDomain.supportsMailbox:
                    self.masterDomain.prepareMailbox()
                    inbox = self._master_volume_path("inbox")
                    outbox = self._master_volume_path("outbox")
                    self.spmMailer = mailbox.SPM_MailMonitor(
                        self, maxHostID, inbox, outbox)
                    self.spmMailer.start()
                    self.spmMailer.registerMessageType(
                        mailbox.EXTEND_CODE, partial(
                            mailbox.SPM_Extend_Message.processRequest, self))
                    self.log.debug("SPM mailbox ready for pool %s on master "
                                   "domain %s", self.spUUID,
                                   self.masterDomain.sdUUID)
                else:
                    self.spmMailer = None

                # Restore tasks is last because tasks are spm ops (spm has to
                # be started)
                self.taskMng.recoverDumpedTasks()

                self.log.debug("ended.")

            except Exception as e:
                self.log.error("Unexpected error", exc_info=True)
                self.log.error("failed: %s" % str(e))
                # __securityOverride is added by the @secured decorator
                # pylint: disable=unexpected-keyword-arg
                self.stopSpm(force=True, __securityOverride=True)
                raise

    @unsecured
    def _cancel_upgrade(self):
        """
        If upgrade threads already took a shared lock, this will block until
        all upgrade threads release the lock. If upgrade did not start yet,
        this will cancel the pending upgrade.
        """
        self.log.info("Canceling upgrade for domains %s",
                      self._domainsToUpgrade)

        with rm.acquireResource(sc.STORAGE, "upgrade_" + self.spUUID,
                                rm.EXCLUSIVE):
            try:
                self.domainMonitor.onDomainStateChange.unregister(
                    self._upgradeCallback)
            except KeyError:
                pass

            self._domainsToUpgrade = []

    @classmethod
    def cleanupMasterMount(cls):
        """
        Check whether there are any dangling master file systems still mounted
        and unmount them if found.
        """
        masters = os.path.join(sc.REPO_MOUNT_DIR,
                               sd.BLOCKSD_DIR, "*", sd.MASTER_FS_DIR)
        for master in glob(masters):
            if mount.isMounted(master):
                cls.log.info("Unmounting master %s", master)
                try:
                    blockSD.BlockStorageDomain.doUnmountMaster(master)
                except se.StorageDomainMasterUnmountError as e:
                    panic("Unmount master {} failed: {}".format(master, e))
            else:
                cls.log.debug("Master %s is not mounted, skipping", master)

    def stopSpm(self, force=False):
        with self.lock:
            if not force and self.spmRole == SPM_FREE:
                return True

            self._cancel_upgrade()

            self._stop_watching_spm_lease()

            self._set_insecure()

            try:
                self.cleanupMasterMount()
            except:
                panic("Error cleaning up master mount")

            if self.spmMailer:
                try:
                    self.spmMailer.stop()
                    if not self.spmMailer.wait(timeout=60):
                        raise RuntimeError("Timeout stopping SPM mail monitor")
                except:
                    panic("Error stopping SPM mail monitor")

            try:
                self._backend.setSpmStatus(spmId=SPM_ID_FREE,
                                           __securityOverride=True)
            except:
                # The system can handle this inconsistency.
                self.log.exception("Error updating SPM status")

            try:
                self.masterDomain.releaseClusterLock()
            except:
                panic("Error releasing cluster lock")

            self.spmRole = SPM_FREE

    def _upgradePool(self, targetDomVersion, lockTimeout=None):
        try:
            with rm.acquireResource(sc.STORAGE, "upgrade_" + self.spUUID,
                                    rm.EXCLUSIVE, timeout=lockTimeout):
                sd.StorageDomain.validate_version(targetDomVersion)
                # _upgradePool is executed during startSpm. Other operations
                # (like storage jobs such as copy_data/amend_image) that use
                # the same lock may be running. In case we'll try to upgrade
                # the master version we'll wait for those jobs to end and
                # release the lock.
                # Usually no upgrade is actually being performed, this check
                # verifies that a master domain upgrade is actually needed
                # before attempting to execute it (The domain version can be
                # only incremented) - note that the last phase of the upgrade
                # should be the version update.
                if self.masterDomain.getVersion() != targetDomVersion:
                    self.log.info("Trying to upgrade master domain `%s`",
                                  self.masterDomain.sdUUID)
                    with rm.acquireResource(sc.STORAGE,
                                            self.masterDomain.sdUUID,
                                            rm.EXCLUSIVE):
                        self._convertDomain(self.masterDomain,
                                            str(targetDomVersion))

                self.log.debug("Marking active domains for upgrade")
                domains = self.getDomains(activeOnly=True)
                domains.pop(self.masterDomain.sdUUID, None)
                self._domainsToUpgrade = list(domains)

                self.log.debug("Registering with state change event")
                self.domainMonitor.onDomainStateChange.register(
                    self._upgradeCallback)
                self.log.debug("Running initial domain upgrade threads")
                for sdUUID in self._domainsToUpgrade:
                    t = concurrent.thread(self._upgradeCallback,
                                          args=(sdUUID, True),
                                          kwargs={"__securityOverride": True},
                                          name="upgrade/" + sdUUID[:7],
                                          log=self.log)
                    t.start()
        except rm.RequestTimedOutError:
            raise se.PoolUpgradeInProgress(self.spUUID)

    @unsecured
    def __createMailboxMonitor(self):
        if self.hsmMailer:
            return

        if self.masterDomain.supportsMailbox:
            # NOTE: The SPM's inbox is the HSM's outbox and vice versa
            outbox = self._master_volume_path("inbox")
            inbox = self._master_volume_path("outbox")
            self.hsmMailer = mailbox.HSM_Mailbox(
                self.id, self.spUUID, inbox, outbox)
            self.log.debug("HSM mailbox ready for pool %s on master "
                           "domain %s", self.spUUID, self.masterDomain.sdUUID)

    @unsecured
    def __cleanupDomains(self, domlist, msdUUID, masterVersion):
        """
        Clean up domains after failed Storage Pool creation
        domlist - comma separated list of sdUUIDs
        """
        # Go through all the domains and detach them from the pool
        # Since something went wrong (otherwise why would we be cleaning
        # the mess up?) do not expect all the domains to exist
        for sdUUID in domlist:
            try:
                self.detachSD(sdUUID)
            except Exception:
                self.log.error("Domain %s detach from MSD %s Ver %s failed.",
                               sdUUID, msdUUID, masterVersion, exc_info=True)
        # Cleanup links to domains under /rhev/datacenter/poolName
        self.refresh(msdUUID, masterVersion)

    def _assert_sd_in_pool(self, sdUUID):
        if sdUUID not in self.getDomains():
            raise se.StorageDomainNotMemberOfPool(self.spUUID, sdUUID)
        return True

    def _assert_sd_owned_by_pool(self, dom):
        """
        Avoid handling domains if not owned by pool.
        """
        self._assert_sd_in_pool(dom.sdUUID)
        if self.spUUID not in dom.getPools():
            dom.invalidateMetadata()
            if self.spUUID not in dom.getPools():
                raise se.StorageDomainNotInPool(self.spUUID, dom.sdUUID)
        return True

    def _assert_sd_in_attached_state(self, sdUUID):
        domains = self.getDomains()
        if domains[sdUUID] != sd.DOM_ATTACHED_STATUS:
            raise se.StorageDomainIllegalStateError(
                sdUUID, sd.DOM_ATTACHED_STATUS, domains[sdUUID])

    @unsecured
    def _acquireTemporaryClusterLock(self, msdUUID, leaseParams):
        try:
            # Master domain is unattached and all changes to unattached domains
            # must be performed under storage lock
            msd = sdCache.produce(msdUUID)

            # As we are just creating the pool then the host doesn't have an
            # assigned id for this pool
            self.id = msd.getReservedId()

            msd.changeLeaseParams(leaseParams)

            msd.acquireHostId(self.id)

            try:
                msd.acquireClusterLock(self.id)
            except:
                msd.releaseHostId(self.id)
                raise
        except:
            self.id = SPM_ID_FREE
            raise

    @unsecured
    def _releaseTemporaryClusterLock(self, msdUUID):
        msd = sdCache.produce(msdUUID)
        try:
            msd.releaseClusterLock()
        finally:
            msd.releaseHostId(self.id)
        self.id = SPM_ID_FREE

    @unsecured
    def create(self, poolName, msdUUID, domList, masterVersion, leaseParams):
        """
        Create new storage pool with single/multiple image data domain.
        The command will create new storage pool meta-data attach each
        storage domain to that storage pool.
        At least one data (images) domain must be provided
         'poolName' - storage pool name
         'msdUUID' - master domain of this pool (one of domList)
         'domList' - list of domains (i.e sdUUID,sdUUID,...,sdUUID)
        """
        self.log.info("spUUID=%s poolName=%s master_sd=%s domList=%s "
                      "masterVersion=%s %s", self.spUUID, poolName, msdUUID,
                      domList, masterVersion, leaseParams)

        if msdUUID not in domList:
            raise se.InvalidParameterException("masterDomain", msdUUID)

        # Check the domains before pool creation
        domains = []
        msdVersion = None
        for sdUUID in domList:
            try:
                domain = sdCache.produce(sdUUID)
                domain.validate()
                if sdUUID == msdUUID:
                    msd = domain
                    msdVersion = msd.getVersion()
            except se.StorageException:
                self.log.error("Unexpected error", exc_info=True)
                raise se.StorageDomainAccessError(sdUUID)

            # Validate unattached domains
            if not domain.isISO():
                domain.invalidateMetadata()
                spUUIDs = domain.getPools()
                # Non ISO domains have only 1 pool
                if len(spUUIDs) > 0:
                    raise se.StorageDomainAlreadyAttached(spUUIDs[0], sdUUID)
            domains.append(domain)

        for domain in domains:
            if domain.isData() and (domain.getVersion() != msdVersion):
                raise se.MixedSDVersionError(domain.sdUUID,
                                             domain.getVersion(), msd.sdUUID,
                                             msdVersion)

        self.log.info("Creating pool directory %r", self.poolPath)
        fileUtils.createdir(self.poolPath)
        self._acquireTemporaryClusterLock(msdUUID, leaseParams)
        try:
            self._set_secure()
            try:
                # Mark 'master' domain.  We should do it before actually
                # attaching this domain to the pool During 'master' marking we
                # create pool metadata and each attached domain should register
                # there.
                self.createMaster(poolName, msd, masterVersion, leaseParams)
                self.__rebuild(msdUUID=msdUUID, masterVersion=masterVersion)
                # Attach storage domains to the storage pool.  Since we are
                # creating the pool then attach is done from the hsm and not
                # the spm therefore we must manually take the master domain
                # lock.
                # TBD: create will receive only master domain and further
                #      attaches should be done under SPM.

                # Master domain was already attached (in createMaster), no need
                # to reattach.
                for sdUUID in domList:
                    # No need to attach the master
                    if sdUUID != msdUUID:
                        self.attachSD(sdUUID)
            except Exception:
                self.log.error("Create pool %s canceled ", poolName,
                               exc_info=True)
                self.log.info("Removing pool directory %r", self.poolPath)
                try:
                    fileUtils.cleanupdir(self.poolPath)
                    self.__cleanupDomains(domList, msdUUID, masterVersion)
                except:
                    self.log.error("Cleanup failed due to an unexpected error",
                                   exc_info=True)
                raise
            finally:
                self._set_insecure()
        finally:
            self._releaseTemporaryClusterLock(msdUUID)
            self.stopMonitoringDomains()

        return True

    @unsecured
    def connect(self, hostID, msdUUID, masterVersion):
        """
        Connect a Host to a specific storage pool.

        Caller must acquire resource Storage.spUUID so that this method would
        never be called twice concurrently.
        """
        self.log.info("Connect host #%s to the storage pool %s with master "
                      "domain: %s (ver = %s)" %
                      (hostID, self.spUUID, msdUUID, masterVersion))

        self.id = hostID
        # Make sure SDCache doesn't have stale data (it can be in case of FC)
        sdCache.invalidateStorage()
        sdCache.refresh()
        # Since we start the monitor threads during the call to __rebuild,
        # we should start watching the domains states right before we call
        # __rebuild.
        self._startWatchingDomainsState()
        try:
            # Rebuild whole Pool
            self.__rebuild(msdUUID=msdUUID, masterVersion=masterVersion)
            self.__createMailboxMonitor()
        except Exception:
            self._stopWatchingDomainsState()
            raise

        return True

    @unsecured
    def _startWatchingDomainsState(self):
        self.log.debug("Start watching domains state")
        self.domainMonitor.onDomainStateChange.register(
            self._domainStateCallback)

    @unsecured
    def _stopWatchingDomainsState(self):
        self.log.debug("Stop watching domains state")
        try:
            self.domainMonitor.onDomainStateChange.unregister(
                self._domainStateCallback)
        except KeyError:
            self.log.warning("Domain state callback is not registered")

    @unsecured
    def stopMonitoringDomains(self):
        self.domainMonitor.stopMonitoring(self.domainMonitor.poolDomains)
        return True

    @unsecured
    def disconnect(self):
        """
        Disconnect a Host from specific storage pool.

        Caller must acquire resource Storage.spUUID so that this method would
        never be called twice concurrently.
        """
        self.log.info("Disconnect from the storage pool %s", self.spUUID)

        self.id = SPM_ID_FREE

        if self.hsmMailer:
            self.hsmMailer.stop()
            self.hsmMailer = None

        # Remove all links
        if os.path.exists(self.poolPath):
            self.log.info("Removing pool directory %r", self.poolPath)
            fileUtils.cleanupdir(self.poolPath)

        self.stopMonitoringDomains()
        self._stopWatchingDomainsState()
        return True

    @unsecured
    def createMaster(self, poolName, domain, masterVersion, leaseParams):
        """
        Create a fresh master file system directory tree
        """
        # THIS METHOD MUST BE RUN UNDER DOMAIN STORAGE LOCK
        self.log.info("setting master domain for spUUID %s on sdUUID=%s",
                      self.spUUID, domain.sdUUID)

        if not misc.isAscii(poolName) and not domain.supportsUnicode():
            raise se.UnicodeArgumentException()

        self._backend.initParameters(poolName, domain, masterVersion)
        domain.initMaster(self.spUUID, leaseParams)

    @unsecured
    def reconstructMaster(self, hostId, poolName, msdUUID, domDict,
                          masterVersion, leaseParams):
        self.log.info("spUUID=%s hostId=%s poolName=%s msdUUID=%s domDict=%s "
                      "masterVersion=%s leaseparams=(%s)", self.spUUID, hostId,
                      poolName, msdUUID, domDict, masterVersion, leaseParams)

        if msdUUID not in domDict:
            raise se.InvalidParameterException("masterDomain", msdUUID)

        futureMaster = sdCache.produce(msdUUID)

        # Forcing to acquire the host id (if it's not acquired already).
        futureMaster.acquireHostId(hostId)
        futureMaster.acquireClusterLock(hostId)

        # The host id must be set for createMaster(...).
        self.id = hostId

        try:
            # As in the create method we need to temporarily set the object
            # secure in order to change the domains map.
            # TODO: it is clear that reconstructMaster and create (StoragePool)
            # are extremely similar and they should be unified.
            self._set_secure()
            try:
                self.createMaster(poolName, futureMaster, masterVersion,
                                  leaseParams)
                self.setMasterDomain(msdUUID, masterVersion)

                for sdUUID in domDict:
                    domDict[sdUUID] = domDict[sdUUID].capitalize()

                # Add domain to domain list in pool metadata.
                self.log.info("Set storage pool domains: %s", domDict)
                self._backend.setDomainsMap(domDict)

                self.refresh(msdUUID=msdUUID, masterVersion=masterVersion)
            finally:
                self._set_insecure()
        finally:
            futureMaster.releaseClusterLock()

    def _copyLeaseParameters(self, srcDomain, dstDomain):
        leaseParams = srcDomain.getLeaseParams()
        self.log.info("Updating lease parameters for domain %s to %s",
                      srcDomain.sdUUID, leaseParams)
        dstDomain.changeLeaseParams(leaseParams)

    def masterMigrate(self, sdUUID, msdUUID, masterVersion):
        self.log.info(
            "Storage pool %s migrating master from %s to %s with "
            "version %s",
            self.spUUID, sdUUID, msdUUID, masterVersion)

        # TODO: is this check still relevant?
        # Make sure the masterVersion higher than that of the pool
        if not masterVersion > self._backend.getMasterVersion():
            raise se.StoragePoolWrongMaster(self.spUUID,
                                            self.masterDomain.sdUUID)

        curmsd = sdCache.produce(sdUUID)
        newmsd = sdCache.produce(msdUUID)
        self._refreshDomainLinks(newmsd)
        curmsd.invalidateMetadata()

        # new 'master' should be in 'active' status
        domList = self.getDomains()
        if msdUUID not in domList:
            raise se.StorageDomainNotInPool(self.spUUID, msdUUID)
        if msdUUID == sdUUID:
            raise se.InvalidParameterException("msdUUID", msdUUID)
        if domList[msdUUID] != sd.DOM_ACTIVE_STATUS:
            raise se.StorageDomainNotActive(msdUUID)

        self._convertDomain(newmsd, curmsd.getFormat())
        self._copyLeaseParameters(curmsd, newmsd)

        if newmsd.isISO():
            raise se.IsoCannotBeMasterDomain(msdUUID)
        if newmsd.isBackup():
            raise se.BackupCannotBeMasterDomain(msdUUID)
        if not newmsd.getMDPath():
            raise se.StorageDomainLayoutError("domain", msdUUID)

        # If the new master domain is using safelease (version < 3) then
        # we can speed up the cluster lock acquirement by resetting the
        # SPM lease.
        # XXX: With SANLock there is no need to speed up the process (the
        # acquirement will take a short time anyway since we already hold
        # the host id) and more importantly resetting the lease is going
        # to interfere with the regular SANLock behavior.
        # @deprecated this is relevant only for domain version < 3
        if not newmsd.hasVolumeLeases():
            newmsd.initSPMlease()

        # Forcing to acquire the host id (if it's not acquired already)
        # and acquiring the cluster lock on new master.
        newmsd.acquireHostId(self.id)
        newmsd.acquireClusterLock(self.id)

        try:
            self.log.debug("Migration to new master %s starting",
                           newmsd.sdUUID)

            # Preparing the mailbox since the new master domain may be an
            # old domain where the mailbox wasn't allocated
            newmsd.prepareMailbox()

            # Mount new master file system
            newmsd.mountMaster()

            # Make sure there is no cruft left over
            for dir in [newmsd.getVMsDir(), newmsd.getTasksDir()]:
                self.log.info("Removing directory %r", dir)
                fileUtils.cleanupdir(dir)

            # Copy master file system content to the new master
            fileUtils.tarCopy(
                os.path.join(curmsd.domaindir, sd.MASTER_FS_DIR),
                os.path.join(newmsd.domaindir, sd.MASTER_FS_DIR),
                exclude=('./lost+found',))

            # There's no way to ensure that we only have one domain marked
            # as master in the storage pool (e.g. after a reconstructMaster,
            # or even in this method if we fail to set the old master to
            # regular). That said, for API cleaness switchMasterDomain is
            # the last method to call as "point of no return" after which we
            # only try to cleanup but we cannot rollback.
            newmsd.changeRole(sd.MASTER_DOMAIN)
            self._backend.switchMasterDomain(curmsd, newmsd, masterVersion)
        except Exception:
            self.log.exception("Migration to new master %s failed",
                               newmsd.sdUUID)
            try:
                self._backend.setDomainRegularRole(newmsd)
            except Exception:
                self.log.exception("Unable to mark domain %s as regular",
                                   newmsd.sdUUID)

            # Do not release the cluster lock if unmount fails. The lock
            # will prevent other hosts from mounting the master filesystem
            # (avoiding corruptions).
            newmsd.unmountMaster()
            newmsd.releaseClusterLock()
            raise

        # At this point both the old and new master acquired the SPM lease.
        # Before we release the SPM lease on the old master, we need to stop
        # watching the old master SPM lease, and start watching the new master
        # SPM lease.
        self._stop_watching_spm_lease()
        self._start_watching_spm_lease(newmsd)

        # From this point on we have a new master and should not fail
        try:
            self.log.debug(
                "Migration to new master %s succeeded, refreshing pool",
                newmsd.sdUUID)
            self.refresh(msdUUID, masterVersion)

            # From this point on there is a new master domain in the pool
            # Now that we are beyond the critical point we can clean up
            # things
            self._backend.setDomainRegularRole(curmsd)

            # Clean up the old data from previous master fs
            for directory in [curmsd.getVMsDir(), curmsd.getTasksDir()]:
                self.log.info("Removing directory %r", directory)
                fileUtils.cleanupdir(directory)
        except Exception:
            self.log.exception(
                "Ignoring old master %s cleanup failure", curmsd.sdUUID)
        finally:
            try:
                # Unmounting the old master filesystem and releasing the
                # old cluster lock. Do not release the cluster lock if
                # unmount fails. The lock will prevent other hosts from
                # mounting the master filesystem (avoiding corruptions).
                curmsd.unmountMaster()
                curmsd.releaseClusterLock()
            except Exception:
                self.log.exception(
                    "Ignoring old master %s unmount and release failures",
                    curmsd.sdUUID)

    def switchMaster(self, oldMasterUUID, newMasterUUID, masterVersion):
        """
        Switches the master domain from oldMasterUUID to newMasterUUID.
        """
        locks = [
            rm.Lock(sc.STORAGE, oldMasterUUID, rm.EXCLUSIVE),
            rm.Lock(sc.STORAGE, newMasterUUID, rm.EXCLUSIVE)
        ]
        self.log.info("Taking domains locks for switching master")
        with guarded.context(locks):
            self.masterMigrate(oldMasterUUID, newMasterUUID, masterVersion)

    def attachSD(self, sdUUID):
        """
        Attach a storage domain to the storage pool.
        This marks the storage domain as "attached" and links it
        to the storage pool

        The storage domain may be the hosted engine storage domain, which is
        being monitored but does not belong to the pool yet.

         'sdUUID' - storage domain UUID
        """
        self.log.info("sdUUID=%s spUUID=%s", sdUUID, self.spUUID)

        domains = self.getDomains()
        if len(domains) >= self._backend.getMaximumSupportedDomains():
            raise se.TooManyDomainsInStoragePoolError()

        try:
            dom = sdCache.produce(sdUUID)
        except se.StorageDomainDoesNotExist:
            sdCache.invalidateStorage()
            dom = sdCache.produce(sdUUID)

        try:
            self._assert_sd_owned_by_pool(dom)
        except (se.StorageDomainNotMemberOfPool, se.StorageDomainNotInPool):
            pass  # domain is not attached to this pool yet
        else:
            self.log.warning('domain %s is already attached to pool %s',
                             sdUUID, self.spUUID)
            return

        # When attaching a the hosted engine storage domain, the domain host id
        # is already acquired, and must not be released.
        domainHasHostId = dom.hasHostId(self.id)

        if domainHasHostId:
            self.log.info("Domain %s has host id %s, attaching live domain",
                          sdUUID, self.id)
        else:
            dom.acquireHostId(self.id)

        try:
            dom.acquireClusterLock(self.id)

            try:
                domVers = dom.getVersion()
                mstVers = self.masterDomain.getVersion()

                # If you remove this condition, remove it from
                # public_createStoragePool too.
                if dom.isData() and domVers > mstVers:
                    raise se.MixedSDVersionError(dom.sdUUID, domVers,
                                                 self.masterDomain.sdUUID,
                                                 mstVers)

                dom.attach(self.spUUID)
                domains[sdUUID] = sd.DOM_ATTACHED_STATUS
                self._backend.setDomainsMap(domains)
                self._refreshDomainLinks(dom)
            finally:
                dom.releaseClusterLock()

        finally:
            # If the domain has a host id, or we are monitoring this domain,
            # we must not release the host id, as it will kill any process
            # holding a resource on this domain, such as the qemu process
            # running the hosted engine vm.
            if domainHasHostId:
                self.log.debug("Domain %s has host id %s, leaving the "
                               "host id acquired", sdUUID, self.id)
            elif self.domainMonitor.isMonitoring(sdUUID):
                self.log.debug("Domain %s is being monitored, leaving host "
                               "id %s acquired", sdUUID, self.id)
            else:
                dom.releaseHostId(self.id)

        self.updateMonitoringThreads()

    def forcedDetachSD(self, sdUUID):
        self.log.warn("Force detaching domain `%s`", sdUUID)
        domains = self.getDomains()

        if sdUUID not in domains:
            return True

        del domains[sdUUID]

        self._backend.setDomainsMap(domains)
        self._cleanupDomainLinks(sdUUID)

        # If the domain that we are detaching is the master domain
        # we attempt to stop the SPM before releasing the host id
        if self.masterDomain.sdUUID == sdUUID:
            self.stopSpm()

        self.updateMonitoringThreads()
        self.log.debug("Force detach for domain `%s` is done", sdUUID)

    def detachSD(self, sdUUID):
        """
        Detach a storage domain from a storage pool.
        This removes the storage domain entry in the storage pool meta-data
        and leaves the storage domain in 'unattached' status.
         'sdUUID' - storage domain UUID
        """

        self.log.info("sdUUID=%s spUUID=%s", sdUUID, self.spUUID)

        dom = sdCache.produce(sdUUID)

        # To detach SD, it has to be put into maintenance, which stops
        # monitoring thread. Producing SD will call its constructor, which
        # in case of block SD, will activate special LVs, but these won't be
        # deactivate by monitoring thread as it was already stopped. Tear the
        # domain down once detach is finished.
        # TODO: remove once SD constructor is refactored so it doesn't do any
        # actions on the storage, like activating LVs.

        with dom.tearing_down():
            # Avoid detach domains which were not put into maintenance first.
            self._assert_sd_in_attached_state(sdUUID)

            # Avoid detach domains if not owned by pool
            self._assert_sd_owned_by_pool(dom)

            if sdUUID == self.masterDomain.sdUUID:
                raise se.CannotDetachMasterStorageDomain(sdUUID)

            # TODO: clusterLock protection should be moved to
            #       StorageDomain.[at,de]tach()
            detachingISO = dom.isISO()

            if detachingISO:
                # An ISO domain can be shared by multiple pools
                dom.acquireHostId(self.id)
                dom.acquireClusterLock(self.id)

            try:
                # Remove pool info from domain metadata
                dom.detach(self.spUUID)
            finally:
                if detachingISO:
                    dom.releaseClusterLock()
                    dom.releaseHostId(self.id)

            # Remove domain from pool metadata
            self.forcedDetachSD(sdUUID)

    def detachAllDomains(self):
        """
        Detach all domains from pool before destroying pool

        Assumed cluster lock and that SPM is already stopped.
        """
        # Find regular (i.e. not master) domains from the pool metadata
        regularDoms = tuple(sdUUID for sdUUID in self.getDomains()
                            if sdUUID != self.masterDomain.sdUUID)
        # The Master domain should be detached last
        for sdUUID in regularDoms:
            self.detachSD(sdUUID)

        # Forced detach master domain
        self.forcedDetachSD(self.masterDomain.sdUUID)
        self.masterDomain.detach(self.spUUID)

    @unsecured
    def _convertDomain(self, domain, targetFormat=None):
        # Remember to get the sdUUID before upgrading because the object is
        # broken after the upgrade
        sdUUID = domain.sdUUID
        isMsd = (self.masterDomain.sdUUID == sdUUID)

        if targetFormat is None:
            targetFormat = self.getFormat()

        try:
            self._formatConverter.convert(
                self.poolPath, self.id, domain.getRealDomain(), isMsd,
                targetFormat)
        finally:
            # For safety we remove the domain from the cache also if the
            # conversion supposedly failed.
            sdCache.manuallyRemoveDomain(sdUUID)
            sdCache.produce(sdUUID)

    @unsecured
    def getFormat(self):
        return str(self.getVersion())

    def activateSD(self, sdUUID):
        """
        Activate a storage domain that is already a member in a storage pool.
        Validate that the storage domain is owned by the storage pool.
         'sdUUID' - storage domain UUID
        """
        self.log.info("sdUUID=%s spUUID=%s", sdUUID, self.spUUID)

        dom = sdCache.produce(sdUUID)
        # Avoid domain activation if not owned by pool
        self._assert_sd_owned_by_pool(dom)

        # Do nothing if already active
        domainStatuses = self.getDomains()
        if domainStatuses[sdUUID] == sd.DOM_ACTIVE_STATUS:
            return True

        # Domain conversion requires the links to be present
        self._refreshDomainLinks(dom)
        self._backend.setDomainRegularRole(dom)

        if dom.getDomainClass() == sd.DATA_DOMAIN:
            self._convertDomain(dom)

        dom.activate()
        # set domains also do rebuild
        domainStatuses[sdUUID] = sd.DOM_ACTIVE_STATUS
        self._backend.setDomainsMap(domainStatuses)
        self.updateMonitoringThreads()
        return True

    def deactivateSD(self, sdUUID, newMsdUUID, masterVersion):
        """
        Deactivate a storage domain.
        Validate that the storage domain is owned by the storage pool.
        Change storage domain status to "Attached" in the storage pool
        meta-data.

        :param sdUUID: The UUID of the storage domain you want to deactivate.
        :param newMsdUUID: The UUID of the new master storage domain.
        :param masterVersion: new master storage domain version
        """

        self._assert_sd_in_pool(sdUUID)
        self.log.info("sdUUID=%s spUUID=%s newMsdUUID=%s", sdUUID, self.spUUID,
                      newMsdUUID)
        domList = self.getDomains()

        if sdUUID not in domList:
            raise se.StorageDomainNotInPool(self.spUUID, sdUUID)

        try:
            dom = sdCache.produce(sdUUID)
        except (se.StorageException, AttributeError):
            # AttributeError: Unreloadable blockSD
            self.log.warn("deactivating missing domain %s", sdUUID,
                          exc_info=True)
            if newMsdUUID != sd.BLANK_UUID:
                # Trying to migrate master failed to reach actual msd
                raise se.StorageDomainAccessError(sdUUID)

        else:
            if self.masterDomain.sdUUID == sdUUID:
                if newMsdUUID == sd.BLANK_UUID:
                    # TODO: For backward compatibility VDSM is silently
                    #       ignoring the deactivation request for the master
                    #       domain. Remove this check as soon as possible.
                    self.log.info("Silently ignoring the deactivation request "
                                  "for the master domain %s", sdUUID)
                    return
                else:
                    self.masterMigrate(sdUUID, newMsdUUID, masterVersion)
            else:
                masterDir = os.path.join(dom.domaindir, sd.MASTER_FS_DIR)
                try:
                    m = mount.getMountFromTarget(masterDir)
                except OSError as e:
                    if e.errno == errno.ENOENT:
                        pass  # Master is not mounted
                    else:
                        raise
                else:
                    try:
                        m.umount()
                    except mount.MountError:
                        self.log.error("Can't umount masterDir %s for domain "
                                       "%s", masterDir, dom)

        domList[sdUUID] = sd.DOM_ATTACHED_STATUS
        self._backend.setDomainsMap(domList)
        self.updateMonitoringThreads()
        try:
            self._domainsToUpgrade.remove(sdUUID)
        except ValueError:
            return

        self._finalizePoolUpgradeIfNeeded()

    @unsecured
    def _finalizePoolUpgradeIfNeeded(self):
        if len(self._domainsToUpgrade) == 0:
            self.log.debug("No domains left for upgrade, unregistering "
                           "from state change event")
            try:
                self.domainMonitor.onDomainStateChange.unregister(
                    self._upgradeCallback)
            except KeyError:
                pass

    @unsecured
    def _linkStorageDomain(self, linkTarget, linkName):
        self.log.info("Linking %s to %s", linkTarget, linkName)
        try:
            currentLinkTarget = os.readlink(linkName)
        except OSError as e:
            if e.errno != errno.ENOENT:
                self.log.error("Can't link SD %s to %s", linkTarget, linkName,
                               exc_info=True)
                return
        else:
            if currentLinkTarget == linkTarget:
                self.log.debug('link already present skipping creation '
                               'for %s', linkName)
                return  # Nothing to do
        # Rebuild the link
        tmp_link_name = os.path.join(sc.REPO_DATA_CENTER,
                                     str(uuid.uuid4()))
        os.symlink(linkTarget, tmp_link_name)  # make tmp_link
        self.log.info("Creating symlink from %s to %s", linkTarget, linkName)
        os.rename(tmp_link_name, linkName)

    @unsecured
    def _cleanupDomainLinks(self, domain):
        linkPath = os.path.join(self.poolPath, domain)
        self.log.info("Removing: %s", linkPath)
        try:
            os.remove(linkPath)
        except (OSError, IOError):
            pass

    @unsecured
    def _refreshDomainLinks(self, domain):
        domain.refreshDirTree()
        linkName = os.path.join(self.poolPath, domain.sdUUID)
        self._linkStorageDomain(domain.domaindir, linkName)
        if self.masterDomain.sdUUID == domain.sdUUID:
            masterName = os.path.join(self.poolPath, sc.POOL_MASTER_DOMAIN)
            self._linkStorageDomain(domain.domaindir, masterName)

    @unsecured
    def __rebuild(self, msdUUID, masterVersion):
        """
        Rebuild storage pool.
        """
        # master domain must be refreshed first
        self.setMasterDomain(msdUUID, masterVersion)
        self.log.info("Creating pool directory %r", self.poolPath)
        fileUtils.createdir(self.poolPath)

        # Find out all domains for future cleanup
        domainpat = os.path.join(self.poolPath, sc.UUID_GLOB_PATTERN)
        oldLinks = set(iglob(domainpat))

        # We should not rebuild non-active domains, because
        # they are probably disconnected from the host
        domUUIDs = list(self.getDomains(activeOnly=True))

        # msdUUID should be present and active in getDomains result.
        try:
            domUUIDs.remove(msdUUID)
        except ValueError:
            self.log.error('master storage domain %s not found in the pool '
                           'domains or not active', msdUUID)
            raise se.StoragePoolWrongMaster(self.spUUID, msdUUID)

        # TODO: Consider to remove this whole block. UGLY!
        # We want to avoid looking up (vgs) of unknown block domains.
        # domUUIDs includes all the domains, file or block.
        block_mountpoint = os.path.join(sc.REPO_MOUNT_DIR, sd.BLOCKSD_DIR)
        blockDomUUIDs = [vg.name for vg in blockSD.lvm.getVGs(domUUIDs)]
        domDirs = {}  # {domUUID: domaindir}
        # Add the block domains
        for domUUID in blockDomUUIDs:
            domaindir = os.path.join(block_mountpoint, domUUID)
            domDirs[domUUID] = domaindir
            # create domain special volumes folder
            md_dir = os.path.join(domaindir, sd.DOMAIN_META_DATA)
            self.log.info("Creating domain metadata directory %r", md_dir)
            fileUtils.createdir(md_dir)
            images_dir = os.path.join(domaindir, sd.DOMAIN_IMAGES)
            self.log.info("Creating domain images directory %r", images_dir)
            fileUtils.createdir(images_dir)
        # Add the file domains
        for domUUID, domaindir in fileSD.scanDomains():
            if domUUID in domUUIDs:
                domDirs[domUUID] = domaindir

        # Link all the domains to the pool
        for domUUID, domaindir in six.iteritems(domDirs):
            linkName = os.path.join(self.poolPath, domUUID)
            self._linkStorageDomain(domaindir, linkName)
            oldLinks.discard(linkName)

        # Always try to build master links
        try:
            self._refreshDomainLinks(self.masterDomain)
        except (se.StorageException, OSError):
            self.log.error("_refreshDomainLinks failed for master domain %s",
                           self.masterDomain.sdUUID, exc_info=True)
        linkName = os.path.join(self.poolPath, self.masterDomain.sdUUID)
        oldLinks.discard(linkName)

        # Cleanup old trash from the pool
        for oldie in oldLinks:
            self.log.info('collecting stale storage domain link %s', oldie)
            try:
                os.remove(oldie)
            except OSError as e:
                if e.errno != errno.ENOENT:
                    self.log.warn("Could not clean all trash from the pool dom"
                                  " `%s` (%s)", oldie, e)
            except Exception as e:
                self.log.warn("Could not clean all trash from the pool dom"
                              " `%s` (%s)", oldie, e)

    @unsecured
    def refresh(self, msdUUID, masterVersion):
        """
        Refresh storage pool.
         'msdUUID' - master storage domain UUID
        """
        sdCache.invalidateStorage()
        sdCache.refresh()
        self.__rebuild(msdUUID=msdUUID, masterVersion=masterVersion)

    def updateVM(self, vmList, sdUUID):
        """
        Update VMs.
         'vmList' - [{'vm':vmUUID,'ovf','imglist':'imgUUID1,imgUUID2,...'},...]
         'sdUUID' - target domain UUID, if not None, VM Images and the master
                    tree must be located on this domain.
                    If sdUUID is None, the update is on the pool, and therefore
                    the master domain will be updated.
        """
        self._assert_sd_in_pool(sdUUID)
        self.log.info("spUUID=%s sdUUID=%s", self.spUUID, sdUUID)
        vms = self._getVMsPath(sdUUID)
        # We should exclude 'masterd' link from IMG_METAPATTERN globing
        vmUUID = ovf = imgList = ''
        for vm in vmList:
            if not vm:
                continue
            try:
                vmUUID = vm['vm']
                ovf = vm['ovf']
                imgList = vm['imglist'].split(',')
                self.log.info("vmUUID=%s imgList=%s", vmUUID, str(imgList))
            except KeyError:
                raise se.InvalidParameterException("vmList", str(vmList))

            vmPath = os.path.join(vms, vmUUID)
            if fileUtils.pathExists(vmPath):
                self.log.info("Removing VM directory %r", vmPath)
                try:
                    fileUtils.cleanupdir(vmPath, ignoreErrors=False)
                except RuntimeError as e:
                    raise se.MiscDirCleanupFailure(str(e))

            self.log.info("Creating VM directory %r", vmPath)
            try:
                fileUtils.createdir(vmPath)
                codecs.open(os.path.join(vmPath, vmUUID + '.ovf'), 'w',
                            encoding='utf8').write(ovf)
            except OSError as ex:
                if ex.errno == errno.ENOSPC:
                    raise se.NoSpaceLeftOnDomain(sdUUID)

                raise

    def removeVM(self, vmUUID, sdUUID):
        """
        Remove VM.
         'vmUUID' - Virtual machine UUID
        """
        self._assert_sd_in_pool(sdUUID)
        self.log.info("spUUID=%s vmUUID=%s sdUUID=%s", self.spUUID, vmUUID,
                      sdUUID)
        vms = self._getVMsPath(sdUUID)
        if os.path.exists(os.path.join(vms, vmUUID)):
            vmDirPath = os.path.join(vms, vmUUID)
            self.log.info("Removing VM directory %r", vmDirPath)
            fileUtils.cleanupdir(vmDirPath)

    def extendVolume(self, sdUUID, volumeUUID, size):
        # This method is not exposed through the remote API but it's called
        # directly from the mailbox to implement the thin provisioning on
        # block devices. The scope of this method is to extend only the
        # volume apparent size; the virtual disk size seen by the guest is
        # unchanged.
        self._assert_sd_in_pool(sdUUID)

        # Extend volume without refreshing its size. If the SPM host see the
        # new size immediately after extension, this can cause data corruption
        # during VM migration when the source host is SPM. Volume size will be
        # refreshed in Vm._after_volume_extension(), which is a callback of
        # disk extend command.
        # For more details see https://bugzilla.redhat.com/1983882
        sdCache.produce(sdUUID).extendVolume(volumeUUID, size, refresh=False)

    def reduceVolume(self, sdUUID, imgUUID, volUUID, allowActive=False):
        self._assert_sd_in_pool(sdUUID)
        dom = sdCache.produce(sdUUID)
        dom.reduceVolume(imgUUID, volUUID, allowActive=allowActive)

    def extendVolumeSize(self, sdUUID, imgUUID, volUUID, new_capacity):
        img_ns = rm.getNamespace(sc.IMAGE_NAMESPACE, sdUUID)
        with rm.acquireResource(img_ns, imgUUID, rm.EXCLUSIVE):
            vol = sdCache.produce(sdUUID).produceVolume(imgUUID, volUUID)
            return vol.extendSize(int(new_capacity))

    @unsecured
    def getVersion(self):
        return self.masterDomain.getVersion()

    @unsecured
    def getInfo(self):
        """
        Get storage pool info.
        """
        try:
            msdInfo = self.masterDomain.getInfo()
        except Exception:
            self.log.error("Couldn't read from master domain", exc_info=True)
            raise se.StoragePoolMasterNotFound(self.spUUID,
                                               self.masterDomain.sdUUID)

        poolInfo = {
            'domains': '',
            'isoprefix': '',
            'lver': LVER_INVALID,
            'master_uuid': self.masterDomain.sdUUID,
            'master_ver': self._backend.getMasterVersion(),
            'name': '',
            'pool_status': 'connected',
            'spm_id': SPM_ID_FREE,
            'type': msdInfo['type'],
            'version': str(msdInfo['version']),
        }

        poolInfo.update(self._backend.getInfo())

        return poolInfo

    @unsecured
    def getIsoDomain(self):
        """
        Get pool's ISO domain if active
        """
        domDict = self.getDomains(activeOnly=True)
        for item in domDict:
            try:
                dom = sdCache.produce(item)
            except se.StorageDomainDoesNotExist:
                self.log.warn("Storage domain %s does not exist", item)
                continue

            if dom.isISO():
                return dom
        return None

    @unsecured
    def setMasterDomain(self, msdUUID, masterVersion):
        """
        Get the (verified) master domain of this pool.

        'msdUUID' - expected master domain UUID.
        'masterVersion' - expected pool msd version.
        """
        try:
            domain = sdCache.produce(msdUUID)
        except se.StorageDomainDoesNotExist:
            # Manager should start reconstructMaster if SPM.
            raise se.StoragePoolMasterNotFound(self.spUUID, msdUUID)

        if not domain.isMaster():
            self.log.error("Requested master domain %s is not a master domain "
                           "at all", msdUUID)
            raise se.StoragePoolWrongMaster(self.spUUID, msdUUID)

        pools = domain.getPools()
        if (self.spUUID not in pools):
            self.log.error("Requested master domain %s does not belong to pool"
                           " %s", msdUUID, self.spUUID)
            raise se.StoragePoolWrongMaster(self.spUUID, msdUUID)

        self._backend.validateMasterDomainVersion(domain, masterVersion)
        self.log.debug("Master domain %s verified, version %s", msdUUID,
                       masterVersion)

        self.masterDomain = domain
        self.updateMonitoringThreads()

    @unsecured
    @misc.samplingmethod
    def updateMonitoringThreads(self):
        # domain list it's list of sdUUID:status
        # sdUUID1:status1,sdUUID2:status2,...
        activeDomains = frozenset(self.getDomains(activeOnly=True))
        monitoredDomains = frozenset(self.domainMonitor.poolDomains)

        monitorsToStop = monitoredDomains - activeDomains
        self.domainMonitor.stopMonitoring(monitorsToStop)

        monitorsToStart = activeDomains - monitoredDomains
        for sdUUID in monitorsToStart:
            self.domainMonitor.startMonitoring(sdUUID, self.id)

    @unsecured
    def getDomains(self, activeOnly=False):
        return dict((sdUUID, status) for sdUUID, status
                    in six.iteritems(self._backend.getDomainsMap())
                    if not activeOnly or status == sd.DOM_ACTIVE_STATUS)

    def checkBackupDomain(self):
        domDict = self.getDomains(activeOnly=True)
        for sdUUID in domDict:
            dom = sdCache.produce(sdUUID)
            if dom.isBackup():
                dom.mountMaster()
                # Master tree should be exist in this point
                # Recreate it if not.
                dom.createMasterTree()

    @unsecured
    def isActive(self, sdUUID):
        return sdUUID in self.getDomains(activeOnly=True)

    # TODO : move to sd.py
    @unsecured
    def _getVMsPath(self, sdUUID):
        """
        Return VMs dir within SD with sdUUID.
        """
        if not self.isActive(sdUUID):
            raise se.StorageDomainNotActive(sdUUID)
        vmPath = sdCache.produce(sdUUID).getVMsDir()
        # Get VMs path from the pool (from the master domain)

        if not os.path.exists(vmPath):
            raise se.VMPathNotExists(vmPath)
        return vmPath

    def copyImage(self, sdUUID, vmUUID, srcImgUUID, srcVolUUID, dstImgUUID,
                  dstVolUUID, descr, dstSdUUID, volType, volFormat,
                  preallocate, postZero, force, discard):
        """
        Creates a new template/volume from VM.
        It does this it by collapse and copy the whole chain
        (baseVolUUID->srcVolUUID).

        :param sdUUID: The UUID of the storage domain in which the image
                       resides.
        :type sdUUID: UUID
        :param vmUUID: The UUID of the virtual machine you want to copy from.
        :type vmUUID: UUID
        :param srcImageUUID: The UUID of the source image you want to copy
                             from.
        :type srcImageUUID: UUID
        :param srcVolUUID: The UUID of the source volume you want to copy from.
        :type srcVolUUID: UUID
        :param dstImageUUID: The UUID of the destination image you want to copy
                             to.
        :type dstImageUUID: UUID
        :param dstVolUUID: The UUID of the destination volume you want to copy
                           to.
        :type dstVolUUID: UUID
        :param descr: The human readable description of the new template.
        :type descr: str
        :param dstSdUUID: The UUID of the destination storage domain you want
                          to copy to.
        :type dstSdUUID: UUID
        :param volType: The volume type of the volume being copied to.
        :type volType: some enum?!
        :param volFormat: The format of the volume being copied to.
        :type volFormat: some enum?!
        :param preallocate: Should the data be preallocated.
        :type preallocate: bool
        :param postZero: ?
        :type postZero: ?
        :param force: Should the copy be forced.
        :type force: bool
        :param discard: Should the destination volume be discarded before
                        copying data to it.
        :type discard: bool

        :returns: a dict containing the UUID of the newly created image.
        :rtype: dict
        """
        src_img_ns = rm.getNamespace(sc.IMAGE_NAMESPACE, sdUUID)
        if dstSdUUID not in (sdUUID, sd.BLANK_UUID):
            dst_img_ns = rm.getNamespace(sc.IMAGE_NAMESPACE, dstSdUUID)
        else:
            dst_img_ns = src_img_ns

        with rm.acquireResource(src_img_ns, srcImgUUID, rm.SHARED), \
                rm.acquireResource(dst_img_ns, dstImgUUID, rm.EXCLUSIVE):
            img = image.Image(self.poolPath)
            dstUUID = img.copyCollapsed(
                sdUUID, vmUUID, srcImgUUID, srcVolUUID, dstImgUUID,
                dstVolUUID, descr, dstSdUUID, volType, volFormat, preallocate,
                postZero, force, discard)

        return dict(uuid=dstUUID)

    def moveImage(self, srcDomUUID, dstDomUUID, imgUUID, vmUUID, op, postZero,
                  force, discard):
        """
        Moves or Copies an image between storage domains within the same
        storage pool.

        :param spUUID: The storage pool where the operation will take place.
        :type spUUID: UUID
        :param srcDomUUID: The UUID of the storage domain you want to copy
                           from.
        :type srcDomUUID: UUID
        :param dstDomUUID: The UUID of the storage domain you want to copy to.
        :type dstDomUUID: UUID
        :param imgUUID: The UUID of the image you want to copy.
        :type imgUUID: UUID
        :param vmUUID: The UUID of the vm that owns the images. ?
        :type vmUUID: UUID
        :param op: The operation code?
        :type op: some enum?
        :param postZero: ?
        :param force: Should the operation be forced.
        :type force: bool
        :param discard: Discard the image before deletion
        :type discard: bool
        """
        src_img_ns = rm.getNamespace(sc.IMAGE_NAMESPACE, srcDomUUID)
        dst_img_ns = rm.getNamespace(sc.IMAGE_NAMESPACE, dstDomUUID)
        # For MOVE_OP acquire exclusive lock
        # For COPY_OP shared lock is enough
        if op == image.MOVE_OP:
            srcLock = rm.EXCLUSIVE
        elif op == image.COPY_OP:
            srcLock = rm.SHARED
        else:
            raise se.MoveImageError(imgUUID)

        with rm.acquireResource(src_img_ns, imgUUID, srcLock), \
                rm.acquireResource(dst_img_ns, imgUUID, rm.EXCLUSIVE):
            img = image.Image(self.poolPath)
            img.move(srcDomUUID, dstDomUUID, imgUUID, vmUUID, op, postZero,
                     force, discard)

    def cloneImageStructure(self, sdUUID, imgUUID, dstSdUUID):
        """
        Clone an image structure from a source domain to a destination domain
        within the same pool.

        :param spUUID: The storage pool where the operation will take place.
        :type spUUID: UUID
        :param sdUUID: The UUID of the storage domain you want to copy from.
        :type sdUUID: UUID
        :param imgUUID: The UUID of the image you want to copy.
        :type imgUUID: UUID
        :param dstSdUUID: The UUID of the storage domain you want to copy to.
        :type dstSdUUID: UUID
        """
        srcImgResNs = rm.getNamespace(sc.IMAGE_NAMESPACE, sdUUID)
        dstImgResNs = rm.getNamespace(sc.IMAGE_NAMESPACE, dstSdUUID)

        first_resource, second_resource = sorted((
            (srcImgResNs, imgUUID, rm.SHARED),
            (dstImgResNs, imgUUID, rm.EXCLUSIVE),
        ))
        with rm.acquireResource(*first_resource):
            with rm.acquireResource(*second_resource):
                img = image.Image(self.poolPath)
                img.cloneStructure(sdUUID, imgUUID, dstSdUUID)

    def syncImageData(self, sdUUID, imgUUID, dstSdUUID, syncType):
        """
        Synchronize image data between storage domains within same pool.

        :param spUUID: The storage pool where the operation will take place.
        :type spUUID: UUID
        :param sdUUID: The UUID of the storage domain you want to copy from.
        :type sdUUID: UUID
        :param imgUUID: The UUID of the image you want to copy.
        :type imgUUID: UUID
        :param dstSdUUID: The UUID of the storage domain you want to copy to.
        :type dstSdUUID: UUID
        :param syncType: The type of sync to perform (all volumes, etc.).
        :type syncType: syncType enum
        """
        srcImgResNs = rm.getNamespace(sc.IMAGE_NAMESPACE, sdUUID)
        dstImgResNs = rm.getNamespace(sc.IMAGE_NAMESPACE, dstSdUUID)

        first_resource, second_resource = sorted((
            (srcImgResNs, imgUUID, rm.SHARED),
            (dstImgResNs, imgUUID, rm.EXCLUSIVE),
        ))
        with rm.acquireResource(*first_resource):
            with rm.acquireResource(*second_resource):
                img = image.Image(self.poolPath)
                img.syncData(sdUUID, imgUUID, dstSdUUID, syncType)

    def uploadImage(self, methodArgs, sdUUID, imgUUID, volUUID=None):
        """
        Upload an image to a remote endpoint using the specified method and
        methodArgs.
        """
        img_ns = rm.getNamespace(sc.IMAGE_NAMESPACE, sdUUID)
        with rm.acquireResource(img_ns, imgUUID, rm.SHARED):
            img = image.Image(self.poolPath)
            return img.upload(methodArgs, sdUUID, imgUUID, volUUID)

    def downloadImage(self, methodArgs, sdUUID, imgUUID, volUUID=None):
        """
        Download an image from a remote endpoint using the specified method
        and methodArgs.
        """
        img_ns = rm.getNamespace(sc.IMAGE_NAMESPACE, sdUUID)
        with rm.acquireResource(img_ns, imgUUID, rm.EXCLUSIVE):
            img = image.Image(self.poolPath)
            return img.download(methodArgs, sdUUID, imgUUID, volUUID)

    def uploadImageToStream(self, methodArgs, callback, startEvent, sdUUID,
                            imgUUID, volUUID=None):
        """
        Retrieves an image from to a given file the specified method
        and methodArgs.
        """
        while not startEvent.is_set():
            startEvent.wait()

        img_ns = rm.getNamespace(sc.IMAGE_NAMESPACE, sdUUID)

        # NOTE: We must take exclusive lock here since we can have concurrent
        # readers, and each reader is activating the volume before the copy and
        # deactivating the volume after the copy. Without an exclusive lock,
        # one reader can deactivate the volume just after the other reader
        # activated the volume.
        # See https://bugzilla.redhat.com/1694972
        with rm.acquireResource(img_ns, imgUUID, rm.EXCLUSIVE):
            try:
                img = image.Image(self.poolPath)
                return img.copyFromImage(methodArgs, sdUUID, imgUUID, volUUID)
            finally:
                callback()

    def downloadImageFromStream(self, methodArgs, callback, sdUUID, imgUUID,
                                volUUID=None):
        """
        Download an image from a stream.
        """
        img_ns = rm.getNamespace(sc.IMAGE_NAMESPACE, sdUUID)
        with rm.acquireResource(img_ns, imgUUID, rm.EXCLUSIVE):
            try:
                img = image.Image(self.poolPath)
                return img.copyToImage(methodArgs, sdUUID, imgUUID, volUUID)
            finally:
                callback()

    def reconcileVolumeChain(self, sdUUID, imgUUID, leafVolUUID):
        """
        Determines the actual volume chain for an offline image and returns it.
        If the actual chain differs from storage metadata, the metadata is
        corrected to reflect the actual chain.

        :param sdUUID: The UUID of the storage domain that contains the image.
        :type sdUUID: UUID
        :param imgUUID: The UUID of the image to be checked.
        :type imgUUID: UUID
        :param leafVolUUID: The UUID of the last known leaf volume.
        :type leafVolUUID: UUID
        :returns: A dict with a list of volume UUIDs in the corrected chain
        :rtype: dict
        """
        img_ns = rm.getNamespace(sc.IMAGE_NAMESPACE, sdUUID)
        with rm.acquireResource(img_ns, imgUUID, rm.EXCLUSIVE):
            img = image.Image(self.poolPath)
            chain = img.reconcileVolumeChain(sdUUID, imgUUID, leafVolUUID)
        return dict(volumes=chain)

    def prepareMerge(self, subchainInfo):
        """
        This operation is required before performing (cold) merge.
        Prepare merge will calculate the required allocation for base volume,
        extend the base volume or enlarge it (if the size of volume being
        removed is larger than the base size), and mark it as ILLEGAL.
        """
        merge.prepare(subchainInfo)

    def finalizeMerge(self, subchainInfo):
        """
        This operation is required after (cold) merge completes.
        Finalize will update qcow metadata and the vdsm volume metadata to
        reflect that a volume is being removed from the chain.
        """
        merge.finalize(subchainInfo)

    def createVolume(self, sdUUID, imgUUID, size, volFormat, preallocate,
                     diskType, volUUID=None, desc="",
                     srcImgUUID=sc.BLANK_UUID,
                     srcVolUUID=sc.BLANK_UUID,
                     initialSize=None,
                     addBitmaps=False,
                     legal=True,
                     sequence=0):
        """
        Creates a new volume.

        .. note::
            If the *imgUUID* is **identical** to the *srcImgUUID* the new
            volume will be logically considered a snapshot of the old volume.
            If the *imgUUID* is **different** from the *srcImgUUID* the old
            volume will be logically considered as a template of the new
            volume.

        :param sdUUID: The UUID of the storage domain that contains the volume.
        :type sdUUID: UUID
        :param imgUUID: The UUID of the image that the new volume will have.
        :type imgUUID: UUID
        :param size: The size of the new volume in bytes.
        :type size: int
        :param volFormat: The format of the new volume.
        :type volFormat: some enum ?!
        :param preallocate: Should the volume be preallocated.
        :type preallocate: bool
        :param diskType: The disk type of the new volume.
        :type diskType: :enum:`constants.VOL_DISKTYPE`
        :param volUUID: The UUID of the new volume that will be created.
        :type volUUID: UUID
        :param desc: A human readable description of the new volume.
        :param srcImgUUID: The UUID of the image that resides on the volume
                           that will be the base of the new volume.
        :type srcImgUUID: UUID
        :param srcVolUUID: The UUID of the volume that will be the base of the
                           new volume.
        :type srcVolUUID: UUID
        :param initialSize: The initial size of the volume in case of thin
                            provisioning.
        :type initialSize: int
        :param addBitmaps: If true, add source volume bitmaps to the
                           created volume.
        :type addBitmaps: boolean
        :param legal: If true, create the volume as legal.
        :type legal: boolean

        :type sequence: int
        :param sequence: The sequence number of the volume.

        :returns: a dict with the UUID of the new volume.
        :rtype: dict
        """
        img_ns = rm.getNamespace(sc.IMAGE_NAMESPACE, sdUUID)

        if imgUUID != srcImgUUID and srcImgUUID != sc.BLANK_UUID:
            srcDom = sdCache.produce(sdUUID)
            srcVol = srcDom.produceVolume(srcImgUUID, srcVolUUID)

            if not srcVol.isShared():
                if srcVol.getParent() == sc.BLANK_UUID:
                    with rm.acquireResource(img_ns, srcImgUUID, rm.EXCLUSIVE):

                        self.log.debug("volume %s is not shared. "
                                       "Setting it as shared", srcVolUUID)
                        srcVol.setShared()
                else:
                    raise se.VolumeNonShareable(srcVol)

        with rm.acquireResource(img_ns, imgUUID, rm.EXCLUSIVE):
            newVolUUID = sdCache.produce(sdUUID).createVolume(
                imgUUID=imgUUID, capacity=size, volFormat=volFormat,
                preallocate=preallocate, diskType=diskType, volUUID=volUUID,
                desc=desc, srcImgUUID=srcImgUUID, srcVolUUID=srcVolUUID,
                initial_size=initialSize, add_bitmaps=addBitmaps, legal=legal,
                sequence=sequence)

        return dict(uuid=newVolUUID)

    def deleteVolume(self, sdUUID, imgUUID, volumes, postZero, force, discard):
        """
        Deletes a given volume.

        .. note::
            This function assumes:

                * If more than 1 volume, all volumes are a part of the **same**
                  chain.
                * Given volumes are ordered, so predecessor is deleted before
                  ancestor. ? (might be confused?)

        :param sdUUID: The UUID of the storage domain that contains the volume.
        :type sdUUID: UUID
        :param imgUUID: The UUID of the image that id that the new volume will
                        have.
        :type imgUUID: UUID
        """
        img_ns = rm.getNamespace(sc.IMAGE_NAMESPACE, sdUUID)

        with rm.acquireResource(img_ns, imgUUID, rm.EXCLUSIVE):
            dom = sdCache.produce(sdUUID)
            for volUUID in volumes:
                vol = dom.produceVolume(imgUUID, volUUID)
                vol.delete(postZero=postZero, force=force, discard=discard)

    def purgeImage(self, sdUUID, imgUUID, volsByImg, discard):
        """
        Free the space taken by a given list of volumes belonging to imgUUID.

        :param domain: The UUID of the relevant domain containing the image.
        :type sdUUID: UUID
        :param imgUUID: The UUID of the relevant image.
        :type imgUUID: UUID
        :param volsByImg: List of the volumes to remove.
        :type volsByImg: list
        :param discard: discard the volumes before removal
        :type discard: bool
        """
        domain = sdCache.produce(sdUUID=sdUUID)
        domain.purgeImage(sdUUID, imgUUID, volsByImg, discard)

    def deleteImage(self, domain, imgUUID, volsByImg):
        """
        Deletes a given list of volumes belonging to imgUUID.

        .. note::
            This function cannot be scheduled as it takes the domain object
            (for performance reasons) instead of the sdUUID.

            Few arguments could be evetually optimzed out and normalized but
            it requires some refactoring.

        :param domain: The object of the domain containing the image.
        :type sdUUID: StorageDomain
        :param imgUUID: The UUID of the relevant image.
        :type imgUUID: UUID
        :param volsByImg: List of the volumes to remove.
        :type volsByImg: list
        """
        domain.deleteImage(domain.sdUUID, imgUUID, volsByImg)

    def setVolumeDescription(self, sdUUID, imgUUID, volUUID, description):
        self._assert_sd_in_pool(sdUUID)
        img_ns = rm.getNamespace(sc.IMAGE_NAMESPACE, sdUUID)
        with rm.acquireResource(img_ns, imgUUID, rm.EXCLUSIVE):
            vol = sdCache.produce(sdUUID).produceVolume(imgUUID, volUUID)
            vol.setDescription(description)

    def setVolumeLegality(self, sdUUID, imgUUID, volUUID, legality):
        self._assert_sd_in_pool(sdUUID)
        img_ns = rm.getNamespace(sc.IMAGE_NAMESPACE, sdUUID)
        with rm.acquireResource(img_ns, imgUUID, rm.EXCLUSIVE):
            vol = sdCache.produce(sdUUID).produceVolume(imgUUID, volUUID)
            vol.setLegality(legality)

    def getVmsList(self, sdUUID):
        self._assert_sd_in_pool(sdUUID)
        return sdCache.produce(sdUUID).getVMsList()

    def getVmsInfo(self, sdUUID, vmList=None):
        self._assert_sd_in_pool(sdUUID)
        return sdCache.produce(sdUUID).getVMsInfo(vmList=vmList)

    def validateVolumeChain(self, sdUUID, imgUUID):
        image.Image(self.poolPath).validateVolumeChain(sdUUID, imgUUID)

    def extendSD(self, sdUUID, devlist, force):
        self._assert_sd_in_pool(sdUUID)
        sdCache.produce(sdUUID).extend(devlist, force)

    def resizePV(self, sdUUID, guid):
        self._assert_sd_in_pool(sdUUID)
        sdCache.produce(sdUUID).resizePV(guid)

    def setSDDescription(self, sd, description):
        self._assert_sd_in_pool(sd.sdUUID)
        sd.setDescription(description)

    def getAllTasksStatuses(self):
        return self.taskMng.getAllTasksStatuses("spm")

    def getAllTasksInfo(self):
        return self.taskMng.getAllTasksInfo("spm")

    # Lease operations

    def create_lease(self, lease, metadata=None):
        """
        SPM task function for creating external lease.

        Succeeds if external lease was created or already exists.
        """
        dom = sdCache.produce(lease.sd_id)
        try:
            dom.create_lease(
                lease.lease_id, metadata=metadata, host_id=self.id)
        except xlease.LeaseExists:
            # We cannot fail the task as engine is not checking tasks errors.
            self.log.info("Reusing existing lease: %s:%s",
                          lease.sd_id, lease.lease_id)

    def delete_lease(self, lease):
        """
        SPM task function for deleting external lease.

        Succeeds if external lease was deleted or do not exists.
        """
        dom = sdCache.produce(lease.sd_id)
        try:
            dom.delete_lease(lease.lease_id)
        except se.NoSuchLease:
            # We cannot fail the task as engine is not checking tasks errors.
            self.log.info("Lease already deleted: %s:%s",
                          lease.sd_id, lease.lease_id)

    def rebuild_leases(self, sd_id):
        """
        SPM task function for rebuilding the external leases volume.
        """
        dom = sdCache.produce(sd_id)
        dom.rebuild_external_leases()

    @unsecured
    def _master_volume_path(self, vol):
        return os.path.join(
            sc.REPO_DATA_CENTER,
            self.spUUID,
            sc.POOL_MASTER_DOMAIN,
            sd.DOMAIN_META_DATA, vol)

    # Watching SPM lease

    def _start_watching_spm_lease(self, master_sd):
        """
        If the master storage domain supports inquire, start watching the SPM
        lease.

        Panics on failures.
        """
        if not config.getboolean("spm", "watchdog_enable"):
            return

        if not master_sd.supports_inquire:
            return

        if self._watchdog is not None:
            raise RuntimeError("Watchdog already started")

        try:
            self._watchdog = spwd.Watchdog(
                master_sd,
                check_interval=config.getfloat("spm", "watchdog_interval"))
            self._watchdog.start()
        except:
            panic("Error starting SPM lease watchdog")

    def _stop_watching_spm_lease(self):
        """
        If we are watching the SPM lease, stop the watchdog.

        Panics on failures.
        """
        if self._watchdog:
            try:
                self._watchdog.stop()
            except:
                panic("Error stopping SPM lease watchdog")
            self._watchdog = None
