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

"""
This is the Storage Pool Manager module.
Contains the SPM functionality.
"""


import os
import glob
import threading
import types
from config import config
from functools import partial
import errno
import signal

import time
import inspect
import constants
import storage_mailbox
import sp
import sd
import hsm
import blockSD
import image
from sdf import StorageDomainFactory as SDF
import volume
import misc
import logging
import storage_exception as se
from threadLocal import vars
from storageConstants import STORAGE
from resourceFactories import IMAGE_NAMESPACE
import resourceManager as rm
from contextlib import nested
import fileUtils
from processPool import Timeout

rmanager = rm.ResourceManager.getInstance()

# Global operation definitions
NOOP = 1
COPYOP = 2
MERGEOP = 3
IMPORTOP = 4
EXPORTOP = 5
MOVEOP = 6
STARTOP = 7
STOPOP = 8
CREATEVOP = 9
DELETEVOP = 10
DELOP = 11
MULTIMOVEOP = 12

MANUAL_RECOVERY_MODE = 0
SAFE_RECOVERY_MODE = 1
FAST_RECOVERY_MODE = 2

SPM_ACQUIRED = 'SPM'
SPM_CONTEND = 'Contend'
SPM_FREE = 'Free'

SECTOR_SIZE = 512

class Secure:
    """
        A wrapper class to execute a method securly. I have not idea what it does in unsafe mode. ??
    """
    safe = False
    log = logging.getLogger("Storage.SPM.Secure")

    @classmethod
    def setSafe(cls):
        """Sets the Protector class to *safe* mode."""
        cls.safe = True


    @classmethod
    def setUnsafe(cls):
        """Sets the Protector class to *unsafe* mode."""
        cls.safe = False


    def __init__(self, name, func):
        """
        Initializes a protector instance.

        :param name: The name of the function. For log purposes only.
        :type name: str
        :param func: The function to wrap.
        :type func: callable
        """
        self.name = str(name)
        self.func = func
        self.innerArgNames, args, kwargs, defValues = inspect.getargspec(func)
        self.help = getattr(func, "__doc__", None)
        if not self.help:
            self.help = "No help available for method %s" % name

    def run(self, *args, **kwargs):
        """
        Runs the wrapped method.

        :returns: Whatever the wrapped method returns.

        :raises: :exc:`storage_exception.SpmStatusError` if in unsafe mode and a non whitelisted method was trying to be run.
        """
        if self.safe:
            return self.func(*args, **kwargs)
        else:
            try:
                caller = inspect.stack()[1][3]
            except:
                caller = ""
            self.log.error("SPM: spm method call rejected: Not SPM!!!  method: %s, called by: %s" % (self.name, caller))
            raise se.SpmStatusError(self.name)


class SPM:
    """
    A class to manage a storage pool.

    .. attribute:: whitelist

        Contains a list of all functions which are allowed to run on an spm object when it is not started (not acting as an SPM for a pool).
    """

    storage_repository = config.get('irs', 'repository')
    lvExtendPolicy = config.get('irs', 'vol_extend_policy')
    lockCmd = config.get('irs', 'lock_cmd')

    # whitelist contains a list of all functions which are allowed to run on an
    # spm object when it is not started (not acting as an SPM for a pool)
    whitelist = ['start', 'public_getSpmStatus', 'isActive', '__cleanupSPM',
                'public_fenceSpmStorage',  '__releaseLocks', 'public_spmStop',
                '__cleanupMasterMount', '__cleanupSPMLinks',
                '_createSpmLinks', 'prepareForShutdown']

    log = logging.getLogger("Storage.SPM")
    lock = threading.Lock()

    def __init__(self, taskMng):
        """
        Initializes a new SPM.

        :param taskMng: The task manager of the pool.
        :type taskMng: :class:`taskManager.TaskManager`
        :param defExcFunc: A function to set the default exception for the SPM.
        :type defExcFunc: callable
        """
        Secure.setUnsafe()
        self.__overrideMethods()
        self.__cleanupSPMLinks()
        self.__cleanupMasterMount()
        self.__releaseLocks()

        self.taskMng = taskMng
        self.spmStarted = False
        self.pools = hsm.HSM.pools
        self.lver = 0
        self.spmRole = SPM_FREE
        self.tasksDir = None
        self._goingDown = False

        self._domainsToUpgrade = []


    def prepareForShutdown(self):
        """
        Prepare environments for system shutdown
        """
        # TBD: what about running tasks? persist and die?
        self.__cleanupMasterMount()
        self.__releaseLocks()
        self.__cleanupSPMLinks()

        # Stop spmMailer thread
        for spUUID in self.pools:
            if self.pools[spUUID].spmMailer:
                self.pools[spUUID].spmMailer.stop()
                self.pools[spUUID].spmMailer.tp.joinAll(waitForTasks=False)

    @classmethod
    def __cleanupMasterMount(cls):
        """
        Check whether there are any dangling master file systems still mounted
        and unmount them if found.
        """
        masters = os.path.join(cls.storage_repository, sd.DOMAIN_MNT_POINT,
                               sd.BLOCKSD_DIR, "*", sd.MASTER_FS_DIR)
        for master in glob.glob(masters):
            if fileUtils.isMounted(mountPoint=master):
                cls.log.debug("unmounting %s", master)
                try:
                    blockSD.BlockStorageDomain.doUnmountMaster(master)
                except se.StorageDomainMasterUnmountError, e:
                    misc.panic("unmount %s failed - %s" % (master, e))
            else:
                cls.log.debug("master `%s` is not mounted, skipping", master)

    @classmethod
    def __cleanupSPMLinks(cls):
        """
        Cleanup All SPM related links.
        """
        vms = glob.glob(os.path.join(cls.storage_repository, constants.UUID_GLOB_PATTERN, sd.VMS_DIR))
        tasks = glob.glob(os.path.join(cls.storage_repository, constants.UUID_GLOB_PATTERN, sd.TASKS_DIR))
        cls.log.debug("cleaning links; %s %s", vms, tasks)
        for d in vms:
            os.unlink(d)
        for d in tasks:
            os.unlink(d)


    def __overrideMethods(self):
        """
        Override class methods with object methods which do nothing -
        This protects against running SPM functions when we do not hold the SPM
        role.  This way there is no need to remember to add code to every new method
        we write
        """
        Secure.setUnsafe()

        mangledPrefix = "_" + self.__class__.__name__
        for funcName in dir(self):
            funcBareName = funcName # funcBareName contains the bare function name (if name mangling is used, the prefix is stripped from funcBareName)
            if funcName.startswith(mangledPrefix):
                funcBareName = funcName[len(mangledPrefix):]
            func = getattr(self, funcName)
            if funcBareName not in self.whitelist and callable(func):
               # Create a new entry in instance's "dict" that will mask the original method
                self.__dict__[funcName] = Secure(funcBareName, func).run


    def _schedule(self, name, func, *args):
        """
        Scheduler
        """
        self.lock.acquire()
        try:
            if not self.spmStarted or self._goingDown:
                raise se.SpmStatusError(name)
            self.taskMng.scheduleJob("spm", self.tasksDir, vars.task, name, func, *args)
        finally:
            self.lock.release()


    def isActive(self, contend=False):
        """
        Checks if a SPM is active.

        :param contend: ?
        :type contend: bool

        :returns: *True* if the SPM is active. *False* if it isn't.
        :rtype: bool
        """
        if contend and self.spmRole == SPM_CONTEND:
            return True
        return self.spmRole == SPM_ACQUIRED


    @classmethod
    def __releaseLocks(cls):
        """
        Releases all locks held by the machine.
        """
        # We are initializing the vdsm and should not be holding ANY lock
        # so we make sure no locks are held by the machine (e.g. because of previous vdsm runs)
        # killall -INT will trigger lock release (proper shutdown)
        try:
            misc.killall(cls.lockCmd, signal.SIGUSR1, group=True)
        except OSError, e:
            if e.errno == errno.ESRCH:
                return
            raise

        cls.log.warning("Found lease locks, releasing")
        for i in range(10):
            time.sleep(1)

            try:
                misc.killall(cls.lockCmd, 0)
            except OSError, e:
                if e.errno == errno.ESRCH:
                    return

        cls.log.warning("Could not release locks, killing lock processes")
        misc.killall(cls.lockCmd, signal.SIGKILL, group=True)


    def __cleanupSPM(self, pool):
        """
        Cleans up a pool.

        :param pool: The pool you want cleaned.
        :type pool: :class:`sd.StoragePool`
        """
        self.log.debug("cleaning up SPM: %s" % pool.spUUID)
        vmslink = os.path.join(pool.poolPath, sd.VMS_DIR)
        if os.path.lexists(vmslink):
            os.remove(vmslink)
        taskslink = os.path.join(pool.poolPath, sd.TASKS_DIR)
        if os.path.lexists(taskslink):
            os.remove(taskslink)
        self.tasksDir = None
        if  self.pools.has_key(pool.spUUID):
            if self.pools[pool.spUUID].spmMailer:
                self.pools[pool.spUUID].spmMailer.stop()


    def public_fenceSpmStorage(self, spUUID, lastOwner, lastLver, options = None):
        """
        Fences the SPM via the storage. ?
        Right now it just clears the owner and last ver fields.

        :param spUUID: The UUID of the storage pool you want to modify.
        :type spUUID: UUID
        :param lastOwner: obsolete
        :param lastLver: obsolete
        :param options: ?

        :returns: a dict containing the spms state?
        :rtype: dict
        """
        vars.task.setDefaultException(se.SpmFenceError("spUUID=%s, lastOwner=%s, lastLver=%s" % (str(spUUID), str(lastOwner), str(lastLver))))
        self.log.debug("spUUID=%s", spUUID)
        pool = hsm.HSM.getPool(spUUID)
        pool.invalidateMetadata()
        vars.task.getExclusiveLock(STORAGE, spUUID)
        # TODO: SCSI Fence the 'lastOwner'
        pool.setMetaParams({sp.PMDK_SPM_ID: -1, sp.PMDK_LVER: -1})
        self.spmRole = SPM_FREE
        st = {'spmStatus':self.spmRole, 'spmLver': -1, 'spmId':-1}
        self.log.debug("spUUID=%s: spmStatus=%s spmLver=%s spmId=%s",
                      spUUID, self.spmRole, -1, -1)
        return dict(spm_st=st)


    def public_upgradeStoragePool(self, spUUID, targetDomVersion):
        targetDomVersion = int(targetDomVersion)
        self._upgradePool(spUUID, targetDomVersion)
        return {"upgradeStatus" : "started"}

    def _upgradePool(self, spUUID, targetDomVersion):
        with rmanager.acquireResource(STORAGE, "upgrade_" + spUUID, rm.LockType.exclusive):
            if len(self._domainsToUpgrade) > 0:
                raise se.PoolUpgradeInProgress(spUUID)

            sd.validateDomainVersion(targetDomVersion)
            pool = hsm.HSM.getPool(spUUID)
            masterDom = pool.getMasterDomain()
            sdUUID = masterDom.sdUUID
            self.log.info("Trying to upgrade master domain `%s`", sdUUID)
            with rmanager.acquireResource(STORAGE, masterDom.sdUUID, rm.LockType.exclusive):
                masterDom.upgrade(targetDomVersion)

            self.log.debug("Marking all domains for upgrade")
            self._domainsToUpgrade = pool.getDomains(activeOnly=True).keys()
            try:
                self._domainsToUpgrade.remove(masterDom.sdUUID)
            except ValueError:
                pass

            self.log.debug("Registering with state change event")
            sp.StatsThread.onDomainConnectivityStateChange.register(self._upgradePoolDomain)
            self.log.debug("Running initial domain upgrade threads")
            for sdUUID in self._domainsToUpgrade:
                threading.Thread(target=self.__class__._upgradePoolDomain, args=(self, sdUUID, True)).start()

    def _upgradePoolDomain(self, sdUUID, isValid):
        # This method is called everytime the onDomainConnectivityStateChange
        # event is emited, this event is emited even when a domain goes INVALID
        # if this happens there is nothing for us to do no matter what the
        # domain is
        if not isValid:
            return

        domain = SDF.produce(sdUUID)
        if sdUUID not in self._domainsToUpgrade:
            return

        self.log.debug("Preparing to upgrade domain %s", sdUUID)

        try:
            #Assumed that the domain can be attached only to one pool
            poolUUID = domain.getPools()[0]
            pool = hsm.HSM.getPool(poolUUID)
            masterDom = pool.getMasterDomain()
            targetDomVersion = masterDom.getVersion()
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
                        sp.StatsThread.onDomainConnectivityStateChange.unregister(self._upgradePoolDomain)
                    except KeyError:
                        pass


    def start(self, spUUID, prevID, prevLVER, recoveryMode, scsiFencing, maxHostID, expectedDomVersion=None):
        """
        Starts the SPM functionality.

        :param spUUID: The UUID of the storage pool you want to manage with the SPM.
        :type spUUID: UUID
        :param prevID: obsolete
        :param prevLVER: obsolete
        :param recoveryMode: One of the following:

                             * Manual - ?
                             * Safe - ?
                             * Fast - ?

        :type recoveryMode: str?
        :param scsiFencing: Should there be scsi fencing.?
        :type scsiFencing: bool
        :param maxHostID: The maximun ID of the host.?
        :type maxHostID: int

        .. note::
            if the SPM is already started the function will fail silently.

        :raises: :exc:`storage_exception.OperationInProgress` if called during an allready running connection attempt.
                 (makes the fact that it fails silently does not matter very much).
        """
        self.log.debug("spUUID=%s", spUUID)
        self.lock.acquire()
        try:
            if self.spmRole == SPM_ACQUIRED:
                return True
            # Since we added the lock the following should NEVER happen
            if self.spmRole == SPM_CONTEND:
                raise se.OperationInProgress("spm start %s" % spUUID)

            pool = hsm.HSM.getPool(spUUID)
            pool.updateMonitoringThreads()
            pool.invalidateMetadata()
            masterDom = pool.getMasterDomain()
            oldlver = pool.getMetaParam(sp.PMDK_LVER)
            oldid = pool.getMetaParam(sp.PMDK_SPM_ID)
            masterDomVersion = pool.getVersion()
            # If no specific domain version was specified use current master domain version
            if expectedDomVersion is None:
                expectedDomVersion = masterDomVersion

            if masterDomVersion > expectedDomVersion:
                raise se.CurrentVersionTooAdvancedError(masterDom.sdUUID,
                        curVer=masterDomVersion, expVer=expectedDomVersion)

            if int(oldlver) != int(prevLVER) or int(oldid) != int(prevID):
                self.log.info("expected previd:%s lver:%s got request for previd:%s lver:%s" % (oldid, oldlver, prevID, prevLVER))


            # Acquire spm lock
            try:
                self.spmRole = SPM_CONTEND
                pool.acquireClusterLock()
            except:
                self.spmRole = SPM_FREE
                raise

            self.log.debug("spm lock acquired successfully")

            try:
                self.lver = int(oldlver) + 1
                self.pools[spUUID] = pool

                pool.invalidateMetadata()
                pool.setMetaParams({sp.PMDK_LVER: self.lver,
                    sp.PMDK_SPM_ID: pool.id})
                self.pools[spUUID]._maxHostID = maxHostID

                # Upgrade the master domain now if needed
                self.__class__._upgradePool(self, spUUID, expectedDomVersion)

                masterDom.mountMaster()
                masterDom.createMasterTree(log=True)
                self._createSpmLinks(pool.poolPath)

                try:
                    # Make sure backup domain is active
                    pool.checkBackupDomain()
                except Exception, e:
                    self.log.error("Backup domain validation failed, exc_info=True")

                self.taskMng.loadDumpedTasks(self.tasksDir)

                self.spmStarted = True
                self.spmRole = SPM_ACQUIRED

                # Once setSafe completes we are running as SPM
                Secure.setSafe()

                # Mailbox issues SPM commands, therefore we start it AFTER spm commands are allowed to run to prevent
                # a race between the mailbox and the "Secure.setSafe() call"

                # Create mailbox if SAN pool (currently not needed on nas)
                # FIXME: Once pool contains mixed type domains (NFS + Block) the mailbox
                # will have to be created if there is an active block domain in the pool
                # or once one is activated

                #FIXME : Use a system wide grouping mechanizm
                sanPool = masterDom.getStorageType() in sd.BLOCK_DOMAIN_TYPES  # Check if pool is SAN or NAS
                if sanPool and self.lvExtendPolicy == "ON":
                    self.pools[spUUID].spmMailer = storage_mailbox.SPM_MailMonitor(self, spUUID, maxHostID)
                else:
                    self.pools[spUUID].spmMailer = None

                # Restore tasks is last because tasks are spm ops (spm has to be started)
                self.taskMng.recoverDumpedTasks()

                self.log.debug("ended.")

            except Exception, e:
                self.log.error("Unexpected error", exc_info=True)
                self.log.error("failed: %s" % str(e))
                self.__class__._stop(self, spUUID)
                raise
        finally:
            self.lock.release()


    def _createSpmLinks(self, poolPath):
        """
        Create links on SPM host
        """
        vmslink = os.path.join(poolPath, sd.VMS_DIR)
        if os.path.lexists(vmslink):
            os.remove(vmslink)
        vms = os.path.join(sp.POOL_MASTER_DOMAIN, sd.MASTER_FS_DIR, sd.VMS_DIR)
        os.symlink(vms, vmslink)

        taskslink = os.path.join(poolPath, sd.TASKS_DIR)
        if os.path.lexists(taskslink):
            os.remove(taskslink)
        tasks = os.path.join(sp.POOL_MASTER_DOMAIN, sd.MASTER_FS_DIR, sd.TASKS_DIR)
        os.symlink(tasks, taskslink)
        self.tasksDir = taskslink


    def public_spmStop(self, spUUID, options = None):
        """
        Stops the SPM functionality.

        :param spUUID: The UUID of the storage pool you want to stop it manager.
        :type spUUID: UUID
        :param options: ?

        :raises: :exc:`storage_exception.TaskInProgress` if there are tasks runnning for this pool.

        """
        #spUUID is redundant and should be removed.
        vars.task.setDefaultException(se.SpmStopError(spUUID))
        self.log.debug("spUUID=%s", spUUID)

        # Get lock to prevent new spm async tasks from starting
        self.lock.acquire()
        try:
            # This validation must come first to ensure spmStop returns successfully
            # if we are not currently the spm.  This is needed for RHEV-M to
            # function properly.
            if not self.spmStarted:
                return True

            #Validates that we are connected to the spUUID pool.
            #Ideally should be checked 1st, but we're preserving semantics.
            #This is different from _stop() semantics.
            hsm.HSM.getPool(spUUID)

            dictTasks = self.taskMng.getAllTasks(tag="spm")
            for taskKey in dictTasks:
                if not dictTasks[taskKey].isDone():
                    raise se.TaskInProgress(spUUID, str(taskKey))

            self._goingDown = True
        finally:
            self.lock.release()

        try:
            vars.task.getExclusiveLock(STORAGE, spUUID)
        finally:
            self._goingDown = False

        self._stop(spUUID)


    def _stop(self, spUUID):
        """
        Stop SPM
        """
        with rmanager.acquireResource(STORAGE, "upgrade_" + spUUID, rm.LockType.exclusive):
            domains = self._domainsToUpgrade
            try:
                sp.StatsThread.onDomainConnectivityStateChange.unregister(self._upgradePoolDomain)
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


        stopFailed = False
        self.lver = 0
        try:
            pool = hsm.HSM.getPool(spUUID)
        except se.StoragePoolUnknown:
            self.log.warning("Pool %s not found in cache", spUUID)


        Secure.setUnsafe()

        # Clean all spm tasks from memory (so they are not accessible)
        try:
            self.taskMng.unloadTasks(tag="spm")
        except:
            stopFailed = True

        try:
            self.__cleanupMasterMount()
        except:
            # If unmounting fails the vdsm panics.
            stopFailed = True

        try:
            self.__cleanupSPM(pool)
        except:
            # Here we are just begin polite.
            # SPM will also clean this on start up.
            pass

        if not stopFailed:
            try:
                pool.setMetaParam(sp.PMDK_SPM_ID, -1)
            except:
                pass # The system can handle this inconsistency

        try:
            pool.releaseClusterLock()
        except:
            stopFailed = True

        if stopFailed:
            misc.panic("Unrecoverable errors during SPM stop process.")

        self.spmStarted = False
        self.spmRole = SPM_FREE


    def public_getSpmStatus(self, spUUID, options = None):
        """
        Gets the status of the SPM.

        :param spUUID: The UUID of the storage pool you want to get it's SPM status.
        :type spUUID: UUID
        :param options: ?

        :returns: a dict containing the status of the SPM.
        :rtype: dict

        :raises: :exc:`storage_exception.MetaDataParamError` if the metata data on the pool is invalid.
        """
        pool = hsm.HSM.getPool(spUUID)
        try:
            #If this is the SPM no need to double check
            if self.spmRole == SPM_FREE:
                spmId = pool.getSpmId()
            else:
                spmId = pool.getMetaParam(sp.PMDK_SPM_ID)

            lver = pool.getMetaParam(sp.PMDK_LVER)
        except se.LogicalVolumeRefreshError:
            # This happens when we cannot read the MD LV
            raise se.CannotRetrieveSpmStatus()
        except se.StorageException:
            self.log.error("Unexpected error", exc_info=True)
            raise
        except Exception:
            self.log.error("Unexpected error", exc_info=True)
            raise se.MetaDataParamError("Version or spm id invalid")

        self.log.debug("spUUID=%s: spmStatus=%s spmLver=%s spmId=%s",
                      spUUID, self.spmRole, lver, spmId)

        status = dict(spmStatus=self.spmRole, spmLver=lver, spmId=spmId)
        return dict(spm_st=status)


    def copyImage(self, sdUUID, spUUID, vmUUID, srcImgUUID, srcVolUUID, dstImgUUID,
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
            repoPath = os.path.join(self.storage_repository, spUUID)
            dstUUID = image.Image(repoPath).copy(sdUUID, vmUUID, srcImgUUID,
                                            srcVolUUID, dstImgUUID, dstVolUUID, descr, dstSdUUID,
                                            volType, volFormat, preallocate, postZero, force)
        return dict(uuid=dstUUID)


    def moveImage(self, spUUID, srcDomUUID, dstDomUUID, imgUUID, vmUUID, op, postZero, force):
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
            repoPath = os.path.join(self.storage_repository, spUUID)
            image.Image(repoPath).move(srcDomUUID, dstDomUUID, imgUUID, vmUUID, op, postZero, force)


    def moveMultipleImages(self, spUUID, srcDomUUID, dstDomUUID, imgDict, vmUUID, force):
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
            repoPath = os.path.join(self.storage_repository, spUUID)
            image.Image(repoPath).multiMove(srcDomUUID, dstDomUUID, imgDict, vmUUID, force)


    def deleteImage(self, sdUUID, spUUID, imgUUID, postZero, force):
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
        repoPath = os.path.join(self.storage_repository, spUUID)
        if SDF.produce(sdUUID).isBackup():
            # Pre-delete requisites
            volParams = image.Image(repoPath).preDeleteHandler(sdUUID=sdUUID, imgUUID=imgUUID)

        # Delete required image
        image.Image(repoPath).delete(sdUUID=sdUUID, imgUUID=imgUUID, postZero=postZero, force=force)

        # We need create 'fake' image instead of deleted one
        if volParams:
            image.Image(repoPath).createFakeTemplate(sdUUID=sdUUID, volParams=volParams)


    def mergeSnapshots(self, sdUUID, spUUID, vmUUID, imgUUID, ancestor, successor, postZero):
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
            repoPath = os.path.join(self.storage_repository, spUUID)
            image.Image(repoPath).merge(sdUUID, vmUUID, imgUUID, ancestor, successor, postZero)


    def public_extendVolume(self, sdUUID, spUUID, imgUUID, volumeUUID, size, isShuttingDown=None, options=None):
        """
        Extends an existing volume.

        .. note::
            This method is valid for SAN only.

        :param sdUUID: The UUID of the storage domain that contains the volume.
        :type sdUUID: UUID
        :param spUUID: The UUID of the storage pool that contains the volume.
        :type spUUID: UUID
        :param imgUUID: The UUID of the new image that is contained on the volume.
        :type imgUUID: UUID
        :param volumeUUID: The UUID of the volume you want to extend.
        :type volumeUUID: UUID
        :param size: Target volume size in MB (desired final size, not by how much to increase)
        :type size: number (anything parsable by int(size))
        :param isShuttingDown: ?
        :type isShuttingDown: bool
        :param options: ?
        """
        vars.task.setDefaultException(se.VolumeExtendingError("spUUID=%s, sdUUID=%s, volumeUUID=%s, size=%s" % (
                                                        str(spUUID), str(sdUUID), str(volumeUUID), str(size))))
        hsm.HSM.validatePoolSD(spUUID, sdUUID)
        size = misc.validateN(size, "size")
        # ExtendVolume expects size in MB
        size = (size + 2**20 - 1) / 2**20

        vars.task.getSharedLock(STORAGE, sdUUID)
        pool = hsm.HSM.getPool(spUUID)
        pool.extendVolume(sdUUID, volumeUUID, size, isShuttingDown)

    def public_extendStorageDomain(self, sdUUID, spUUID, devlist, options = None):
        """
        Extends a VG. ?

        .. note::
            Currently the vg must be a storage domain.

        :param sdUUID: The UUID of the storage domain that owns the VG.
        :type sdUUID: UUID
        :param spUUID: The UUID of the storage pool that owns the VG.
        :type spUUID: UUID
        :param devlist: The list of devices you want to extend the VG to. ?
        :type devlist: list of devices. ``[dev1, dev2]``. ?
        :param options: ?
        """
        vars.task.setDefaultException(se.StorageDomainActionError("sdUUID=%s, devlist=%s" % (str(sdUUID), str(devlist))))

        hsm.HSM.validatePoolSD(spUUID, sdUUID)
        vars.task.getExclusiveLock(STORAGE, sdUUID)
        # We need to let the domain to extend itself
        SDF.produce(sdUUID).extend(devlist)


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
            uuid = SDF.produce(sdUUID).createVolume(imgUUID=imgUUID, size=size,
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
                SDF.produce(sdUUID).produceVolume(imgUUID, volUUID).delete(postZero=postZero,
                                                                           force=force)


    def setMaxHostID(self, spUUID, maxID):
        """
        Set maximum host ID
        """
        self.log.error("TODO: Implement")
        self.pools[spUUID]._maxHostID
        self.pools[spUUID].spmMailer.setMaxHostID(maxID)
        raise se.NotImplementedException


    def public_forcedDetachStorageDomain(self, sdUUID, spUUID, options = None):
        """Forced detach a storage domain from a storage pool.
           This removes the storage domain entry in the storage pool meta-data
           and leaves the storage domain in 'unattached' status.
           This action can only be performed on regular (i.e. non master) domains
        """
        vars.task.setDefaultException(se.StorageDomainActionError("sdUUID=%s, spUUID=%s" % (str(sdUUID), str(spUUID))))
        vars.task.getExclusiveLock(STORAGE, spUUID)
        pool = hsm.HSM.getPool(spUUID)
        if sdUUID == pool.getMasterDomain().sdUUID:
            raise se.CannotDetachMasterStorageDomain(sdUUID)
        pool.forcedDetachSD(sdUUID)


    def public_detachStorageDomain(self, sdUUID, spUUID, msdUUID, masterVersion, options = None):
        """
        Detachs a storage domain from a storage pool.
        This removes the storage domain entry in the storage pool meta-data
        and leaves the storage domain in 'unattached' status.

        :param sdUUID: The UUID of the storage domain that you want to detach.
        :type sdUUID: UUID
        :param spUUID: The UUID of the storage pool that contains the storage domain being detached.
        :type spUUID: UUID
        :param msdUUID: The UUID of the master domain.
        :type msdUUID: UUID
        :param masterVersion: The version of the pool.
        :type masterVersion: int
        :param options: ?
        """
        vars.task.setDefaultException(se.StorageDomainActionError("sdUUID=%s, spUUID=%s, msdUUID=%s, masterVersion=%s" % (str(sdUUID), str(spUUID), str(msdUUID), str(masterVersion))))
        hsm.HSM.validatePoolSD(spUUID, sdUUID)

        vars.task.getExclusiveLock(STORAGE, spUUID)
        vars.task.getExclusiveLock(STORAGE, sdUUID)
        pool = hsm.HSM.getPool(spUUID)
        pool.detachSD(sdUUID, msdUUID, masterVersion)


    def detachAllDomains(self, pool):
        """
        Detach all domains from pool before destroying pool
        """
        # First find out this pool master domain
        mDom = pool.getMasterDomain()
        # Find out domain list from the pool metadata
        domList = pool.getDomains().keys()

        for sdUUID in domList:
            # master domain should be detached last, after spm is stopped
            if sdUUID == mDom.sdUUID:
                continue
            pool.detachSD(sdUUID=sdUUID, msdUUID=sd.BLANK_UUID, masterVersion=0)
        self._stop(pool.spUUID)
        # Forced detach 'master' domain after stopping SPM
        pool.detachSD(mDom.sdUUID, sd.BLANK_UUID, 0)


    def public_attachStorageDomain(self, sdUUID, spUUID, options = None):
        """
        Attach a storage domain to a storage pool.
        This marks the storage domain as status 'attached' and link it to the storage pool

        .. note::
            The target domain must be accessible in this point (storage connected)

        :param sdUUID: The UUID of the storage domain that you want to attach.
        :type sdUUID: UUID
        :param spUUID: The UUID of the storage pool that contains the storage domain being attached.
        :type spUUID: UUID
        :param options: ?
        """
        vars.task.setDefaultException(se.StorageDomainActionError("sdUUID=%s, spUUID=%s" % (str(sdUUID), str(spUUID))))

        vars.task.getExclusiveLock(STORAGE, spUUID)
        vars.task.getExclusiveLock(STORAGE, sdUUID)
        pool = hsm.HSM.getPool(spUUID)
        try:
            hsm.HSM.validateSdUUID(sdUUID)
        except:
            pool.refresh()
            hsm.HSM.validateSdUUID(sdUUID)
        pool = hsm.HSM.getPool(spUUID)
        pool.attachSD(sdUUID)


    def public_deactivateStorageDomain(self, sdUUID, spUUID, msdUUID, masterVersion, options = None):
        """
        1. Deactivates a storage domain.
        2. Validates that the storage domain is owned by the storage pool.
        3. Disables access to that storage domain.
        4. Changes storage domain status to 'Inactive' in the storage pool meta-data.

        .. note::
            Disconnected storage domains are not monitored by the host.

        :param sdUUID: The UUID of the storage domain that you want to deactivate.
        :type sdUUID: UUID
        :param spUUID: The UUID of the storage pool that contains the storage domain being deactivated.
        :type spUUID: UUID
        :param msdUUID: The UUID of the master domain.
        :type msdUUID: UUID
        :param masterVersion: The version of the pool.
        :type masterVersion: int
        :param options: ?
        """
        vars.task.setDefaultException(
            se.StorageDomainActionError(
                "sdUUID=%s, spUUID=%s, msdUUID=%s, masterVersion=%s" %
                (str(sdUUID), str(spUUID), str(msdUUID), str(masterVersion))
            )
        )
        hsm.HSM.validatePoolSD(spUUID, sdUUID)

        vars.task.getExclusiveLock(STORAGE, spUUID)
        vars.task.getExclusiveLock(STORAGE, sdUUID)
        pool = hsm.HSM.getPool(spUUID)
        pool.deactivateSD(sdUUID, msdUUID, masterVersion)


    def public_activateStorageDomain(self, sdUUID, spUUID, options = None):
        """
        1. Activates a storage domain that is already a member in a storage pool.
        2. Validates that the storage domain is owned by the storage pool.

        .. note::
            The target domain must be accessible in this point (storage connected).

        :param sdUUID: The UUID of the storage domain that you want to activate.
        :type sdUUID: UUID
        :param spUUID: The UUID of the storage pool that contains the storage domain being activated.
        :type spUUID: UUID
        :param options: ?
        """
        vars.task.setDefaultException(se.StorageDomainActionError("sdUUID=%s, spUUID=%s" % (str(sdUUID), str(spUUID))))

        vars.task.getExclusiveLock(STORAGE, spUUID)
        vars.task.getExclusiveLock(STORAGE, sdUUID)
        pool = hsm.HSM.getPool(spUUID)
        try:
            hsm.HSM.validateSdUUID(sdUUID)
            hsm.HSM.validatePoolSD(spUUID, sdUUID)
        except Timeout:
            self.log.error("Timeout reached activating storage domain "
                           "sdUUID=%s, spUUID=%s", sdUUID, spUUID)
            raise se.StorageDomainActivateError(sdUUID)
        except Exception:
            self.log.warn("Could not validate storage domain sdUUID=%s, "
                           "spUUID=%s, refreshing and retrying", sdUUID, spUUID,
                           exc_info=True)
            pool.refresh()
            hsm.HSM.validateSdUUID(sdUUID)
            hsm.HSM.validatePoolSD(spUUID, sdUUID)
        pool.activateSD(sdUUID)


    def public_setStoragePoolDescription(self, spUUID, description, options = None):
        """
        Sets the storage pool's description.

        :param spUUID: The UUID of the storage pool that you want to set it's description.
        :type spUUID: UUID
        :param description: A human readable description of the storage pool.
        :type description: str
        :param options: ?
        """
        vars.task.setDefaultException(se.StoragePoolActionError("spUUID=%s, descr=%s" % (str(spUUID), str(description))))
        vars.task.getExclusiveLock(STORAGE, spUUID)
        pool = hsm.HSM.getPool(spUUID)
        pool.setDescription(description)


    def public_setVolumeDescription(self, sdUUID, spUUID, imgUUID, volUUID, description, options = None):
        """
        Sets a Volume's Description

        :param spUUID: The UUID of the storage pool that contains the volume being modified.
        :type spUUID: UUID
        :param sdUUID: The UUID of the storage domain that contains the volume.
        :type sdUUID: UUID
        :param imgUUID: The UUID of the image that is contained on the volume.
        :type imgUUID: UUID
        :param volUUID: The UUID of the volume you want to modify.
        :type volUUID: UUID
        :param description: The new human readable description of the volume.
        :type description: str
        """
        hsm.HSM.validatePoolSD(spUUID, sdUUID)

        vars.task.getSharedLock(STORAGE, sdUUID)
        imageResourcesNamespace = sd.getNamespace(sdUUID, IMAGE_NAMESPACE)

        with rmanager.acquireResource(imageResourcesNamespace, imgUUID, rm.LockType.exclusive):
            SDF.produce(sdUUID).produceVolume(imgUUID=imgUUID,
                                              volUUID=volUUID).setDescription(descr=description)


    def public_setVolumeLegality(self, sdUUID, spUUID, imgUUID, volUUID, legality, options = None):
        """
        Sets a Volume's Legality

        :param spUUID: The UUID of the storage pool that contains the volume being modified.
        :type spUUID: UUID
        :param sdUUID: The UUID of the storage domain that contains the volume.
        :type sdUUID: UUID
        :param imgUUID: The UUID of the image that is contained on the volume.
        :type imgUUID: UUID
        :param volUUID: The UUID of the volume you want to modify.
        :type volUUID: UUID
        :param description: The legality status ot the volume.?
        :type description: ?
        """
        hsm.HSM.validatePoolSD(spUUID, sdUUID)

        vars.task.getSharedLock(STORAGE, sdUUID)
        imageResourcesNamespace = sd.getNamespace(sdUUID, IMAGE_NAMESPACE)

        with rmanager.acquireResource(imageResourcesNamespace, imgUUID, rm.LockType.exclusive):
            SDF.produce(sdUUID).produceVolume(imgUUID=imgUUID,
                                              volUUID=volUUID).setLegality(legality=legality)


    def public_updateVM(self, spUUID, vmList, sdUUID=None, options = None):
        """
        Updates a VM list in a storage pool or in a Backup domain.
        Creates the VMs if a domain with the specified UUID does not exist.

        .. note::
            Should be called by VDC for every change of VM (add or remove snapshots, updates, ...)

        :param spUUID: The UUID of the storage pool that contains the VMs being updated or created.
        :type spUUID: UUID
        :param vmList: The list of VMs being updated.?
        :type vmList: list
        :param sdUUID: The UUID of the backup domain you want to update or :keyword:`None` if you want something something. ?
        :type sdUUID: UUID
        :param options: ?
        """
        if sdUUID and sdUUID != sd.BLANK_UUID:
            hsm.HSM.validatePoolSD(spUUID, sdUUID)
            hsm.HSM.validateSdUUID(sdUUID)
        #getSharedLock(spUUID...)
        vars.task.getSharedLock(STORAGE, spUUID)
        #getExclusiveLock(vmList...)
        pool = hsm.HSM.getPool(spUUID)
        pool.updateVM(vmList=vmList, sdUUID=sdUUID)


    def public_removeVM(self, spUUID, vmList, sdUUID=None, options = None):
        """
        Removes a VM list from a storage pool or from a Backup domain.

        :param spUUID: The UUID of the storage pool that contains the VMs being removed.
        :type spUUID: UUID
        :param vmList: The list of VMs being removed.?
        :type vmList: list
        :param sdUUID: The UUID of the backup domain you want to update or :keyword:`None` if you want something something. ?
        :type sdUUID: UUID
        :param options: ?
        """
        if sdUUID and sdUUID != sd.BLANK_UUID:
            hsm.HSM.validatePoolSD(spUUID, sdUUID)
            hsm.HSM.validateSdUUID(sdUUID)
        #getSharedLock(spUUID...)
        vars.task.getSharedLock(STORAGE, spUUID)
        #getExclusiveLock(vmList...)
        pool = hsm.HSM.getPool(spUUID)
        pool.removeVM(vmList=vmList, sdUUID=sdUUID)

    def public_checkImage(self, sdUUID, spUUID, imgUUID, options = None):
        """
        Check an image. Why? For what?

        :param sdUUID: The UUID of the storage domain that contains the image being checked.
        :type sdUUID: UUID
        :param spUUID: The UUID of the storage pool that contains image being checked.
        :type spUUID: UUID
        :param imgUUID: The UUID of the image you want to check.
        :type imgUUID: UUID
        :param options: ?
        """
        hsm.HSM.validatePoolSD(spUUID, sdUUID)
        hsm.HSM.validateSdUUID(sdUUID)
        repoPath = os.path.join(self.storage_repository, spUUID)
        return image.Image(repoPath).check(sdUUID=sdUUID, imgUUID=imgUUID)

    def public_checkDomain(self, sdUUID, spUUID, options = None):
        """
        Check a domain. Why? For what?

        :param sdUUID: The UUID of the storage domain that the you want to checked.
        :type sdUUID: UUID
        :param spUUID: The UUID of the storage pool that contains domain being checked.
        :type spUUID: UUID
        :param options: ?
        """
        hsm.HSM.validatePoolSD(spUUID, sdUUID)
        hsm.HSM.validateSdUUID(sdUUID)
        return SDF.produce(sdUUID).checkDomain(spUUID=spUUID)


    def public_checkPool(self, spUUID, options = None):
        """
        Check a domain. Why? For what?

        :param spUUID: The UUID of the storage pool that contains domain being checked.
        :type spUUID: UUID
        :param options: ?
        """
        pool = hsm.HSM.getPool(spUUID)
        return pool.check()

    def public_getVmsList(self, spUUID, sdUUID=None, options = None):
        """
        Gets a list of VMs from the pool.
        If 'sdUUID' is given and it's a bakup domain the function will get the list of VMs from it

        :param spUUID: The UUID of the storage pool that you want to query.
        :type spUUID: UUID
        :param sdUUID: The UUID of the backup domain that the you want to query or :keyword:`None`.
        :type sdUUID: UUID
        :param options: ?
        """
        if sdUUID and sdUUID != sd.BLANK_UUID:
            hsm.HSM.validatePoolSD(spUUID, sdUUID)
            hsm.HSM.validateSdUUID(sdUUID)
        vars.task.getSharedLock(STORAGE, sdUUID)

        if sdUUID == None:
            dom = self.getPool(spUUID).getMasterDomain()
        else:
            dom = SDF.produce(sdUUID)

        vms = dom.getVMsList()
        return dict(vmlist=vms)

    def public_getVmsInfo(self, spUUID, sdUUID=None, vmList=None, options = None):
        """
        Gets a list of VMs with their info from the pool.

        * If 'sdUUID' is given and it's a bakup domain then get the list of VMs from it.
        * If 'vmList' is given get info for these VMs only.

        :param spUUID: The UUID of the storage pool that you want to query.
        :type spUUID: UUID
        :param sdUUID: The UUID of the backup domain that the you want to query or :keyword:`None`.
        :type sdUUID: UUID
        :param vmList: A UUID list of the VMs you want info on or :keyword:`None` for all VMs in pool or backup domain.
        :param options: ?
        """
        if sdUUID and sdUUID != sd.BLANK_UUID:
            hsm.HSM.validatePoolSD(spUUID, sdUUID)
            # Only backup domains are allowed in this path
            hsm.HSM.validateBackupDom(sdUUID)
        vars.task.getSharedLock(STORAGE, sdUUID)
        vms = SDF.produce(sdUUID).getVMsInfo(vmList=vmList)
        return dict(vmlist=vms)

    def public_uploadVolume(self, sdUUID, spUUID, imgUUID, volUUID, srcPath, size, method="rsync", options = None):
        """
        Uploads a volume to the server. (NFS only?)

        :param spUUID: The UUID of the storage pool that will contain the new volume.
        :type spUUID: UUID
        :param sdUUID: The UUID of the backup domain that will contain the new volume.
        :type sdUUID: UUID
        :param imgUUID: The UUID of image you want assosiated with that volume.
        :type imgUUID: UUID
        :param volUUID: The UUID that the new volume will have after upload.
        :type volUUID: UUID
        :param size: The size of the volume being uploaded in ...?
        :type size: ?
        :param method: The desired method of upload. Currently only *'wget'* and *'rsync'* are supported.
        :type method: str
        :param options: ?
        """
        vars.task.getSharedLock(STORAGE, spUUID)
        vol = SDF.produce(sdUUID).produceVolume(imgUUID, volUUID)
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
                self.log.warning("spm.uploadVolume: SP %s SD %s img %s Vol %s - teardown failed")


    def public_createVolume(self, sdUUID, spUUID, imgUUID, size, volFormat, preallocate, diskType, volUUID, desc, srcImgUUID=None, srcVolUUID=None):
        """
        Create a new volume
            Function Type: SPM
            Parameters:
            Return Value:
        """
        argsStr = "sdUUID=%s, spUUID=%s, imgUUID=%s, size=%s, volFormat=%s, " \
                "preallocate=%s, diskType=%s, volUUID=%s, desc=%s, " \
                "srcImgUUID=%s, srcVolUUID=%s" % (str(sdUUID), str(spUUID),
                 str(imgUUID), str(size), str(volFormat), str(preallocate),
                 str(diskType), str(volUUID), str(desc),
                 str(srcImgUUID), str(srcVolUUID))
        vars.task.setDefaultException(se.VolumeCreationError(argsStr))
        hsm.HSM.getPool(spUUID) #Validates that the pool is connected. WHY?
        hsm.HSM.validateSdUUID(sdUUID)
        misc.validateUUID(imgUUID, 'imgUUID')
        misc.validateUUID(volUUID, 'volUUID')
        # TODO: For backwards compatibility, we need to support accepting number of sectors as int type
        # Updated interface is accepting string type in bytes (ugly, get rid of this when possible)
        if not isinstance(size, types.IntType):
            size = misc.validateN(size, "size")
            size = (size + SECTOR_SIZE -1) / SECTOR_SIZE

        if srcImgUUID:
            misc.validateUUID(srcImgUUID, 'srcImgUUID')
        if srcVolUUID:
            misc.validateUUID(srcVolUUID, 'srcVolUUID')
        # Validate volume type and format
        SDF.produce(sdUUID).validateCreateVolumeParams(volFormat, preallocate, srcVolUUID)

        vars.task.getSharedLock(STORAGE, sdUUID)
        self._schedule("createVolume", self.createVolume, sdUUID,
            imgUUID, size, volFormat, preallocate, diskType, volUUID, desc,
            srcImgUUID, srcVolUUID
        )


    def public_deleteVolume(self, sdUUID, spUUID, imgUUID, volumes, postZero=False, force=False):
        """
        Delete a volume
        """
        argsStr = "sdUUID=%s, spUUID=%s, imgUUID=%s, volumes=%s, " \
                "postZero=%s, force=%s" % (str(sdUUID), str(spUUID),
                str(imgUUID), str(volumes), str(postZero), str(force))
        vars.task.setDefaultException(se.CannotDeleteVolume(argsStr))
        hsm.HSM.getPool(spUUID) #Validates that the pool is connected. WHY?
        hsm.HSM.validateSdUUID(sdUUID)
        misc.validateUUID(imgUUID, 'imgUUID')

        vars.task.getSharedLock(STORAGE, sdUUID)
        # Do not validate if forced.
        if not misc.parseBool(force):
            for volUUID in volumes:
                SDF.produce(sdUUID).produceVolume(imgUUID, volUUID).validateDelete()

        self._schedule("deleteVolume", self.deleteVolume, sdUUID,
            imgUUID, volumes, misc.parseBool(postZero), misc.parseBool(force)
        )


    def public_deleteImage(self, sdUUID, spUUID, imgUUID, postZero=False, force=False):
        """
        Delete Image folder with all volumes
        """
        #vars.task.setDefaultException(se.ChangeMeError("%s" % str(args)))
        hsm.HSM.getPool(spUUID) #Validates that the pool is connected. WHY?
        hsm.HSM.validateSdUUID(sdUUID)

        #Need this resource to induce all the LVs in the image to be active
        #at once if zeroed.
        #See http://gerrit.usersys.redhat.com/771
        if postZero:
            vars.task.getSharedLock(STORAGE, imgUUID)

        vars.task.getSharedLock(STORAGE, sdUUID)
        # Do not validate if forced.
        repoPath = os.path.join(self.storage_repository, spUUID)
        if not misc.parseBool(force):
            image.Image(repoPath).validateDelete(sdUUID, imgUUID)
        # Rename image if postZero and perform delete as async operation
        # else delete image in sync. stage
        if misc.parseBool(postZero):
            newImgUUID = image.Image(repoPath).preDeleteRename(sdUUID, imgUUID)
            self._schedule("deleteImage", self.deleteImage, sdUUID, spUUID, newImgUUID,
                            misc.parseBool(postZero), misc.parseBool(force)
            )
        else:
            self.deleteImage(sdUUID, spUUID, imgUUID,
                              misc.parseBool(postZero), misc.parseBool(force))
            # This is a hack to keep the interface consistent
            # We currently have race conditions in delete image, to quickly fix
            # this we delete images in the "synchronous" state. This only works
            # because rhev-m does not send two requests at a time. This hack is
            # intended to quickly fix the integration issue with rhev-m. In 2.3
            # we should use the new resource system to synchronize the process
            # an eliminate all race conditions
            self._schedule("deleteImage", lambda : True)


    def public_moveImage(self, spUUID, srcDomUUID, dstDomUUID, imgUUID, vmUUID, op, postZero=False, force=False):
        """
        Move/Copy image between storage domains within same storage pool
        """
        argsStr = "spUUID=%s, srcDomUUID=%s, dstDomUUID=%s, imgUUID=%s, vmUUID=%s, op=%s, "\
                  "force=%s, postZero=%s force=%s" % (str(spUUID), str(srcDomUUID), str(dstDomUUID),
                        str(imgUUID), str(vmUUID), str(op), str(force), str(postZero), str(force))
        vars.task.setDefaultException(se.MoveImageError("%s" % argsStr))
        if srcDomUUID == dstDomUUID:
            raise se.InvalidParameterException("srcDom must be different from dstDom: %s" % argsStr)

        hsm.HSM.getPool(spUUID) #Validates that the pool is connected. WHY?
        hsm.HSM.validateSdUUID(srcDomUUID)
        hsm.HSM.validateSdUUID(dstDomUUID)
        # Do not validate images in Backup domain
        repoPath = os.path.join(self.storage_repository, spUUID)
        if not SDF.produce(dstDomUUID).isBackup():
            image.Image(repoPath).validate(srcDomUUID, dstDomUUID, imgUUID, op)

        domains = [srcDomUUID, dstDomUUID]
        domains.sort()

        for dom in domains:
            vars.task.getSharedLock(STORAGE, dom)

        self._schedule("moveImage", self.moveImage, spUUID, srcDomUUID,
                    dstDomUUID, imgUUID, vmUUID, op, misc.parseBool(postZero),
                    misc.parseBool(force)
        )


    def public_moveMultipleImages(self, spUUID, srcDomUUID, dstDomUUID, imgDict, vmUUID, force=False):
        """
        Move multiple images between storage domains within same storage pool
        """
        argsStr = "spUUID=%s, srcDomUUID=%s, dstDomUUID=%s, imgDict=%s, vmUUID=%s force=%s" % (str(spUUID),
                                        str(srcDomUUID), str(dstDomUUID), str(imgDict), str(vmUUID), str(force))
        vars.task.setDefaultException(se.MultipleMoveImageError("%s" % argsStr))
        if srcDomUUID == dstDomUUID:
            raise se.InvalidParameterException("dstDomUUID", dstDomUUID)

        hsm.HSM.getPool(spUUID) #Validates that the pool is connected. WHY?
        hsm.HSM.validateSdUUID(srcDomUUID)
        hsm.HSM.validateSdUUID(dstDomUUID)
        images = {}
        for (imgUUID, pZero) in imgDict.iteritems():
            images[imgUUID.strip()] = misc.parseBool(pZero)
        # Do not validate images in Backup domain
        repoPath = os.path.join(self.storage_repository, spUUID)
        if not SDF.produce(dstDomUUID).isBackup():
            for imgUUID in imgDict:
                imgUUID = imgUUID.strip()
                image.Image(repoPath).validate(srcDomUUID, dstDomUUID, imgUUID)

        domains = sorted([srcDomUUID, dstDomUUID])
        for dom in domains:
            vars.task.getSharedLock(STORAGE, dom)

        self._schedule("moveMultipleImages", self.moveMultipleImages, spUUID,
                srcDomUUID, dstDomUUID, images, vmUUID, misc.parseBool(force)
        )


    def public_copyImage(self, sdUUID, spUUID, vmUUID, srcImgUUID, srcVolUUID, dstImgUUID, dstVolUUID,
                       description='', dstSdUUID=sd.BLANK_UUID, volType=volume.SHARED_VOL,
                       volFormat=volume.UNKNOWN_VOL, preallocate=volume.UNKNOWN_VOL,
                       postZero=False, force=False):
        """
        Create new template/volume from VM.
        Do it by collapse and copy the whole chain (baseVolUUID->srcVolUUID)
        """
        argsStr = "sdUUID=%s, spUUID=%s, vmUUID=%s, srcImgUUID=%s, srcVolUUID=%s, dstImgUUID=%s, "\
                   "dstVolUUID=%s, description=%s, dstSdUUID=%s, volType=%s, volFormat=%s, "\
                   "preallocate=%s force=%s, postZero=%s" % (str(sdUUID), str(spUUID), str(vmUUID),
                   str(srcImgUUID), str(srcVolUUID), str(dstImgUUID), str(dstVolUUID), str(description),
                   str(dstSdUUID), str(volType), str(volFormat), str(preallocate), str(force), str(postZero))
        vars.task.setDefaultException(se.TemplateCreationError("%s" % argsStr))
        # Validate imgUUID in case of copy inside source domain itself
        if dstSdUUID in [sdUUID, sd.BLANK_UUID]:
            if srcImgUUID == dstImgUUID:
                raise se.InvalidParameterException("dstImgUUID", dstImgUUID)
        hsm.HSM.getPool(spUUID) #Validates that the pool is connected. WHY?
        hsm.HSM.validateSdUUID(sdUUID)

        # Avoid VM copy if one of its volume (including template if exists) ILLEGAL/FAKE
        repoPath = os.path.join(self.storage_repository, spUUID)
        image.Image(repoPath).validateVolumeChain(sdUUID=sdUUID, imgUUID=srcImgUUID)
        # Validate volume type and format
        if dstSdUUID != sd.BLANK_UUID:
            dom = dstSdUUID
        else:
            dom = sdUUID
        SDF.produce(dom).validateCreateVolumeParams(volFormat, preallocate, volume.BLANK_UUID)

        # If dstSdUUID defined, means we copy image to it
        domains = [sdUUID]
        if dstSdUUID not in [sdUUID, sd.BLANK_UUID]:
            hsm.HSM.validateSdUUID(dstSdUUID)
            domains.append(dstSdUUID)
            domains.sort()

        for dom in domains:
            vars.task.getSharedLock(STORAGE, dom)

        self._schedule("copyImage", self.copyImage,
            sdUUID, spUUID, vmUUID, srcImgUUID, srcVolUUID, dstImgUUID,
            dstVolUUID, description, dstSdUUID, volType, volFormat,
            preallocate, misc.parseBool(postZero), misc.parseBool(force)
        )


    def public_mergeSnapshots(self, sdUUID, spUUID, vmUUID, imgUUID, ancestor, successor, postZero=False):
        """
        Merge source volume to the destination volume.
        """
        argsStr = "sdUUID=%s, spUUID=%s, vmUUID=%s, imgUUID=%s, ancestor=%s, successor=%s, "\
                  "postZero=%s" % (str(sdUUID), str(spUUID), str(vmUUID), str(imgUUID),
                                    str(ancestor), str(successor), str(postZero))
        vars.task.setDefaultException(se.MergeSnapshotsError("%s" % argsStr))
        hsm.HSM.getPool(spUUID) #Validates that the pool is connected. WHY?
        hsm.HSM.validateSdUUID(sdUUID)
        vars.task.getSharedLock(STORAGE, sdUUID)
        self._schedule("mergeSnapshots", self.mergeSnapshots, sdUUID, spUUID,
                    vmUUID, imgUUID, ancestor, successor, misc.parseBool(postZero)
        )

