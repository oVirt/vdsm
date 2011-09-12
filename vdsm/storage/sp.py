#
# Copyright 2009-2011 Red Hat, Inc.
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
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA  02110-1301  USA
#
# Refer to the README and COPYING files for full details of the license
#

import os
from glob import iglob, glob
import logging
import time
import threading
import errno
import uuid
import codecs
from contextlib import nested
from functools import partial

import constants
import storage_mailbox
import blockSD
import fileSD
import sd
import misc
from misc import Event
import fileUtils
from config import config
from sdc import sdCache
import storage_exception as se
from persistentDict import DictValidator
from processPool import Timeout
from securable import Securable, unsecured
import image
from resourceFactories import IMAGE_NAMESPACE
from storageConstants import STORAGE
import resourceManager as rm
import volume

BLANK_POOL_UUID = '00000000-0000-0000-0000-000000000000'

POOL_MASTER_DOMAIN = 'mastersd'

MAX_POOL_DESCRIPTION_SIZE = 50

PMDK_DOMAINS = "POOL_DOMAINS"
PMDK_POOL_DESCRIPTION = "POOL_DESCRIPTION"
PMDK_LVER = "POOL_SPM_LVER"
PMDK_SPM_ID = "POOL_SPM_ID"
PMDK_MASTER_VER = "MASTER_VERSION"

rmanager = rm.ResourceManager.getInstance()

SPM_ACQUIRED = 'SPM'
SPM_CONTEND = 'Contend'
SPM_FREE = 'Free'

def domainListEncoder(domDict):
    domains = ','.join([ '%s:%s' % (k, v) for k, v in domDict.iteritems()])
    return domains

def domainListDecoder(s):
    domList = {}
    if not s:
        return domList
    for domDecl in s.split(","):
        k, v = domDecl.split(':')
        domList[k.strip("'")] = v.strip("'").capitalize()
    return domList

SP_MD_FIELDS = {
        # Key          dec,  enc
        PMDK_DOMAINS : (domainListDecoder, domainListEncoder),
        PMDK_POOL_DESCRIPTION : (str, str), # should be decode\encode utf8
        PMDK_LVER : (int, str),
        PMDK_SPM_ID : (int, str),
        PMDK_MASTER_VER : (int, str)
    }

# Calculate how many domains can be in the pool before overflowing the Metadata
MAX_DOMAINS = blockSD.SD_METADATA_SIZE - blockSD.METADATA_BASE_SIZE
MAX_DOMAINS -= MAX_POOL_DESCRIPTION_SIZE + sd.MAX_DOMAIN_DESCRIPTION_SIZE
MAX_DOMAINS -= blockSD.PVS_METADATA_SIZE
MAX_DOMAINS /= 48

class StatsThread(threading.Thread):
    log = logging.getLogger('Storage.StatsThread')
    onDomainConnectivityStateChange = Event("StatsThread.onDomainConnectivityStateChange")
    def __init__(self, func, sdUUID):
        """
        StatsThread gets two arguments on instatiation:
        func - function to call
        dom - argument to pass to func()
        """
        threading.Thread.__init__(self)
        self._statscache = dict(result=
            dict(code=200, lastCheck=0.0, delay='0', valid=True))
        self._statsdelay = config.getint('irs', 'sd_health_check_delay')
        self._statsletrun = True
        self._statsfunc = func
        self._sdUUID = sdUUID
        self._domain = None


    def run(self):
        while self._statsletrun:
            try:
                if self._domain is None:
                    self._domain = sdCache.produce(self._sdUUID)
                stats, code = self._statsfunc(self._domain)
            except se.StorageException, e:
                self.log.error("Unexpected error", exc_info=True)
                code = e.code
            except Exception, e:
                self.log.error("Unexpected error", exc_info=True)
                code = 200

            delay = 0
            if self._domain is not None:
                try:
                    # This is handled seperatly because in case of this kind
                    # of failure we don't want to print stack trace
                    delay = self._domain.getReadDelay()
                except Exception, e:
                    self.log.error("Could not figure out delay for domain `%s` (%s)", self._sdUUID, e)
                    code = 200

            if code != 0:
                self._domain = None

            finish = time.time()

            stats['finish'] = finish
            stats['result'] = dict(code=code, lastCheck=finish,
                delay=str(delay), valid=(code == 0))

            try:
                if self._statscache["result"]["valid"] != stats["result"]["valid"]:
                    self.onDomainConnectivityStateChange.emit(self._sdUUID, stats["result"]["valid"])
            except:
                self.log.error("Could not emit domain state event", exc_info=True)

            self._statscache.update(stats)

            count = 0
            while self._statsletrun and count < self._statsdelay:
                count += 1
                time.sleep(1)

        self._statsfunc = None


    def stop(self):
        self._statsletrun = False


    def getStatsResults(self):
        return self._statscache.copy()


class StoragePool:
    '''
    StoragePool object should be relatively cheap to construct. It should defer
    any heavy lifting activities until the time it is really needed.
    '''

    __metaclass__ = Securable

    log = logging.getLogger('Storage.StoragePool')
    storage_repository = config.get('irs', 'repository')
    _poolsTmpDir = config.get('irs', 'pools_data_dir')
    lvExtendPolicy = config.get('irs', 'vol_extend_policy')

    def __init__(self, spUUID, taskManager):
        self._domainsToUpgrade = []
        self.lock = threading.Lock()
        self._setUnsafe()
        self.spUUID = str(spUUID)
        self.poolPath = os.path.join(self.storage_repository, self.spUUID)
        self.id = None
        self.scsiKey = None
        self.taskMng = taskManager
        self._poolFile = os.path.join(self._poolsTmpDir, self.spUUID)
        self.hsmMailer = None
        self.spmMailer = None
        self.masterDomain = None
        self.repostats = {}
        self.spmStarted = False
        self.spmRole = SPM_FREE

    @unsecured
    def getSpmRole(self):
        return self.spmRole

    @unsecured
    def getSpmLver(self):
        return self.getMetaParam(PMDK_LVER)

    @unsecured
    def getSpmStatus(self):
        #If this is the SPM no need to double check
        return self.getSpmRole(), self.getSpmLver(), self.getSpmId()


    def __del__(self):
        if len(self.repostats) > 0:
            threading.Thread(target=self.disconnectDomains).start()

    def forceFreeSpm(self):
        # DO NOT USE, STUPID, HERE ONLY FOR BC
        # TODO: SCSI Fence the 'lastOwner'
        self.setMetaParams({PMDK_SPM_ID: -1, PMDK_LVER: -1})
        self.spmRole = SPM_FREE

    def _upgradePoolDomain(self, sdUUID, isValid):
        # This method is called everytime the onDomainConnectivityStateChange
        # event is emited, this event is emited even when a domain goes INVALID
        # if this happens there is nothing for us to do no matter what the
        # domain is
        if not isValid:
            return

        domain = sdCache.produce(sdUUID)
        if sdUUID not in self._domainsToUpgrade:
            return

        self.log.debug("Preparing to upgrade domain %s", sdUUID)

        try:
            #Assumed that the domain can be attached only to one pool
            targetDomVersion = self.masterDomain.getVersion()
        except:
            self.log.error("Error while preparing domain `%s` upgrade", sdUUID, exc_info=True)
            return

        with rmanager.acquireResource(STORAGE, "upgrade_" + sdUUID, rm.LockType.exclusive):
            with rmanager.acquireResource(STORAGE, sdUUID, rm.LockType.exclusive):
                if sdUUID not in self._domainsToUpgrade:
                    return

                # This can never be the master
                # Non data domain should not be upgraded
                domClass = domain.getDomainClass()
                if domClass != sd.DATA_DOMAIN:
                    self.log.debug("Domain `%s` is not a data domain it is an %s domain, not upgrading", sdUUID, domClass)
                else:
                    domain.invalidateMetadata()
                    domVersion = domain.getVersion()
                    if domVersion > targetDomVersion:
                        self.log.critical("Found a domain with a more advanced version then the master domain")
                    elif domVersion < targetDomVersion:
                        try:
                            domain.upgrade(targetDomVersion)
                        except:
                            self.log.warn("Could not upgrade domain `%s`", sdUUID, exc_info=True)
                            return

                self._domainsToUpgrade.remove(sdUUID)
                if len(self._domainsToUpgrade) == 0:
                    self.log.debug("All domains are upgraded, unregistering from state change event")
                    try:
                        StatsThread.onDomainConnectivityStateChange.unregister(self._upgradePoolDomain)
                    except KeyError:
                        pass

    @unsecured
    def startSpm(self, prevID, prevLVER, scsiFencing, maxHostID, expectedDomVersion=None):
        """
        Starts the SPM functionality.

        :param spUUID: The UUID of the storage pool you want to manage with the SPM.
        :type spUUID: UUID
        :param prevID: obsolete
        :param prevLVER: obsolete
        :param scsiFencing: Should there be scsi fencing.?
        :type scsiFencing: bool
        :param maxHostID: The maximun ID of the host.?
        :type maxHostID: int

        .. note::
            if the SPM is already started the function will fail silently.

        :raises: :exc:`storage_exception.OperationInProgress` if called during an allready running connection attempt.
                 (makes the fact that it fails silently does not matter very much).
        """
        self.lock.acquire()
        try:
            if self.spmRole == SPM_ACQUIRED:
                return True
            # Since we added the lock the following should NEVER happen
            if self.spmRole == SPM_CONTEND:
                raise se.OperationInProgress("spm start %s" % self.spUUID)

            self.updateMonitoringThreads()
            self.invalidateMetadata()
            oldlver = self.getSpmLver()
            oldid = self.getSpmId()
            masterDomVersion = self.getVersion()
            # If no specific domain version was specified use current master domain version
            if expectedDomVersion is None:
                expectedDomVersion = masterDomVersion

            if masterDomVersion > expectedDomVersion:
                raise se.CurrentVersionTooAdvancedError(self.masterDomain.sdUUID,
                        curVer=masterDomVersion, expVer=expectedDomVersion)

            if int(oldlver) != int(prevLVER) or int(oldid) != int(prevID):
                self.log.info("expected previd:%s lver:%s got request for previd:%s lver:%s" % (oldid, oldlver, prevID, prevLVER))


            # Acquire spm lock
            try:
                self.spmRole = SPM_CONTEND
                self.acquireClusterLock()
            except:
                self.spmRole = SPM_FREE
                raise

            self.log.debug("spm lock acquired successfully")

            try:
                self.lver = int(oldlver) + 1

                self.invalidateMetadata()
                self.setMetaParams({PMDK_LVER: self.lver,
                    PMDK_SPM_ID: self.id}, __securityOverride=True)
                self._maxHostID = maxHostID

                # Upgrade the master domain now if needed
                self._upgradePool(expectedDomVersion, __securityOverride=True)

                self.masterDomain.mountMaster()
                self.masterDomain.createMasterTree(log=True)
                self.tasksDir = os.path.join(self.poolPath, POOL_MASTER_DOMAIN, sd.MASTER_FS_DIR, sd.TASKS_DIR)

                try:
                    # Make sure backup domain is active
                    self.checkBackupDomain(__securityOverride=True)
                except Exception, e:
                    self.log.error("Backup domain validation failed, exc_info=True")

                self.taskMng.loadDumpedTasks(self.tasksDir)

                self.spmStarted = True
                self.spmRole = SPM_ACQUIRED

                # Once setSafe completes we are running as SPM
                self._setSafe()

                # Mailbox issues SPM commands, therefore we start it AFTER spm commands are allowed to run to prevent
                # a race between the mailbox and the "self._setSafe() call"

                # Create mailbox if SAN pool (currently not needed on nas)
                # FIXME: Once pool contains mixed type domains (NFS + Block) the mailbox
                # will have to be created if there is an active block domain in the pool
                # or once one is activated

                #FIXME : Use a system wide grouping mechanizm
                sanPool = self.masterDomain.getStorageType() in sd.BLOCK_DOMAIN_TYPES  # Check if pool is SAN or NAS
                if sanPool and self.lvExtendPolicy == "ON":
                    self.spmMailer = storage_mailbox.SPM_MailMonitor(self, maxHostID)
                    self.spmMailer.registerMessageType('xtnd', partial(storage_mailbox.SPM_Extend_Message, self))
                else:
                    self.spmMailer = None

                # Restore tasks is last because tasks are spm ops (spm has to be started)
                self.taskMng.recoverDumpedTasks()

                self.log.debug("ended.")

            except Exception, e:
                self.log.error("Unexpected error", exc_info=True)
                self.log.error("failed: %s" % str(e))
                self.stopSpm(force=True, __securityOverride=True)
                raise
        finally:
            self.lock.release()

    @unsecured
    def _shutDownUpgrade(self):
        with rmanager.acquireResource(STORAGE, "upgrade_" + self.spUUID, rm.LockType.exclusive):
            domains = self._domainsToUpgrade
            try:
                StatsThread.onDomainConnectivityStateChange.unregister(self._upgradePoolDomain)
            except KeyError:
                pass
            requests = []

            def cancelUpgrade(sdUUID, req, res):
                try:
                    self._domainsToUpgrade.remove(sdUUID)
                except ValueError:
                    pass

                res.release()

            for sdUUID in domains:
                req = rmanager.registerResource(STORAGE, "upgrade_" + sdUUID, rm.LockType.exclusive, partial(cancelUpgrade, sdUUID))
                requests.append(req)

            for req in requests:
                req.wait()

    @classmethod
    def __cleanupMasterMount(cls):
        """
        Check whether there are any dangling master file systems still mounted
        and unmount them if found.
        """
        masters = os.path.join(cls.storage_repository, sd.DOMAIN_MNT_POINT,
                               sd.BLOCKSD_DIR, "*", sd.MASTER_FS_DIR)
        for master in glob(masters):
            if fileUtils.isMounted(mountPoint=master):
                cls.log.debug("unmounting %s", master)
                try:
                    blockSD.BlockStorageDomain.doUnmountMaster(master)
                except se.StorageDomainMasterUnmountError, e:
                    misc.panic("unmount %s failed - %s" % (master, e))
            else:
                cls.log.debug("master `%s` is not mounted, skipping", master)

    def stopSpm(self, force=False):
        self.lock.acquire()
        if not force and self.getSpmRole() in SPM_FREE:
            return True

        self._shutDownUpgrade()
        self._setUnsafe()

        stopFailed = False

        try:
            self.__cleanupMasterMount()
        except:
            # If unmounting fails the vdsm panics.
            stopFailed = True

        try:
            if self.spmMailer:
                self.spmMailer.stop()
        except:
            # Here we are just begin polite.
            # SPM will also clean this on start up.
            pass

        if not stopFailed:
            try:
                self.setMetaParam(PMDK_SPM_ID, -1)
            except:
                pass # The system can handle this inconsistency

        try:
            self.releaseClusterLock()
        except:
            stopFailed = True

        if stopFailed:
            misc.panic("Unrecoverable errors during SPM stop process.")

        self.spmStarted = False
        self.spmRole = SPM_FREE

    def _upgradePool(self, targetDomVersion):
        with rmanager.acquireResource(STORAGE, "upgrade_" + self.spUUID, rm.LockType.exclusive):
            if len(self._domainsToUpgrade) > 0:
                raise se.PoolUpgradeInProgress(self.spUUID)

            sd.validateDomainVersion(targetDomVersion)
            self.log.info("Trying to upgrade master domain `%s`", self.masterDomain.sdUUID)
            with rmanager.acquireResource(STORAGE, self.masterDomain.sdUUID, rm.LockType.exclusive):
                self.masterDomain.upgrade(targetDomVersion)

            self.log.debug("Marking all domains for upgrade")
            self._domainsToUpgrade = self.getDomains(activeOnly=True).keys()
            try:
                self._domainsToUpgrade.remove(self.masterDomain.sdUUID)
            except ValueError:
                pass

            self.log.debug("Registering with state change event")
            StatsThread.onDomainConnectivityStateChange.register(self._upgradePoolDomain)
            self.log.debug("Running initial domain upgrade threads")
            for sdUUID in self._domainsToUpgrade:
                threading.Thread(target=self._upgradePoolDomain, args=(sdUUID, True), kwargs={"__securityOverride": True}).start()



    @unsecured
    def __createMailboxMonitor(self):
        # Currently mailbox is not needed for non block device sd's
        if self.hsmMailer:
            return

        if isinstance(self.masterDomain, blockSD.BlockStorageDomain) and self.lvExtendPolicy == "ON":
            self.hsmMailer = storage_mailbox.HSM_Mailbox(self.id, self.spUUID)

    @unsecured
    def __cleanupDomains(self, domlist, msdUUID, masterVersion):
        """
        Clean up domains after failed Storage Pool creation
        domlist - comma separated list of sdUUIDs
        """
        # Go through all the domains and detach them from the pool
        # Since something went wrong (otherwise why would we be cleaning
        # the mess up?) do not expect all the domains to exist
        domains = [sdCache.produce(d) for d in domlist]
        for d in domains:
            try:
                self.detachSD(d, msdUUID, masterVersion)
            except Exception:
                self.log.error("Unexpected error", exc_info=True)
        self.refresh()

    @unsecured
    def getMasterVersion(self):
        return self.getMetaParam(PMDK_MASTER_VER)

    @unsecured
    def acquireClusterLock(self):
        self.masterDomain.acquireClusterLock(self.id)

    @unsecured
    def releaseClusterLock(self):
        self.masterDomain.releaseClusterLock()

    @unsecured
    def validateAttachedDomain(self, sdUUID):
        domList = self.getDomains()
        if sdUUID not in domList:
            raise se.StorageDomainNotInPool(self.spUUID, sdUUID)
        # Avoid handle domains if not owned by pool
        dom = sdCache.produce(sdUUID)
        pools = dom.getPools()
        if self.spUUID not in pools:
            raise se.StorageDomainNotInPool(self.spUUID, sdUUID)


    @unsecured
    def validatePoolMVerHigher(self, masterVersion):
        """
        Make sure the masterVersion higher than that of the pool.

        :param masterVersion: the master version you want to validate
        :type masterVersion: int

        :raises: :exc:`storage_exception.StoragePoolWrongMasterVersion`
            exception if masterVersion doesn't follow the rules

        """
        mver = self.getMasterVersion()
        if not int(masterVersion) > mver:
            raise se.StoragePoolWrongMaster(self.spUUID, self.masterDomain.sdUUID)


    @unsecured
    def getMaximumSupportedDomains(self):
        msdInfo = self.masterDomain.getInfo()
        msdType = sd.name2type(msdInfo["type"])
        msdVersion = int(msdInfo["version"])
        if msdType in sd.BLOCK_DOMAIN_TYPES and msdVersion in blockSD.VERS_METADATA_LV:
            return MAX_DOMAINS
        else:
            return config.getint("irs", "maximum_domains_in_pool")

    @unsecured
    def create(self, poolName, msdUUID, domList, masterVersion, safeLease):
        """
        Create new storage pool with single/multiple image data domain.
        The command will create new storage pool meta-data attach each
        storage domain to that storage pool.
        At least one data (images) domain must be provided
         'poolName' - storage pool name
         'msdUUID' - master domain of this pool (one of domList)
         'domList' - list of domains (i.e sdUUID,sdUUID,...,sdUUID)
        """
        self.log.info("spUUID=%s poolName=%s master_sd=%s "
                      "domList=%s masterVersion=%s %s",
                      self.spUUID, poolName, msdUUID,
                      domList, masterVersion, str(safeLease))

        if msdUUID not in domList:
            raise se.InvalidParameterException("masterDomain", msdUUID)

        # Check the domains before pool creation
        for dom in domList:
            try:
                domain = sdCache.produce(dom)
                domain.validate()
            except se.StorageException:
                self.log.error("Unexpected error", exc_info=True)
                raise se.StorageDomainAccessError(dom)

            # Validate unattached domains
            if not domain.isISO():
                domain.invalidateMetadata()
                spUUIDs = domain.getPools()
                # Non ISO domains have only 1 pool
                if len(spUUIDs) > 0:
                    raise se.StorageDomainAlreadyAttached(spUUIDs[0], dom)

        fileUtils.createdir(self.poolPath)

        try:
            # Seeing as we are just creating the pool then the host doesn't
            # have an assigned Id for this pool.  When locking the domain we must use an Id
            self.id = 1000
            # Master domain is unattached and all changes to unattached domains
            # must be performed under storage lock
            msd = sdCache.produce(msdUUID)
            msd.changeLeaseParams(safeLease)
            msd.acquireClusterLock(self.id)
        except:
            self.id = None
            raise
        try:
            try:
                # Mark 'master' domain
                # We should do it before actually attaching this domain to the pool.
                # During 'master' marking we create pool metadata and each attached
                # domain should register there
                self.createMaster(poolName, msd, masterVersion, safeLease)
                self.__rebuild(msdUUID=msdUUID, masterVersion=masterVersion)
                # Attach storage domains to the storage pool
                # Since we are creating the pool then attach is done from the hsm and not the spm
                # therefore we must manually take the master domain lock
                # TBD: create will receive only master domain and further attaches should be done
                # under SPM

                # Master domain was already attached (in createMaster),
                # no need to reattach
                for sdUUID in domList:
                    # No need to attach the master
                    if sdUUID == msdUUID:
                        continue

                    self.attachSD(sdUUID)
            except Exception:
                self.log.error("Create domain canceled due to an unexpected error", exc_info=True)

                try:
                    fileUtils.cleanupdir(self.poolPath)
                    self.__cleanupDomains(domList, msdUUID, masterVersion)
                except:
                    self.log.error("Cleanup failed due to an unexpected error", exc_info=True)

                raise
        finally:
            msd.releaseClusterLock()
            self.id = None

        self.disconnectDomains()
        return True

    @unsecured
    def _saveReconnectInformation(self, hostID, scsiKey, msdUUID, masterVersion):
        pers = ["id=%d\n" % hostID]
        pers.append("scsiKey=%s\n" % scsiKey)
        pers.append("sdUUID=%s\n" % msdUUID)
        pers.append("version=%s\n" % masterVersion)
        with open(self._poolFile, "w") as f:
            f.writelines(pers)


    @unsecured
    def connect(self, hostID, scsiKey, msdUUID, masterVersion):
        """
        Connect a Host to a specific storage pool.

        Caller must acquire resource Storage.spUUID so that this method would never be called twice concurrently.
        """
        self.log.info("Connect host #%s to the storage pool %s with master domain: %s (ver = %s)" %
            (hostID, self.spUUID, msdUUID, masterVersion))

        if not os.path.exists(self._poolsTmpDir):
            msg = ("StoragePoolConnectionError for hostId: %s, on poolId: %s," +
                   " Pools temp data dir: %s does not exist" %
                    (hostID, self.spUUID, self._poolsTmpDir))
            self.log.error(msg)
            msg = "Pools temp data dir: %s does not exist" % (self._poolsTmpDir)
            raise se.StoragePoolConnectionError(msg)

        if os.path.exists(self._poolFile):
            os.unlink(self._poolFile)

        self._saveReconnectInformation(hostID, scsiKey, msdUUID, masterVersion)
        self.id = hostID
        self.scsiKey = scsiKey
        # Make sure SDCache doesn't have stale data (it can be in case of FC)
        sdCache.refresh()
        # Rebuild whole Pool
        self.__rebuild(msdUUID=msdUUID, masterVersion=masterVersion)
        self.__createMailboxMonitor()

        return True


    @unsecured
    def disconnectDomains(self):
        for sdUUID in self.repostats.keys():
            self.stopRepoStats(sdUUID)
        return True


    @unsecured
    def disconnect(self):
        """
        Disconnect a Host from specific storage pool.

        Caller must acquire resource Storage.spUUID so that this method would never be called twice concurrently.
        """
        self.log.info("Disconnect from the storage pool %s", self.spUUID)

        self.id = None
        self.scsiKey = None
        if os.path.exists(self._poolFile):
            os.unlink(self._poolFile)

        if self.hsmMailer:
            self.hsmMailer.stop()
            self.hsmMailer = None

        # Remove all links
        if os.path.exists(self.poolPath):
            fileUtils.cleanupdir(self.poolPath)

        self.disconnectDomains()

        return True


    @unsecured
    def getPoolParams(self):
        file = open(self._poolFile, "r")
        for line in file:
            pair = line.strip().split("=")
            if len(pair) == 2:
                if pair[0] == "id":
                    hostId = int(pair[1])
                elif pair[0] == "scsiKey":
                    scsiKey = pair[1]
                elif pair[0] == "sdUUID":
                    msdUUID = pair[1]
                elif pair[0] == "version":
                    masterVersion = pair[1]
        file.close()

        return hostId, scsiKey, msdUUID, masterVersion


    @unsecured
    def createMaster(self, poolName, domain, masterVersion, leaseParams):
        """
        Create a fresh master file system directory tree
        """
        # THIS METHOD MUST BE RUN UNDER DOMAIN STORAGE LOCK
        self.log.info("setting master domain for spUUID %s on sdUUID=%s", self.spUUID, domain.sdUUID)
        futurePoolMD = self._getPoolMD(domain)
        with futurePoolMD.transaction():
            domain.changeLeaseParams(leaseParams)
            for spUUID in domain.getPools():
                if spUUID != self.spUUID:
                    self.log.warn("Force detaching from pool `%s` because of reconstruct master", spUUID)
                    domain.detach(spUUID)
            domain.attach(self.spUUID)
            domain.changeRole(sd.MASTER_DOMAIN)

            futurePoolMD.update({
            PMDK_SPM_ID: -1,
            PMDK_LVER: -1,
            PMDK_MASTER_VER: masterVersion,
            PMDK_POOL_DESCRIPTION: poolName,
            PMDK_DOMAINS: {domain.sdUUID: sd.DOM_ACTIVE_STATUS}})

    @unsecured
    def reconstructMaster(self, poolName, msdUUID, domDict, masterVersion, safeLease):
        self.log.info("spUUID=%s poolName=%s"
                      " master_sd=%s domDict=%s masterVersion=%s "
                      "leaseparams=(%s)",
                      self.spUUID, poolName, msdUUID, domDict, masterVersion,
                      str(safeLease))

        if msdUUID not in domDict:
            raise se.InvalidParameterException("masterDomain", msdUUID)

        try:
            # Seeing as we are just creating the pool then the host doesn't
            # have an assigned Id for this pool.  When locking the domain we must use an Id
            self.id = 1000
            # Master domain is unattached and all changes to unattached domains
            # must be performed under storage lock
            futureMaster = sdCache.produce(msdUUID)
            futureMaster.changeLeaseParams(safeLease)
            futureMaster.acquireClusterLock(self.id)
            try:
                self.createMaster(poolName, futureMaster, masterVersion, safeLease)
                self.refresh(msdUUID=msdUUID, masterVersion=masterVersion)

                # TBD: Run full attachSD?
                domains = self.getDomains()
                for sdUUID in domDict:
                    domains[sdUUID] = domDict[sdUUID].capitalize()
                # Add domain to domain list in pool metadata
                self.setMetaParam(PMDK_DOMAINS, domains)
                self.log.info("Set storage pool domains: %s", domains)
            finally:
                # We need stop all repoStats threads that were started during reconstructMaster
                self.disconnectDomains()
                futureMaster.releaseClusterLock()
        finally:
            self.id = None


    @unsecured
    def copyPoolMD(self, prevMd, newMD):
        prevPoolMD = self._getPoolMD(prevMd)
        domains = prevPoolMD[PMDK_DOMAINS]
        pool_descr = prevPoolMD[PMDK_POOL_DESCRIPTION]
        lver = prevPoolMD[PMDK_LVER]
        spmId = prevPoolMD[PMDK_SPM_ID]
        # This is actually domain metadata, But I can't change this because of
        # backward compatibility
        leaseParams = prevMd.getLeaseParams()

        # Now insert pool metadata into new mastersd metadata

        newPoolMD = self._getPoolMD(newMD)
        with newPoolMD.transaction():
            newPoolMD.update({PMDK_DOMAINS: domains,
                PMDK_POOL_DESCRIPTION: pool_descr,
                PMDK_LVER: lver,
                PMDK_SPM_ID: spmId})
            newMD.changeLeaseParams(leaseParams)

    @unsecured
    def __masterMigrate(self, sdUUID, msdUUID, masterVersion):
        curmsd = sdCache.produce(sdUUID)
        newmsd = sdCache.produce(msdUUID)
        self._refreshDomainLinks(newmsd)
        curmsd.invalidateMetadata()
        newmsd.upgrade(curmsd.getVersion())

        # new 'master' should be in 'active' status
        domList = self.getDomains()
        if msdUUID not in domList:
            raise se.StorageDomainNotInPool(self.spUUID, msdUUID)
        if domList[msdUUID] != sd.DOM_ACTIVE_STATUS:
            raise se.StorageDomainNotActive(msdUUID)
        if newmsd.isISO():
            raise se.IsoCannotBeMasterDomain(msdUUID)
        if newmsd.isBackup():
            raise se.BackupCannotBeMasterDomain(msdUUID)

        # Copy master file system content to the new master
        src = os.path.join(curmsd.domaindir, sd.MASTER_FS_DIR)
        dst = os.path.join(newmsd.domaindir, sd.MASTER_FS_DIR)

        # Mount new master file system
        newmsd.mountMaster()
        # Make sure there is no cruft left over
        for dir in [newmsd.getVMsDir(), newmsd.getTasksDir()]:
            fileUtils.cleanupdir(dir)

        try:
            fileUtils.tarCopy(src, dst, exclude=("./lost+found",))
        except fileUtils.TarCopyFailed:
            self.log.error("tarCopy failed", exc_info = True)
            # Failed to copy the master data
            try:
                newmsd.unmountMaster()
            except Exception:
                self.log.error("Unexpected error", exc_info=True)
            raise se.StorageDomainMasterCopyError(msdUUID)

        self.copyPoolMD(curmsd, newmsd)

        path = newmsd.getMDPath()
        if not path:
            newmsd.unmountMaster()
            raise se.StorageDomainLayoutError("domain", msdUUID)

        # Acquire safelease lock on new master
        try:
            # Reset SPM lock because of the host still SPM
            # It will speedup new lock acquiring
            newmsd.initSPMlease()
            newmsd.acquireClusterLock(self.id)
        except Exception:
            self.log.error("Unexpected error", exc_info=True)
            newmsd.releaseClusterLock()
            newmsd.unmountMaster()
            raise
        self.log.debug("masterMigrate - lease acquired successfully")

        try:
            # Now mark new domain as 'master'
            # if things break down here move the master back pronto
            newPoolMD = self._getPoolMD(newmsd)
            with newPoolMD.transaction():
                newPoolMD[PMDK_MASTER_VER] = masterVersion
                newmsd.changeRole(sd.MASTER_DOMAIN)
            self._saveReconnectInformation(self.id, self.scsiKey, newmsd.sdUUID, masterVersion)
        except Exception:
            self.log.error("Unexpected error", exc_info=True)
            newmsd.releaseClusterLock()
            newmsd.unmountMaster()
            raise

        # From this point on we have a new master and should not fail
        try:
            # Now recreate 'mastersd' link
            # we can use refresh() to do the job
            self.refresh(msdUUID, masterVersion)

            # From this point on there is a new master domain in the pool
            # Now that we are beyond the criticial point we can clean up things
            curmsd.changeRole(sd.REGULAR_DOMAIN)

            # Clean up the old data from previous master fs
            for dir in [curmsd.getVMsDir(), curmsd.getTasksDir()]:
                fileUtils.cleanupdir(dir)

            # NOTE: here we unmount the *previous* master file system !!!
            curmsd.unmountMaster()
        except Exception:
            self.log.error("Unexpected error", exc_info=True)

        try:
            # Release old lease
            curmsd.releaseClusterLock()
        except Exception:
            self.log.error("Unexpected error", exc_info=True)


    @unsecured
    def __unmountLastMaster(self, sdUUID):
        curmsd = sdCache.produce(sdUUID)
        # Check if it's last domain and allow it detaching
        dl = self.getDomains(activeOnly=True)
        domList = dl.keys()
        if curmsd.sdUUID in domList:
            domList.remove(curmsd.sdUUID)
        for item in domList:
            domain = sdCache.produce(item)
            if domain.isData():
                # Failure, we have at least one more data domain
                # in the pool and one which can become 'master'
                raise se.StoragePoolHasPotentialMaster(item)
        curmsd.unmountMaster()

    def masterMigrate(self, sdUUID, msdUUID, masterVersion):
        self.log.info("sdUUID=%s spUUID=%s msdUUID=%s", sdUUID,  self.spUUID, msdUUID)

        # Check if we are migrating to or just unmounting last master
        if msdUUID != sd.BLANK_UUID:
            # TODO: is this check relevant?
            self.validatePoolMVerHigher(masterVersion)
            self.__masterMigrate(sdUUID, msdUUID, masterVersion)
            return False    # not last master

        self.__unmountLastMaster(sdUUID)
        return True     # last master

    def attachSD(self, sdUUID):
        """
        Attach a storage domain to the storage pool.
        This marks the storage domain as "attached" and links it
        to the storage pool
         'sdUUID' - storage domain UUID
        """
        self.log.info("sdUUID=%s spUUID=%s", sdUUID, self.spUUID)

        domains = self.getDomains()
        if sdUUID in domains:
            return True

        if len(domains) >= self.getMaximumSupportedDomains():
            raise se.TooManyDomainsInStoragePoolError()

        dom = sdCache.produce(sdUUID)
        dom.acquireClusterLock(self.id)
        try:
            #If you remove this condition, remove it from public_createStoragePool too.
            if dom.isData() and (dom.getVersion() != self.masterDomain.getVersion()):
                raise se.MixedSDVersionError(dom.sdUUID, dom.getVersion(), self.masterDomain.sdUUID, self.masterDomain.getVersion())
            dom.attach(self.spUUID)
            domains[sdUUID] = sd.DOM_ATTACHED_STATUS
            self.setMetaParam(PMDK_DOMAINS, domains)
            self._refreshDomainLinks(dom)
        finally:
            dom.releaseClusterLock()
        self.updateMonitoringThreads()


    def forcedDetachSD(self, sdUUID):
        self.log.warn("Force detaching domain `%s`", sdUUID)
        domains = self.getDomains()
        if sdUUID not in domains:
            return True
        del domains[sdUUID]
        self.setMetaParam(PMDK_DOMAINS, domains)
        self._cleanupDomainLinks(sdUUID)
        self.updateMonitoringThreads()
        self.log.debug("Force detach for domain `%s` is done", sdUUID)

    def detachSD(self, sdUUID, msdUUID, masterVersion):
        """
        Detach a storage domain from a storage pool.
        This removes the storage domain entry in the storage pool meta-data
        and leaves the storage domain in 'unattached' status.
         'sdUUID' - storage domain UUID
         'msdUUID' - master storage domain UUID
         'masterVersion' - new master storage domain version
        """
        self.log.info("sdUUID=%s spUUID=%s msdUUID=%s", sdUUID,  self.spUUID, msdUUID)

        dom = sdCache.produce(sdUUID)
        if dom.isISO():
            dom.acquireClusterLock(self.id)
        try:
            dom.invalidateMetadata()
            try:
                # Avoid detach domains if not owned by pool
                self.validateAttachedDomain(sdUUID)
                domList = self.getDomains()
                sd.validateSDStateTransition(sdUUID, domList[sdUUID], sd.DOM_UNATTACHED_STATUS)

                # If the domain being detached is the 'master', move all pool
                # metadata to the new 'master' domain (msdUUID)
                if sdUUID == self.masterDomain.sdUUID:
                    self.masterMigrate(sdUUID, msdUUID, masterVersion, __securityOverride=True)

                # Remove pool info from domain metadata
                dom.detach(self.spUUID)

                # Remove domain from pool metadata
                del domList[sdUUID]
                self.setMetaParam(PMDK_DOMAINS, domList, __securityOverride=True)
                self._cleanupDomainLinks(sdUUID)

                self.updateMonitoringThreads()
            except Exception:
                self.log.error("Unexpected error", exc_info=True)
        finally:
            if dom.isISO():
                dom.releaseClusterLock()


    def activateSD(self, sdUUID):
        """
        Activate a storage domain that is already a member in a storage pool.
        Validate that the storage domain is owned by the storage pool.
         'sdUUID' - storage domain UUID
        """
        self.log.info("sdUUID=%s spUUID=%s", sdUUID,  self.spUUID)

        # Avoid domain activation if not owned by pool
        self.validateAttachedDomain(sdUUID)
        domList = self.getDomains()
        dom = sdCache.produce(sdUUID)
        sd.validateSDStateTransition(sdUUID, domList[sdUUID], sd.DOM_ACTIVE_STATUS)

        # Do nothing if already active
        if domList[sdUUID] == sd.DOM_ACTIVE_STATUS:
            return True

        if dom.getDomainClass() == sd.DATA_DOMAIN:
            dom.upgrade(self.getVersion())

        dom.activate()
        # set domains also do rebuild
        domList[sdUUID] = sd.DOM_ACTIVE_STATUS
        self.setMetaParam(PMDK_DOMAINS, domList)
        self._refreshDomainLinks(dom)
        self.updateMonitoringThreads()
        return True


    def deactivateSD(self, sdUUID, new_msdUUID, masterVersion):
        """
        Deactivate a storage domain.
        Validate that the storage domain is owned by the storage pool.
        Change storage domain status to "Attached" in the storage pool meta-data.

        :param sdUUID: The UUID of the storage domain you want to deactivate.
        :param new_msdUUID: The UUID of the new master storage domain.
        :param masterVersion: new master storage domain version
        """
        self.log.info("sdUUID=%s spUUID=%s new_msdUUID=%s", sdUUID,  self.spUUID, new_msdUUID)
        domList = self.getDomains()
        if sdUUID not in domList:
            raise se.StorageDomainNotInPool(self.spUUID, sdUUID)
        try:
            dom = sdCache.produce(sdUUID)
            #Check that dom is really reachable and not a cached value.
            dom.validate(False)
        except (se.StorageException, Timeout):
            self.log.warn("deactivaing MIA domain `%s`", sdUUID, exc_info=True)
            if new_msdUUID != BLANK_POOL_UUID:
                #Trying to migrate master failed to reach actual msd.
                raise se.StorageDomainAccessError(sdUUID)
        else:
            if dom.isMaster():
                #Maybe there should be information in the exception that the UUID is
                #not invalid because of its format but because it is equal to the SD. Will be less confusing.
                #TODO: verify in masterMigrate().
                if sdUUID == new_msdUUID:
                    raise se.InvalidParameterException("new_msdUUID", new_msdUUID)
                self.masterMigrate(sdUUID, new_msdUUID, masterVersion)
            elif dom.isBackup():
                dom.unmountMaster()

        domList[sdUUID] = sd.DOM_ATTACHED_STATUS
        self.setMetaParam(PMDK_DOMAINS, domList)
        self.updateMonitoringThreads()

    @unsecured
    def _linkStorageDomain(self, src, linkName):
        self.log.info("Linking %s to %s", src, linkName)
        try:
            current = os.readlink(linkName)
        except OSError, e:
            if e.errno != errno.ENOENT:
                self.log.error("Can't link SD %s to %s", src, linkName, exc_info=True)
                return
        else:
            if current == linkName:
                return #Nothing to do
        #Rebuid the link
        tmp_link_name = os.path.join(self.storage_repository, str(uuid.uuid4()))
        os.symlink(src, tmp_link_name)     #make tmp_link
        os.rename(tmp_link_name, linkName)


    @unsecured
    def _cleanupDomainLinks(self, domain):
        linkPath = os.path.join(self.poolPath, domain)
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
            masterName = os.path.join(self.poolPath, POOL_MASTER_DOMAIN)
            self._linkStorageDomain(domain.domaindir, masterName)
        else:
            domPoolMD = self._getPoolMD(domain)
            with domPoolMD.transaction():
                domain.changeRole(sd.REGULAR_DOMAIN)
                domPoolMD[PMDK_MASTER_VER] = 0


    @unsecured
    def __rebuild(self, msdUUID, masterVersion):
        """
        Rebuild storage pool.
        """
        # master domain must be refreshed first
        self.masterDomain = self.getMasterDomain(msdUUID=msdUUID, masterVersion=masterVersion)
        self.updateMonitoringThreads()

        fileUtils.createdir(self.poolPath)

        # Find out all domains for future cleanup
        domainpat = os.path.join(self.poolPath, constants.UUID_GLOB_PATTERN)
        oldLinks = set(iglob(domainpat))

        # We should not rebuild non-active domains, because
        # they are probably disconnected from the host
        domUUIDs = self.getDomains(activeOnly=True).keys()

        #msdUUID should be present and active in getDomains result.
        #TODO: Consider remove if clause.
        if msdUUID in domUUIDs:
            domUUIDs.remove(msdUUID)

        #TODO: Consider to remove this whole block. UGLY!
        #We want to avoid lookups (vgs) of unknown block domains.
        #domUUIDs includes all the domains, file or block.
        block_mountpoint = os.path.join(sd.StorageDomain.storage_repository,
                sd.DOMAIN_MNT_POINT, sd.BLOCKSD_DIR)
        blockDomUUIDs = [vg.name for vg in blockSD.lvm.getVGs(domUUIDs)]
        domDirs = {} # {domUUID: domaindir}
        #Add the block domains
        for domUUID in blockDomUUIDs:
            domaindir = os.path.join(block_mountpoint, domUUID)
            domDirs[domUUID] = domaindir
            # create domain special volumes folder
            fileUtils.createdir(os.path.join(domaindir, sd.DOMAIN_META_DATA))
            fileUtils.createdir(os.path.join(domaindir, sd.DOMAIN_IMAGES))
        #Add the file domains
        for domUUID, domaindir in fileSD.scanDomains(): #[(fileDomUUID, file_domaindir)]
            domDirs[domUUID] = domaindir

        #Link all the domains to the pool
        for domUUID, domaindir in domDirs.iteritems():
            linkName = os.path.join(self.poolPath, domUUID)
            self._linkStorageDomain(domaindir, linkName)
            oldLinks.discard(linkName)

        # Always try to build master links
        try:
            self._refreshDomainLinks(self.masterDomain)
        except (se.StorageException, OSError):
            self.log.error("_refreshDomainLinks failed for master domain %s", self.masterDomain.sdUUID, exc_info=True)
        linkName = os.path.join(self.poolPath, self.masterDomain.sdUUID)
        oldLinks.discard(linkName)

        # Cleanup old trash from the pool
        for oldie in oldLinks:
            try:
                os.remove(oldie)
            except OSError as e:
                if e.errno != errno.ENOENT:
                    self.log.warn("Could not clean all trash from the pool dom `%s` (%s)", oldie, e)
            except Exception as e:
                    self.log.warn("Could not clean all trash from the pool dom `%s` (%s)", oldie, e)


    @unsecured
    def refresh(self, msdUUID=None, masterVersion=None):
        """
        Refresh storage pool.
         'msdUUID' - master storage domain UUID
        """
        sdCache.refresh()
        self.__rebuild(msdUUID=msdUUID, masterVersion=masterVersion)


    def updateVM(self, vmList, sdUUID=None):
        """
        Update VMs.
         'vmList' - [{'vm':vmUUID,'ovf','imglist':'imgUUID1,imgUUID2,...'},...]
         'sdUUID' - target domain UUID, if not None, VM Images and the master tree
                    must be located on this domain.
                    If sdUUID is None, the update is on the pool, and therefore the
                    master domain will be updated.
        """
        if sdUUID is None:
            sdUUID = self.masterDomain.sdUUID

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
                fileUtils.cleanupdir(vmPath, ignoreErrors = False)

            try:
                os.mkdir(vmPath)
                codecs.open(os.path.join(vmPath, vmUUID + '.ovf'), 'w',
                            encoding='utf8').write(ovf)
            except OSError, ex:
                if ex.errno == errno.ENOSPC:
                    raise se.NoSpaceLeftOnDomain(sdUUID)

                raise


    def removeVM(self, vmList, sdUUID=None):
        """
        Remove VMs.
         'vmList' - vmUUID1,vmUUID2,...
        """
        self.log.info("spUUID=%s vmList=%s sdUUID=%s", self.spUUID, str(vmList), sdUUID)
        vms = self._getVMsPath(sdUUID)
        vmUUIDs = vmList.split(',')
        for vm in vmUUIDs:
            if os.path.exists(os.path.join(vms, vm)):
                fileUtils.cleanupdir(os.path.join(vms, vm))


    def setDescription(self, descr):
        """
        Set storage pool description.
         'descr' - pool description
        """
        if len(descr) > MAX_POOL_DESCRIPTION_SIZE:
            raise se.StoragePoolDescriptionTooLongError()

        self.log.info("spUUID=%s descr=%s", self.spUUID, descr)
        self.setMetaParam(PMDK_POOL_DESCRIPTION, descr)


    def extendVolume(self, sdUUID, volumeUUID, size, isShuttingDown=None):
        sdCache.produce(sdUUID).extendVolume(volumeUUID, size, isShuttingDown)

    @classmethod
    def _getPoolMD(cls, domain):
        # This might look disgusting but this makes it so that
        # This is the only intrusion needed to satisfy the
        # unholy union between pool and SD metadata
        return DictValidator(domain._metadata._dict, SP_MD_FIELDS)

    @property
    def _metadata(self):
        return self._getPoolMD(self.masterDomain)

    @unsecured
    def getDescription(self):
        try:
            return self.getMetaParam(PMDK_POOL_DESCRIPTION)
            # There was a bug that cause pool description to
            # disappear. Returning "" might be ugly but it keeps
            # everyone happy.
        except KeyError:
            return ""

    @unsecured
    def getVersion(self):
        return self.masterDomain.getVersion()

    @unsecured
    def getSpmId(self):
        spmid = self.getMetaParam(PMDK_SPM_ID)
        if spmid != self.id or self.spmRole != SPM_FREE:
            return spmid

        # If we claim to be the SPM we have to be really sure we are
        self.invalidateMetadata()
        return self.getMetaParam(PMDK_SPM_ID)

    @unsecured
    def getInfo(self):
        """
        Get storage pool info.
        """
        ##self.log.info("Get info of the storage pool %s",
        ##    self.spUUID)
        if not self.spUUID:
            raise se.StoragePoolInternalError

        info = {'type': '', 'name': '', 'domains': '', 'master_uuid': '', 'master_ver': 0,
                'lver': -1, 'spm_id': -1, 'isoprefix': '', 'pool_status': 'uninitialized', 'version': -1}
        list_and_stats = {}

        msdUUID = self.masterDomain.sdUUID
        try:
            msdInfo = self.masterDomain.getInfo()
        except Exception:
            self.log.error("Couldn't read from master domain", exc_info=True)
            raise se.StoragePoolMasterNotFound(self.spUUID, msdUUID)

        try:
            info['type'] = msdInfo['type']
            info['domains'] = domainListEncoder(self.getDomains())
            info['name'] = self.getDescription()
            info['spm_id'] = self.getSpmId()
            info['lver'] = self.getMetaParam(PMDK_LVER)
            info['master_uuid'] = msdInfo['uuid']
            info['master_ver'] = self.getMasterVersion()
            info['version'] = str(self.getVersion())
        except Exception:
            self.log.error("Pool metadata error", exc_info=True)
            raise se.StoragePoolActionError(self.spUUID)


        # Get info of all pool's domains
        domDict = self.getDomains()
        repoStats = self.getRepoStats()
        for item in domDict:
            # Return statistics for active domains only
            stats = {}
            alerts = []
            if domDict[item] == sd.DOM_ACTIVE_STATUS:
                try:
                    dom = sdCache.produce(item)
                    if dom.isISO():
                        info['isoprefix'] = os.path.join(self.poolPath, item,
                                              sd.DOMAIN_IMAGES, sd.ISO_IMAGE_UUID)
                except:
                    self.log.warn("Could not get full domain information, it is probably unavailable", exc_info=True)

                if item in repoStats:
                    try:
                        # For unreachable domains repoStats will return disktotal/diskfree as None.
                        # We should drop these parameters in this case
                        if repoStats[item]['disktotal'] != None and repoStats[item]['diskfree'] != None:
                            stats['disktotal'] = repoStats[item]['disktotal']
                            stats['diskfree'] = repoStats[item]['diskfree']
                        if not repoStats[item]['mdavalid']:
                            alerts.append({'code':se.SmallVgMetadata.code,
                                           'message':se.SmallVgMetadata.message})
                            self.log.warning("VG %s's metadata size too small %s",
                                              dom.sdUUID, repoStats[item]['mdasize'])

                        if not repoStats[item]['mdathreshold']:
                            alerts.append({'code':se.VgMetadataCriticallyFull.code,
                                           'message':se.VgMetadataCriticallyFull.message})
                            self.log.warning("VG %s's metadata size exceeded critical size: \
                                             mdasize=%s mdafree=%s", dom.sdUUID,
                                             repoStats[item]['mdasize'], repoStats[item]['mdafree'])
                    except KeyError:
                        # We might have been asked to run before the first repoStats cycle was run
                        if item not in self.repostats:
                            self.log.warn("RepoStats is not active for active domain `%s`", item)

                        try:
                            stats.update(sdCache.produce(item).getStats())
                        except:
                            self.log.error("Could not get information for domain %s", item, exc_info=True)
                            # Domain is unavailable and we have nothing in the cache
                            # We need to return both of them or none
                            stats.pop('disktotal', None)
                            stats.pop('diskfree', None)
                    stats['alerts'] = alerts

            stats['status'] = domDict[item]
            list_and_stats[item] = stats

        info["pool_status"] = "connected"
        return dict(info=info, dominfo=list_and_stats)


    @unsecured
    def getIsoDomain(self):
        """
        Get pool's ISO domain if active
        """
        domDict = self.getDomains(activeOnly=True)
        for item in domDict:
            try:
                dom = sdCache.produce(item)
            except se.StorageDomainDoesNotExist :
               self.log.warn("Storage domain %s does not exist", item)
               continue

            if dom.isISO():
                return dom
        return None

    def setMetaParams(self, params):
        self._metadata.update(params)

    def setMetaParam(self, key, value):
        """
        Set key:value in pool metadata file
        """
        self._metadata[key] = value

    @unsecured
    def getMetaParam(self, key):
        """
        Get parameter from pool metadata file
        """
        return self._metadata[key]

    @unsecured
    def getMasterDomain(self, msdUUID, masterVersion):
        """
        Get the (verified) master domain of this pool.

        'msdUUID' - expected master domain UUID.
        'masterVersion' - expected pool msd version.
        """
        try:
            domain = sdCache.produce(msdUUID)
        except se.StorageDomainDoesNotExist:
            #Manager should start reconstructMaster if SPM.
            raise se.StoragePoolMasterNotFound(self.spUUID, msdUUID)

        if not domain.isMaster():
            self.log.error("Requested master domain %s is not a master domain at all", msdUUID)
            raise se.StoragePoolWrongMaster(self.spUUID, msdUUID)

        pools = domain.getPools()
        if (self.spUUID not in pools):
            self.log.error("Requested master domain %s does not belong to pool %s", msdUUID, self.spUUID)
            raise se.StoragePoolWrongMaster(self.spUUID, msdUUID)

        version = self._getPoolMD(domain)[PMDK_MASTER_VER]
        if version != int(masterVersion):
            self.log.error("Requested master domain %s does not have expected version %s it is version %s",
                        msdUUID, masterVersion, version)
            raise se.StoragePoolWrongMaster(self.spUUID, msdUUID)

        self.log.debug("Master domain %s verified, version %s", msdUUID, masterVersion)
        return domain


    @unsecured
    def invalidateMetadata(self):
        if not self.spmStarted:
            self._metadata.invalidate()

    @unsecured
    @misc.samplingmethod
    def updateMonitoringThreads(self):
        # domain list it's list of sdUUID:status
        # sdUUID1:status1,sdUUID2:status2,...
        self.invalidateMetadata()
        activeDomains = self.getDomains(activeOnly=True)
        monitoredDomains = self.repostats.keys()

        for sdUUID in monitoredDomains:
            if sdUUID not in activeDomains:
                try:
                    self.stopRepoStats(sdUUID)
                    self.log.debug("sp `%s` stopped monitoring domain `%s`" % (self.spUUID, sdUUID))
                except se.StorageException:
                    self.log.error("Unexpected error while trying to stop monitoring domain `%s`", sdUUID, exc_info=True)

        for sdUUID in activeDomains:
            if sdUUID not in monitoredDomains:
                try:
                    self.startRepoStats(sdUUID)
                    self.log.debug("sp `%s` started monitoring domain `%s`" % (self.spUUID, sdUUID))
                except se.StorageException:
                    self.log.error("Unexpected error while trying to monitor domain `%s`", sdUUID, exc_info=True)

    @unsecured
    def getDomains(self, activeOnly=False):
        return dict((sdUUID, status) \
               for sdUUID, status in self.getMetaParam(PMDK_DOMAINS).iteritems() \
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
    def getImageDomainsList(self, imgUUID, datadomains=True):
        """
        Get list of all domains in the pool that contain imgUUID
         'imgUUID' - image UUID
        """
        # TODO: get rid of this verb and let management query each domain separately
        #  the problem with current implementation is that when a domain is not accesible
        #  the error must be ignored and management can reach wrong conclusions.
        domainsdict = self.getDomains(activeOnly=True)
        domainslist = []

        for dom in domainsdict:
            try:
                d = sdCache.produce(dom)
            except Exception:
                # Pass over invisible active domains
                self.log.error("Unexpected error", exc_info=True)
                continue

            if datadomains and not d.isData():
                continue

            imageslist = d.getAllImages()
            if imgUUID in imageslist:
                domainslist.append(dom)

        return domainslist

    @unsecured
    def isMember(self, sdUUID, checkActive=False):
        """
        Check if domain is memeber in the pool.
        """
        return sdUUID in self.getDomains(activeOnly=checkActive)

    @unsecured
    def isActive(self, sdUUID):
        return sdUUID in self.getDomains(activeOnly=True)

    # TODO : move to sd.py
    @unsecured
    def _getVMsPath(self, sdUUID):
        """
        Return general path of VMs from the pool.
        If 'sdUUID' is given then return VMs dir within it.
        """
        if sdUUID and sdUUID != sd.BLANK_UUID:
            if not self.isActive(sdUUID):
                raise se.StorageDomainNotActive(sdUUID)
            vmPath = sdCache.produce(sdUUID).getVMsDir()
        # Get VMs path from the pool (from the master domain)
        else:
            vmPath = self.masterDomain.getVMsDir()

        if not os.path.exists(vmPath):
            raise se.VMPathNotExists(vmPath)
        return vmPath

    @unsecured
    def check(self):
        poolstatus = 0
        baddomains = {}
        message = "Pool OK"
        try:
            self.invalidateMetadata()
            spmId = self.getMetaParam(PMDK_SPM_ID)
            domains = self.getDomains(activeOnly=True)

            for dom in domains:
                d = sdCache.produce(dom)
                domstatus = d.checkDomain(spUUID=self.spUUID)
                if domstatus["domainstatus"] != 0:
                    baddomains[dom] = domstatus
                    poolstatus = se.StoragePoolCheckError.code
                    message = "Pool has bad domains"
        except se.StorageException, e:
            poolstatus = e.code
            message = str(e)
        except:
            poolstatus = se.StorageException.code
            message = "Pool is bad"

        return dict(poolstatus = poolstatus, baddomains = baddomains,
                    masterdomain = self.masterDomain.sdUUID, spmhost=spmId,
                    message = message)


    @unsecured
    def _repostats(self, domain):
        # self.selftest() should return True if things are looking good
        # and False otherwise
        stats = { 'disktotal' : None,
                  'diskfree' : None,
                  'masterValidate' : { 'mount' : False, 'valid' : False }
                }
        code = 0
        try:
            domain.selftest()

            res = domain.getStats()
            stats.update(res)
            # Add here more selftests if needed
            # Fill stats to get it back to the caller
            # Keys 'finish' and 'result' are reserved and may not be used
            stats['masterValidate'] = domain.validateMaster()
        except se.StorageException, e:
            code = e.code
        except (OSError, Timeout):
            code = se.StorageDomainAccessError.code

        return stats, code


    @unsecured
    def startRepoStats(self, sdUUID):
        statthread = self.repostats.get(sdUUID)
        if not statthread:
            statthread = StatsThread(self._repostats, sdUUID)
            statthread.start()
            self.repostats[sdUUID] = statthread
        self.log.debug("%s stat %s", sdUUID, statthread)


    @unsecured
    def stopRepoStats(self, domain):
        statthread = self.repostats.pop(domain, None)
        if statthread:
            statthread.stop()
        self.log.debug("%s stat %s", domain, statthread)


    @unsecured
    def getRepoStats(self):
        repostats = self.repostats.copy()
        result = {}
        for d in repostats:
            result[d] = repostats[d].getStatsResults()

        return result

    def copyImage(self, sdUUID, vmUUID, srcImgUUID, srcVolUUID, dstImgUUID,
                  dstVolUUID, descr, dstSdUUID, volType, volFormat, preallocate, postZero, force):
        """
        Creates a new template/volume from VM.
        It does this it by collapse and copy the whole chain (baseVolUUID->srcVolUUID).

        :param sdUUID: The UUID of the storage domain in which the image resides.
        :type sdUUID: UUID
        :param spUUID: The UUID of the storage pool in which the image resides.
        :type spUUID: UUID
        :param vmUUID: The UUID of the virtual machine you want to copy from.
        :type vmUUID: UUID
        :param srcImageUUID: The UUID of the source image you want to copy from.
        :type srcImageUUID: UUID
        :param srcVolUUID: The UUID of the source volume you want to copy from.
        :type srcVolUUID: UUID
        :param dstImageUUID: The UUID of the destination image you want to copy to.
        :type dstImageUUID: UUID
        :param dstVolUUID: The UUID of the destination volume you want to copy to.
        :type dstVolUUID: UUID
        :param descr: The human readable description of the new template.
        :type descr: str
        :param dstSdUUID: The UUID of the destination storage domain you want to copy to.
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

        :returns: a dict containing the UUID of the newly created image.
        :rtype: dict
        """
        srcImageResourcesNamespace = sd.getNamespace(sdUUID, IMAGE_NAMESPACE)
        if dstSdUUID not in [sdUUID, sd.BLANK_UUID]:
            dstImageResourcesNamespace = sd.getNamespace(dstSdUUID, IMAGE_NAMESPACE)
        else:
            dstImageResourcesNamespace = srcImageResourcesNamespace

        with nested(rmanager.acquireResource(srcImageResourcesNamespace, srcImgUUID, rm.LockType.shared),
                    rmanager.acquireResource(dstImageResourcesNamespace, dstImgUUID, rm.LockType.exclusive)):
            repoPath = os.path.join(self.storage_repository, self.spUUID)
            dstUUID = image.Image(repoPath).copy(sdUUID, vmUUID, srcImgUUID,
                                            srcVolUUID, dstImgUUID, dstVolUUID, descr, dstSdUUID,
                                            volType, volFormat, preallocate, postZero, force)
        return dict(uuid=dstUUID)


    def moveImage(self, srcDomUUID, dstDomUUID, imgUUID, vmUUID, op, postZero, force):
        """
        Moves or Copys an image between storage domains within same storage pool.

        :param spUUID: The storage pool where the operation will take place.
        :type spUUID: UUID
        :param srcDomUUID: The UUID of the storage domain you want to copy from.
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
        """
        srcImageResourcesNamespace = sd.getNamespace(srcDomUUID, IMAGE_NAMESPACE)
        dstImageResourcesNamespace = sd.getNamespace(dstDomUUID, IMAGE_NAMESPACE)
        # For MOVE_OP acqure exclusive lock
        # For COPY_OP shared lock is enough
        if op == image.MOVE_OP:
            srcLock = rm.LockType.exclusive
        elif op == image.COPY_OP:
            srcLock = rm.LockType.shared
        else:
            raise se.MoveImageError(imgUUID)

        with nested(rmanager.acquireResource(srcImageResourcesNamespace, imgUUID, srcLock),
                    rmanager.acquireResource(dstImageResourcesNamespace, imgUUID, rm.LockType.exclusive)):
            repoPath = os.path.join(self.storage_repository, self.spUUID)
            image.Image(repoPath).move(srcDomUUID, dstDomUUID, imgUUID, vmUUID, op, postZero, force)


    def moveMultipleImages(self, srcDomUUID, dstDomUUID, imgDict, vmUUID, force):
        """
        Moves multiple images between storage domains within same storage pool.

        :param spUUID: The storage pool where the operation will take place.
        :type spUUID: UUID
        :param srcDomUUID: The UUID of the storage domain you want to copy from.
        :type srcDomUUID: UUID
        :param dstDomUUID: The UUID of the storage domain you want to copy to.
        :type dstDomUUID: UUID
        :param imgDict: A dict of images in for form of ``{somthing:idunno}``
        :type imgDict: dict
        :param vmUUID: The UUID of the vm that owns the images.
        :type vmUUID: UUID
        :param force: Should the operation be forced.
        :type force: bool
        """
        srcImageResourcesNamespace = sd.getNamespace(srcDomUUID, IMAGE_NAMESPACE)
        dstImageResourcesNamespace = sd.getNamespace(dstDomUUID, IMAGE_NAMESPACE)

        imgList = imgDict.keys()
        imgList.sort()

        resourceList = []
        for imgUUID in imgList:
            resourceList.append(rmanager.acquireResource(srcImageResourcesNamespace, imgUUID, rm.LockType.exclusive))
            resourceList.append(rmanager.acquireResource(dstImageResourcesNamespace, imgUUID, rm.LockType.exclusive))

        with nested(*resourceList):
            repoPath = os.path.join(self.storage_repository, self.spUUID)
            image.Image(repoPath).multiMove(srcDomUUID, dstDomUUID, imgDict, vmUUID, force)


    def deleteImage(self, sdUUID, imgUUID, postZero, force):
        """
        Deletes an Image folder with all it's volumes.

        :param sdUUID: The UUID of the storage domain that contains the images.
        :type sdUUID: UUID
        :param imgUUID: The UUID of the image you want to delete.
        :type imgUUID: UUID
        :param postZero: ?
        :param force: Should the operation be forced.
        :type force: bool
        """
        volParams = None
        repoPath = os.path.join(self.storage_repository, self.spUUID)
        if sdCache.produce(sdUUID).isBackup():
            # Pre-delete requisites
            volParams = image.Image(repoPath).preDeleteHandler(sdUUID=sdUUID, imgUUID=imgUUID)

        # Delete required image
        image.Image(repoPath).delete(sdUUID=sdUUID, imgUUID=imgUUID, postZero=postZero, force=force)

        # We need create 'fake' image instead of deleted one
        if volParams:
            image.Image(repoPath).createFakeTemplate(sdUUID=sdUUID, volParams=volParams)


    def mergeSnapshots(self, sdUUID, vmUUID, imgUUID, ancestor, successor, postZero):
        """
        Merges the source volume to the destination volume.

        :param sdUUID: The UUID of the storage domain that contains the images.
        :type sdUUID: UUID
        :param spUUID: The UUID of the storage pool that contains the images.
        :type spUUID: UUID
        :param imgUUID: The UUID of the new image you will be created after the merge.?
        :type imgUUID: UUID
        :param ancestor: The UUID of the source volume.?
        :type ancestor: UUID
        :param successor: The UUID of the destination volume.?
        :type successor: UUID
        :param postZero: ?
        :type postZero: bool?
        """
        imageResourcesNamespace = sd.getNamespace(sdUUID, IMAGE_NAMESPACE)

        with rmanager.acquireResource(imageResourcesNamespace, imgUUID, rm.LockType.exclusive):
            repoPath = os.path.join(self.storage_repository, self.spUUID)
            image.Image(repoPath).merge(sdUUID, vmUUID, imgUUID, ancestor, successor, postZero)



    def createVolume(self, sdUUID, imgUUID, size, volFormat, preallocate, diskType, volUUID=None,
                     desc="", srcImgUUID=volume.BLANK_UUID, srcVolUUID=volume.BLANK_UUID):
        """
        Creates a new volume.

        .. note::
            If the *imgUUID* is **identical** to the *srcImgUUID* the new volume
            will be logically considered a snapshot of the old volume.
            If the *imgUUID* is **different** from the *srcImgUUID* the old volume
            will be logically considered a template of the new volume.

        :param sdUUID: The UUID of the storage domain that contains the volume.
        :type sdUUID: UUID
        :param imgUUID: The UUID of the image that id that the new volume will have.
        :type imgUUID: UUID
        :param size: The size of the new volume in bytes.
        :type size: int
        :param volFormat: The format of the new volume.
        :type volFormat: some enum ?!
        :param preallocate: Should the volume be preallocated.
        :type preallocate: bool
        :param diskType: The disk type of the new volume.
        :type diskType: some enum ?!
        :param volUUID: The UUID of the new volume that will be created.
        :type volUUID: UUID
        :param desc: A human readable description of the new volume.
        :param srcImgUUID: The UUID of the image that resides on the volume that will be the base of the new volume.
        :type srcImgUUID: UUID
        :param srcVolUUID: The UUID of the volume that will be the base of the new volume.
        :type srcVolUUID: UUID

        :returns: a dicts with the UUID of the new volume.
        :rtype: dict
        """
        imageResourcesNamespace = sd.getNamespace(sdUUID, IMAGE_NAMESPACE)

        with rmanager.acquireResource(imageResourcesNamespace, imgUUID, rm.LockType.exclusive):
            uuid = sdCache.produce(sdUUID).createVolume(imgUUID=imgUUID, size=size,
                                                    volFormat=volFormat, preallocate=preallocate,
                                                    diskType=diskType, volUUID=volUUID, desc=desc,
                                                    srcImgUUID=srcImgUUID, srcVolUUID=srcVolUUID)
        return dict(uuid=uuid)


    def deleteVolume(self, sdUUID, imgUUID, volumes, postZero, force):
        """
        Deletes a given volume.

        .. note::
            This function assumes:

                * If more than 1 volume, all volumes are a part of the **same** chain.
                * Given volumes are ordered, so predecessor is deleted before ancestor. ? (might be confused?)

        :param sdUUID: The UUID of the storage domain that contains the volume.
        :type sdUUID: UUID
        :param imgUUID: The UUID of the image that id that the new volume will have.
        :type imgUUID: UUID
        """
        imageResourcesNamespace = sd.getNamespace(sdUUID, IMAGE_NAMESPACE)

        with rmanager.acquireResource(imageResourcesNamespace, imgUUID, rm.LockType.exclusive):
            for volUUID in volumes:
                sdCache.produce(sdUUID).produceVolume(imgUUID, volUUID).delete(postZero=postZero,
                                                                           force=force)


    def setMaxHostID(self, spUUID, maxID):
        """
        Set maximum host ID
        """
        self.log.error("TODO: Implement")
        self._maxHostID
        self.spmMailer.setMaxHostID(maxID)
        raise se.NotImplementedException


    def detachAllDomains(self):
        """
        Detach all domains from pool before destroying pool
        """
        # First find out this pool master domain
        # Find out domain list from the pool metadata
        domList = self.getDomains().keys()

        for sdUUID in domList:
            # master domain should be detached last, after spm is stopped
            if sdUUID == self.masterDomain.sdUUID:
                continue
            self.detachSD(sdUUID=sdUUID, msdUUID=sd.BLANK_UUID, masterVersion=0)
        self.stopSpm(self.spUUID)
        # Forced detach 'master' domain after stopping SPM
        self.detachSD(self.masterDomain.sdUUID, sd.BLANK_UUID, 0, __securityOverride=True)

    def setVolumeDescription(self, sdUUID, imgUUID, volUUID, description):
        imageResourcesNamespace = sd.getNamespace(sdUUID, IMAGE_NAMESPACE)
        with rmanager.acquireResource(imageResourcesNamespace, imgUUID, rm.LockType.exclusive):
            sdCache.produce(sdUUID).produceVolume(imgUUID=imgUUID,
                                              volUUID=volUUID).setDescription(descr=description)

    def setVolumeLegality(self, sdUUID, imgUUID, volUUID, legality):
        imageResourcesNamespace = sd.getNamespace(sdUUID, IMAGE_NAMESPACE)
        with rmanager.acquireResource(imageResourcesNamespace, imgUUID, rm.LockType.exclusive):
            sdCache.produce(sdUUID).produceVolume(imgUUID=imgUUID,
                                              volUUID=volUUID).setLegality(legality=legality)

    def checkDomain(self, sdUUID):
        return sdCache.produce(sdUUID).checkDomain(spUUID=self.spUUID)

    def getVmsList(self, sdUUID=None):
        if sdUUID == None:
            dom = self.masterDomain
        else:
            dom = sdCache.produce(sdUUID)

        return dom.getVMsList()

    def getVmsInfo(self, sdUUID, vmList=None):
        return sdCache.produce(sdUUID).getVMsInfo(vmList=vmList)

    def uploadVolume(self, sdUUID, imgUUID, volUUID, srcPath, size, method="rsync"):
        vol = sdCache.produce(sdUUID).produceVolume(imgUUID, volUUID)
        if not vol.isLeaf():
            raise se.NonLeafVolumeNotWritable(vol)
        targetPath = vol.getVolumePath()
        if vol.isSparse():
            vol.extend(int(size))

        vol.prepare(rw=True, setrw=False)
        try:
            if method.lower() == "wget":
                cmd = [constants.EXT_WGET, "-O", targetPath, srcPath]
                (rc, out, err) = misc.execCmd(cmd, sudo=False)

                if rc:
                    self.log.error("uploadVolume - error while trying to retrieve: %s into: %s, stderr: %s" % (srcPath, targetPath, err))
                    raise se.VolumeCopyError(vol, err)
            #CR : should be elif 'rsync' and and else "error not supported" in the end
            else:
                cmd = [constants.EXT_RSYNC, "-aq", srcPath, targetPath]
                (rc, out, err) = misc.execCmd(cmd, sudo=False)

                if rc:
                    self.log.error("uploadVolume - error while trying to copy: %s into: %s, stderr: %s" % (srcPath, targetPath, err))
                    raise se.VolumeCopyError(vol, err)
        finally:
            try:
                vol.teardown(sdUUID, volUUID)
            except:
                self.log.warning("SP %s SD %s img %s Vol %s - teardown failed")

    def preDeleteRename(self, sdUUID, imgUUID):
        repoPath = os.path.join(self.storage_repository, self.spUUID)
        return image.Image(repoPath).preDeleteRename(sdUUID, imgUUID)

    def validateDelete(self, sdUUID, imgUUID):
        repoPath = os.path.join(self.storage_repository, self.spUUID)
        image.Image(repoPath).validateDelete(sdUUID, imgUUID)

    def validateImage(self, srcDomUUID, dstDomUUID, imgUUID, op=image.MOVE_OP):
        repoPath = os.path.join(self.storage_repository, self.spUUID)
        image.Image(repoPath).validate(srcDomUUID, dstDomUUID, imgUUID, op)

    def validateVolumeChain(self, sdUUID, imgUUID):
        repoPath = os.path.join(self.storage_repository, self.spUUID)
        image.Image(repoPath).validateVolumeChain(sdUUID, imgUUID)

    def checkImage(self, sdUUID, imgUUID):
        repoPath = os.path.join(self.storage_repository, self.spUUID)
        image.Image(repoPath).check(sdUUID, imgUUID)

    def extendSD(sdUUID, devlist):
        sdCache.produce(sdUUID).extend(devlist)

