#
# Copyright 2009 Red Hat, Inc. and/or its affiliates.
#
# Licensed to you under the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.  See the files README and
# LICENSE_GPL_v2 which accompany this distribution.
#

"""
This is the Host Storage Manager module.
"""

import os
import threading
import logging
from fnmatch import fnmatch
from copy import deepcopy
from config import config
from itertools import imap

import sp
import sd
import blockSD
import spm
import lvm
import fileUtils
import multipath
from sdf import StorageDomainFactory as SDF
import volume
import iscsi
import misc
import taskManager
import safelease
import storage_connection
import storage_exception as se
from threadLocal import vars
import constants
from storageConstants import STORAGE
from task import Job
from resourceFactories import IMAGE_NAMESPACE
import resourceManager as rm
import devicemapper

GUID = "guid"
NAME = "name"
UUID = "uuid"
TYPE = "type"
INITIALIZED = "initialized"
CAPACITY = "capacity"
PATHLIST = "pathlist"


rmanager = rm.ResourceManager.getInstance()

class HSM:
    """
    This is the HSM class. It controls all the stuff relate to the Host.
    Further more it doesn't change any pool metadata.

    .. attribute:: tasksDir

        A string containing the path of the directory where backups of tasks a saved on the disk.
    """
    pools = {}
    log = logging.getLogger('Storage.HSM')

    @classmethod
    def validateSdUUID(cls, sdUUID):
        """
        Validate a storage domain.

        :param sdUUID: the UUID of the storage domain you want to validate.
        :type sdUUID: UUID
        """
        SDF.produce(sdUUID=sdUUID).validate()

    @classmethod
    def validateBackupDom(cls, sdUUID):
        """
        Validates a backup domain.

        :param sdUUID: the UUID of the storage domain you want to validate.
        :type sdUUID: UUID

        If the domain doesn't exist an exeption will be thrown.
        If the domain isn't a backup domain a :exc:`storage_exception.StorageDomainTypeNotBackup` exception will be raised.
        """
        if not SDF.produce(sdUUID=sdUUID).isBackup():
            raise se.StorageDomainTypeNotBackup(sdUUID)

    @classmethod
    def validatePoolSD(cls, spUUID, sdUUID):
        if not cls.getPool(spUUID).isMember(sdUUID):
            raise se.StorageDomainNotMemberOfPool(spUUID, sdUUID)

    @classmethod
    def validateNonDomain(cls, sdUUID):
        """
        Validates that there is no domain with this UUID.

        :param sdUUID: The UUID to test.
        :type sdUUID: UUID

        :raises: :exc:`storage_exception.StorageDomainAlreadyExists` exception if a domain with this UUID exists.
        """
        try:
            SDF.produce(sdUUID=sdUUID)
            raise se.StorageDomainAlreadyExists(sdUUID)
        #If partial metadata exists the method will throw MetadataNotFound.
        #Though correct the logical response in this context is StorageDomainNotEmpty.
        except se.StorageDomainMetadataNotFound:
            raise se.StorageDomainNotEmpty()
        except se.StorageDomainDoesNotExist:
            pass

    def validateNotSPM(self, spUUID):
        if self.spm.isActive(contend=True):
            raise se.IsSpm(spUUID)

    @classmethod
    def getPool(cls, spUUID):
        if spUUID not in cls.pools:
            raise se.StoragePoolUnknown(spUUID)
        return cls.pools[spUUID]

    def __init__(self):
        """
        The HSM Constructor

        :param defExcFunc: The function that will set the default exception for this thread
        :type defExcFun: function
        """
        self.storage_repository = config.get('irs', 'repository')
        self.sd_validate_timeout = config.getint('irs', 'sd_validate_timeout')
        self.taskMng = taskManager.TaskManager()
        self.spm = spm.SPM(self.taskMng)
        self._domstats = {}
        self._cachedStats = {}
        self._statslock = threading.Lock()

        if not iscsi.isConfigured():
            iscsi.setupiSCSI()

        if not multipath.isEnabled():
            multipath.setupMultipath()

        self.__validateLvmLockingType()

        def storageRefresh():
            SDF.refreshStorage()

            self.tasksDir = config.get('irs', 'hsm_tasks')
            try:
                self.__cleanStorageRepository()
            except Exception:
                self.log.warn("Failed to clean Storage Repository.", exc_info=True)

            fileUtils.createdir(self.tasksDir)
            # TBD: Should this be run in connectStoragePool? Should tasksDir exist under pool link as well (for hsm tasks)
            self.taskMng.loadDumpedTasks(self.tasksDir)
            self.taskMng.recoverDumpedTasks()

            _poolsTmpDir = config.get('irs', 'pools_data_dir')
            dirList = os.listdir(_poolsTmpDir)
            for spUUID in dirList:
                poolPath = os.path.join(self.storage_repository, spUUID)
                try:
                    if os.path.exists(poolPath):
                        self._restorePool(spUUID)
                        #TODO Once we support simultaneous connection to multiple pools, remove following line (break)
                        break
                except Exception:
                    self.log.error("Unexpected error", exc_info=True)

        threading.Thread(target=storageRefresh).start()


    def __validateLvmLockingType(self):
        """
        Check lvm locking type.
        """
        rc, out, err = misc.execCmd([constants.EXT_LVM, "dumpconfig", "global/locking_type"])
        if rc != 0:
            self.log.error("Can't validate lvm locking_type. %d %s %s", rc, out, err)
            return False

        try:
            lvmLockingType = int(out[0].split('=')[1])
        except (ValueError, IndexError):
            self.log.error("Can't parse lvm locking_type. %s", out)
            return False

        if lvmLockingType != 1:
            self.log.error("Invalid lvm locking_type. %d", lvmLockingType)
            return False

        return True


    def __cleanStorageRepository(self):
        """
        """
        self.log.debug("Started cleaning storage repository at '%s'" % self.storage_repository)

        mntDir = os.path.join(self.storage_repository, 'mnt')
        tasksFiles = os.path.join(self.tasksDir, "*")
        whiteList = [self.tasksDir, tasksFiles, mntDir]

        def isInWhiteList(fpath):
            fullpath = os.path.abspath(fpath)

            # 'readlink' doesn't following nested symlinks like 'realpath'
            # but it's also doesn't stuck on inaccessible mounts like 'realpath'.
            # Anyway, it's enough for our purposes.
            if os.path.islink(fullpath):
                fullpath = os.readlink(fullpath)

            for entry in whiteList:
                if fnmatch(fullpath, entry):
                    return True

            return False

        #Add mounted folders to whitelist
        mounts = fileUtils.getMounts()
        for mount in mounts:
            mountPoint = mount[1]
            isInStorageRepo = (os.path.commonprefix([self.storage_repository, mountPoint]) == self.storage_repository)
            if isInStorageRepo:
                whiteList.extend([mountPoint, os.path.join(mountPoint, "*")])

        self.log.debug("White list is %s." % str(whiteList))
        #Clean whatever is left
        self.log.debug("Cleaning leftovers.")
        # We can't list files form top to bottom because the it will go into
        # mounts.A mounted NFS could be stuck and freeze vdsm startup. Because
        # we will ignore files in mounts anyway using out of process file ops
        # is useless. We just clean all directories before removing them.
        # We push them at the start so we delete them from the inner moust
        # outward.
        rmDirList = []
        for base, dirs, files in os.walk(self.storage_repository):
            for directory in dirs:
                fullPath = os.path.join(base, directory)
                if isInWhiteList(fullPath):
                    dirs.remove(directory)
                    continue

                rmDirList.insert(0, os.path.join(base, fullPath))

            for fname in files:
                fullPath = os.path.join(base, fname)
                if isInWhiteList(fullPath):
                    continue

                try:
                    os.unlink(os.path.join(base, fullPath))
                except Exception, ex:
                    self.log.warn("Cold not delete file '%s' (%s: %s)." % (fullPath, ex.__class__.__name__, str(ex)))

        for directory in rmDirList:
            try:
                os.rmdir(directory)
            except Exception, ex:
                self.log.warn("Could not delete dir '%s' (%s: %s)." % (fullPath, ex.__class__.__name__, str(ex)))

        self.log.debug("Finished cleaning storage repository at '%s'" % self.storage_repository)


    def public_getConnectedStoragePoolsList(self, options = None):
        """
        Get a list of all the connected storage pools.

        :param options: Could be one or more of the following:
                * OptionA - A good option. Chosen by most
                * OptionB - A much more complex option. Only for the brave

        :type options: list
        """
        vars.task.setDefaultException(se.StoragePoolActionError())

        return dict(poollist = self.pools.keys())


    def public_spmStart(self, spUUID, prevID, prevLVER, recoveryMode, scsiFencing,
            maxHostID=safelease.MAX_HOST_ID, domVersion=None, options = None):
        """
        Starts an SPM.

        :param spUUID: The storage pool you want managed.
        :type spUUID: UUID
        :param prevID: The previous ID of the SPM that managed this pool.
        :type prevID: int
        :param prevLVER: The previous version of the pool that was managed by the SPM.
        :type prevLVER: int
        :param recoveryMode: The mode in which to recover the SPM.
        :type recoveryMode: RecoveryEnum?
        :param scsiFencing: Do you want to fence the scsi.
        :type scsiFencing: bool
        :param maxHostID: The maximun Host ID in the cluster.
        :type maxHostID: int
        :param options: unused

        :returns: The UUID of the started task.
        :rtype: UUID
        """

        argsStr = "spUUID=%s, prevID=%s, prevLVER=%s, recoveryMode=%s, scsiFencing=%s, maxHostID=%s, domVersion=%s" % (
                spUUID, prevID, prevLVER, recoveryMode, scsiFencing, maxHostID, domVersion)
        vars.task.setDefaultException(se.SpmStartError("%s" % (argsStr)))

        if domVersion is not None:
            domVersion = int(domVersion)
            sd.validateDomainVersion(domVersion)

        #This code is repeated twice for perfomance reasons
        #Avoid waiting for the lock for validate.
        self.getPool(spUUID)
        self.validateNotSPM(spUUID)

        vars.task.getExclusiveLock(STORAGE, spUUID)
        self.getPool(spUUID)
        # We should actually just return true if we are SPM after lock,
        # but seeing as it would break the API with RHEVM, it's easiest to fail.
        self.validateNotSPM(spUUID)

        vars.task.setTag("hsm")
        vars.task.setManager(self.taskMng)
        vars.task.setRecoveryPolicy("auto")
        vars.task.addJob(Job("spmStart", self.spm.start, spUUID, prevID, prevLVER,
                recoveryMode, scsiFencing, maxHostID, domVersion))


    def sendExtendMsg(self, spUUID, volDict, newSize, callbackFunc):
        """
        Send an extended message?

        :param spUUID: The UUID of the storage pool you want to send the message to.
        :type spUUID: UUID
        :param volDict: ?
        :param newSize: ?
        :param callbackFun: A function to run once the operation is done. ?

        .. note::
            If the pool doesn't exist the function will fail sliently and the callback will never be called.

        """
        newSize = misc.validateN(newSize, "newSize") / 2**20
        try:
            pool = self.getPool(spUUID)
        except se.StoragePoolUnknown:
            pass
        else:
            if pool.hsmMailer:
                pool.hsmMailer.sendExtendMsg(volDict, newSize, callbackFunc)


    def public_refreshStoragePool(self, spUUID, msdUUID, masterVersion, options = None):
        """
        Refresh the Storage Pool info in HSM.

        :param spUUID: The UUID of the storage pool you want to refresh.
        :type spUUID: UUID
        :param msdUUID: The UUID of the master storage domain.
        :type msdUUID: UUID
        :param masterVersion: The master version of the storage pool.
        :type masterVersion: uint
        :param options: Lot of options. ?

        :returns: True if everything went as planned.
        :rtype: bool

        :raises: a :exc:`Storage_Exception.StoragePoolMaterNotFound` if the storage pool and the master storage domain don't exist or don't match.

        """
        vars.task.setDefaultException(
            se.StoragePoolActionError("spUUID=%s, msdUUID=%s, masterVersion=%s" % (
                                str(spUUID), str(msdUUID), str(masterVersion))))
        vars.task.getSharedLock(STORAGE, spUUID)
        pool = self.getPool(spUUID)
        try:
            self.validateSdUUID(msdUUID)
            pool.refresh(msdUUID, masterVersion)
        except:
            self._disconnectPool(pool, pool.id, pool.scsiKey, False)
            raise

        if pool.hsmMailer:
            pool.hsmMailer.flushMessages()


    def _restorePool(self, spUUID):
        self.log.info("RESTOREPOOL: %s", spUUID)
        pool = sp.StoragePool(spUUID)
        if pool.reconnect():
            self.pools[spUUID] = pool
            return True
        self.log.info("RESTOREPOOL: %s reconnect failed", spUUID)


    def public_createStoragePool(self, poolType, spUUID, poolName, masterDom, domList, masterVersion, lockPolicy=None, lockRenewalIntervalSec=None, leaseTimeSec=None, ioOpTimeoutSec=None, leaseRetries=None, options = None):
        """
        Create new storage pool with single/multiple image data domain.
        The command will create new storage pool meta-data attach each
        storage domain to that storage pool.
        At least one data (images) domain must be provided

        .. note::
            The master domain needs to be also stated in the domain list

        :param poolType: The type of the new storage pool.
        :type poolType: Some enum?
        :param spUUID: The UUID that the new storage pool will have
        :type spUUID: UUID
        :param poolName: The human readable name of the new pool.
        :type poolName: str
        :param masterDom: The UUID of the master storage domain that contains\will contain the pool's metadata.
        :type masterDom: UUID
        :param domList: A list of allthe UUIDs of the storage domains managed by this storage pool.
        :type domList: UUID list
        :param masterVersion: The master version of the storage pool meta data.
        :type masterVersion: uint
        :param lockPolicy: ?
        :param lockRenewalIntervalSec: ?
        :param leaseTimeSec: ?
        :param ioOpTimeoutSec: The default timeout for IO operations in seconds.?
        :type ioOpTimroutSec: uint
        :param leaseRetries: ?
        :param options: ?

        :returns: The newly created storage pool object.
        :rtype: :class:`sp.StoragePool`

        :raises: an :exc:`Storage_Exception.InvalidParameterException` if the master domain is not supplied in the domain list.
        """
        safeLease = sd.packLeaseParams(
                               lockRenewalIntervalSec=lockRenewalIntervalSec,
                               leaseTimeSec=leaseTimeSec,
                               ioOpTimeoutSec=ioOpTimeoutSec,
                               leaseRetries=leaseRetries)
        vars.task.setDefaultException(
            se.StoragePoolCreationError("spUUID=%s, " \
                "poolName=%s, masterDom=%s, domList=%s, masterVersion=%s, " \
                "safelease params: (%s)" % (
                    str(spUUID), str(poolName), str(masterDom), str(domList),
                    str(masterVersion), str(safeLease)
                )
            )
        )
        misc.validateUUID(spUUID, 'spUUID')
        if masterDom not in domList:
            raise se.InvalidParameterException("masterDom", str(masterDom))

        if len(poolName) > sp.MAX_POOL_DESCRIPTION_SIZE:
            raise se.StoragePoolDescriptionTooLongError()

        msd = SDF.produce(sdUUID=masterDom)
        msdType = msd.getStorageType()
        msdVersion = msd.getVersion()
        if msdType in sd.BLOCK_DOMAIN_TYPES and msdVersion in blockSD.VERS_METADATA_LV and len(domList) > sp.MAX_DOMAINS:
            raise se.TooManyDomainsInStoragePoolError()

        for sdUUID in domList:
            try:
                dom = SDF.produce(sdUUID=sdUUID)
                # TODO: consider removing validate() from here, as the domains
                # are going to be accessed much later, and may loose validity
                # until then.
                dom.validate()
            except:
                raise se.StorageDomainAccessError(sdUUID)
            #If you remove this condition, remove it from StoragePool.attachSD() too.
            if dom.isData() and (dom.getVersion() != msdVersion):
                raise se.MixedSDVersionError(dom.sdUUID, dom.getVersion(), msd.sdUUID, msdVersion)

        vars.task.getExclusiveLock(STORAGE, spUUID)
        for dom in sorted(domList):
            vars.task.getExclusiveLock(STORAGE, dom)

        return sp.StoragePool(spUUID).create(poolName, masterDom, domList, masterVersion, safeLease)

    def public_connectStoragePool(self, spUUID, hostID, scsiKey, msdUUID, masterVersion, options = None):
        """
        Connect a Host to a specific storage pool.

        :param spUUID: The UUID of the storage pool you want to connect to.
        :type spUUID: UUID
        :param hostID: The hostID to be used for clustered locking.
        :type hostID: int
        :param scsiKey: ?
        :param msdUUID: The UUID for the pool's master domain.
        :type msdUUID: UUID
        :param masterVersion: The expected master version. Used for validation.
        :type masterVersion: int
        :param options: ?

        :returns: :keyword:`True` if connection was successful.
        :rtype: bool

        :raises: :exc:`storage_exception.ConnotConnectMultiplePools` when storage pool is not connected to the system.
        """
        vars.task.setDefaultException(
            se.StoragePoolConnectionError("spUUID=%s, msdUUID=%s, masterVersion=%s, " \
                                          "hostID=%s, scsiKey=%s" % (str(spUUID), str(msdUUID),
                                          str(masterVersion), str(hostID), str(scsiKey))
            )
        )
        misc.validateN(hostID, 'hostID')
        misc.validateUUID(spUUID, 'spUUID')

        # TBD: To support multiple pool connection on single host,
        # we'll need to remove this validation
        if len(self.pools) and spUUID not in self.pools:
            raise se.CannotConnectMultiplePools(str(self.pools.keys()))

        try:
            self.getPool(spUUID)
        except se.StoragePoolUnknown:
            pass #pool not connected yet
        else:
            vars.task.getSharedLock(STORAGE, spUUID)
            pool = self.getPool(spUUID)
            pool.verifyMasterDomain(msdUUID=msdUUID, masterVersion=masterVersion)
            return

        vars.task.getExclusiveLock(STORAGE, spUUID)
        try:
            pool = self.getPool(spUUID)
        except se.StoragePoolUnknown:
            pass #pool not connected yet
        else:
            pool.verifyMasterDomain(msdUUID=msdUUID, masterVersion=masterVersion)
            return

        pool = sp.StoragePool(spUUID)
        res = pool.connect(hostID, scsiKey, msdUUID, masterVersion)
        if res:
            self.pools[spUUID] = pool
        return res

    def public_disconnectStoragePool(self, spUUID, hostID, scsiKey, remove=False, options = None):
        """
        Disconnect a Host from a specific storage pool.

        :param spUUID: The UUID of the storage pool you want to disconnect.
        :type spUUID: UUID
        :param hostID: The ID of the host you want to disconnect the pool from.
        :type hostID: int
        :param scsiKey: ?
        :param remove: ?
        :type remove: bool
        :param options: ?

        :returns: :keyword:`True` if disconnection was successful.
        :rtype: bool

        .. note::
            if storage pool is not connected or dosn't exist the opration will exit silently.
        """
        vars.task.setDefaultException(se.StoragePoolDisconnectionError("spUUID=%s, hostID=%s, scsiKey=%s" % (str(spUUID), str(hostID), str(scsiKey))))
        misc.validateN(hostID, 'hostID')
        # already disconnected/or pool is just unknown - return OK
        try:
            pool = self.getPool(spUUID)
        except se.StoragePoolUnknown:
            return

        self.validateNotSPM(spUUID)

        vars.task.getExclusiveLock(STORAGE, spUUID)
        self.validateNotSPM(spUUID)

        pool = self.getPool(spUUID)
        return self._disconnectPool(pool, hostID, scsiKey, remove)


    def _disconnectPool(self, pool, hostID, scsiKey, remove):
        res = pool.disconnect()
        del self.pools[pool.spUUID]
        return res


    def public_destroyStoragePool(self, spUUID, hostID, scsiKey, options = None):
        """
        Destroy a storage pool.
        The command will detach all inactive domains from the pool
        and delete the pool with all its links.

        :param spUUID: The UUID of the storage pool you want to destroy.
        :type spUUID: UUID
        :param hostID: The ID of the host managing this storage pool. ?
        :type hostID: int
        :param scsiKey: ?
        :param options: ?
        """
        vars.task.setDefaultException(se.StoragePoolDestroyingError("spUUID=%s, hostID=%s, scsiKey=%s" % (str(spUUID), str(hostID), str(scsiKey))))
        self.log.info("spUUID=%s", spUUID)

        pool = self.getPool(spUUID)
        if not pool.id == hostID:
            raise se.HostIdMismatch(spUUID)

        vars.task.getExclusiveLock(STORAGE, pool.spUUID)
        # Find out domain list from the pool metadata
        domList = sorted(pool.getDomains().keys())
        for sdUUID in domList:
            vars.task.getExclusiveLock(STORAGE, sdUUID)

        self.spm.detachAllDomains(pool)
        return self._disconnectPool(pool, hostID, scsiKey, remove=True)


    def public_reconstructMaster(self, spUUID, poolName, masterDom, domDict,
                                 masterVersion, lockPolicy=None,
                                 lockRenewalIntervalSec=None, leaseTimeSec=None,
                                 ioOpTimeoutSec=None, leaseRetries=None, options = None):
        """
        Reconstruct Master Domains - rescue action: can be issued even when pool is not connected.

        :param spUUID: The UUID of the storage pool you want to reconstruct.
        :type spUUID: UUID
        :param masterDom: The new master domain UUID.
        :type masterDom: UUID
        :param domDict: Dict. of domain and statuses ``{'sdUUID1':status1, 'sdUUID2':status2}``
        :type domDict: dict
        :param masterVersion: The new version of master domain.
        :type masterVersion: int
        :param lockPolicy: ?
        :param lockRenewalIntervalSec: ?
        :param leaseTimeSec: ?
        :param ioOpTimeoutSec: The timout of IO operations in seconds. ?
        :type ioOpTimeoutSec: int
        :param leaseRetries: ?
        :param options: ?

        :returns: Nothing ? pool.reconstructMaster return nothing
        :rtype: ?
        """
        safeLease = sd.packLeaseParams(
                               lockRenewalIntervalSec=lockRenewalIntervalSec,
                               leaseTimeSec=leaseTimeSec,
                               ioOpTimeoutSec=ioOpTimeoutSec,
                               leaseRetries=leaseRetries)
        vars.task.setDefaultException(se.ReconstructMasterError("spUUID=%s, masterDom=%s, masterVersion=%s, safelease params: (%s)" % (str(spUUID), str(masterDom), str(masterVersion), str(safeLease))))
        self.log.info("spUUID=%s master=%s", spUUID, masterDom)
        try:
            pool = self.getPool(spUUID)
        except se.StoragePoolUnknown:
            pool = sp.StoragePool(spUUID)
        else:
            raise se.StoragePoolConnected(spUUID)

        self.validateSdUUID(masterDom)
        vars.task.getExclusiveLock(STORAGE, spUUID)
        for d, status in domDict.iteritems():
            misc.validateUUID(d)
            try:
                sd.validateSDStatus(status)
            except:
                domDict[d] = sd.validateSDDeprecatedStatus(status)

        return pool.reconstructMaster(poolName, masterDom, domDict, masterVersion, safeLease)


    def _logResp_getDeviceList(self, response):
        logableDevs = deepcopy(response)
        for dev in logableDevs['devList']:
            for con in dev['pathlist']:
                con['password'] = "******"
        return logableDevs

    def public_getDeviceList(self, storageType=None, options = None):
        """
        List all Block Devices.

        :param storageType: Filter by storage type.
        :type storageType: Some enum?
        :param options: ?

        :returns: Dict containing a list of all the devices of the storage type specified.
        :rtype: dict
        """
        vars.task.setDefaultException(se.BlockDeviceActionError())
        devices = self._getDeviceList(storageType)
        return dict(devList=devices)

    def _getDeviceList(self, storageType=None, guids=None):
        SDF.refreshStorage()
        typeFilter = lambda dev : True
        if storageType:
            if sd.storageType(storageType) == sd.type2name(sd.ISCSI_DOMAIN):
                typeFilter = lambda dev : multipath.devIsiSCSI(dev.get("devtype"))
            elif sd.storageType(storageType) == sd.type2name(sd.FCP_DOMAIN):
                typeFilter = lambda dev : multipath.devIsFCP(dev.get("devtype"))

        devices = []
        pvs = {}
        if guids is not None:
            for guid in guids:
                try:
                    pv = lvm.getPV(guid)
                    if pv is None:
                        continue
                    pvs[os.path.basename(pv.name)] = pv
                except:
                    pass
        else:
            for pv in lvm.getAllPVs():
                pvs[os.path.basename(pv.name)] = pv

        # FIXME: pathListIter() should not return empty records
        for dev in multipath.pathListIter(guids):
            try:
                if not typeFilter(dev):
                    continue

                pvuuid = ""
                vguuid = ""

                pv = pvs.get(dev.get('guid', ""))
                if pv is not None:
                    pvuuid = pv.uuid
                    vguuid = pv.vg_uuid

                devInfo = {'GUID': dev.get("guid", ""), 'pvUUID': pvuuid, 'vgUUID': vguuid,
                        'vendorID': dev.get("vendor", ""), 'productID': dev.get("product", ""),
                        'fwrev': dev.get("fwrev", ""), "serial" : dev.get("serial", ""),
                        'capacity': dev.get("capacity", "0"), 'devtype': dev.get("devtype", ""),
                        'pathstatus': dev.get("paths", []), 'pathlist': dev.get("connections", [])}
                for path in devInfo["pathstatus"]:
                    path["lun"] = path["hbtl"].lun
                    del path["hbtl"]
                    del path["devnum"]
                devices.append(devInfo)
            except se.InvalidPhysDev:
                pass
            except se.PartitionedPhysDev:
                self.log.warning("Ignore partitioned device %s", dev)


        return devices

    def public_getDeviceInfo(self, guid, options = None):
        """
        Get info of block device.

        :param guid: The GUID of the device you want to get info on.
        :type guid: UUID
        :param options: ?

        :returns: Dict of all the info on the device.
        :rtype: dict

        :raises: :exc:`storage_exception.DeviceNotFound` if a device with that GUID doesn't exist.
        """
        vars.task.setDefaultException(se.BlockDeviceActionError("GUID: %s" % str(guid)))
        #getSharedLock(connectionsResource...)
        try:
            devInfo = self._getDeviceList(guids=[guid])[0]
            for p in devInfo["pathstatus"]:
                if p.get("state", "error") == "active":
                    return {"info" : devInfo }

            raise se.DeviceNotFound(str(guid))
        except KeyError:
           raise se.DeviceNotFound(str(guid))


    def public_getDevicesVisibility(self, guids, options=None):
        """
        Check which of the luns with specified guids are visible

        :param guids: List of device GUIDs to check.
        :type guids: list
        :param options: ?

        :returns: dictionary of specified guids and respective visibility
                  boolean
        :rtype: dict
        """
        import stat
        def devVisible(guid):
            try:
                res = os.stat('/dev/mapper/' + guid).st_mode & stat.S_IRUSR != 0
            except:
                res = False
            return res
        return {'visible' : dict(zip(guids, map(devVisible, guids)))}


    def public_createVG(self, vgname, devlist, options = None):
        """
        Creates a volume group with the name 'vgname' out of the devices in 'devlist'

        :param vgname: The human readable name of the vg.
        :type vgname: str
        :param devlist: A list of devices to be included in the VG. The devices must be unattached.
        :type devlist: list
        :param options: ?

        :returns: the UUID of the new VG.
        :rtype: UUID
        """
        MINIMALVGSIZE = 10 * 1024 * constants.MEGAB

        vars.task.setDefaultException(se.VolumeGroupCreateError(str(vgname), str(devlist)))
        misc.validateUUID(vgname, 'vgname')
        #getSharedLock(connectionsResource...)
        knowndevs = list(multipath.getMPDevNamesIter())
        devices = []
        devSizes = []

        for dev in devlist:
            if dev in knowndevs:
                devices.append(dev)
                devSizes.append(multipath.getDeviceSize(devicemapper.getDmId(dev)))
            else:
                raise se.InvalidPhysDev(dev)

        #Minimal size check
        size = sum(devSizes)
        if size < MINIMALVGSIZE:
           raise se.VolumeGroupSizeError("VG size must be more than %s MiB" % str(MINIMALVGSIZE / constants.MEGAB))

        lvm.createVG(vgname, devices, blockSD.STORAGE_UNREADY_DOMAIN_TAG)

        return dict(uuid=lvm.getVG(vgname).uuid)


    def public_removeVG(self, vgUUID, options = None):
        """
        Removes a volume group.

        :param vgUUID: The UUID of the VG you want removed.
        :type vgUUID: UUID
        :param options: ?

        :raises: :exc:`storage_exception.VolumeGroupDoesNotExist` if no VG with this UUID exists.
        """
        vars.task.setDefaultException(se.VolumeGroupActionError("%s" % str(vgUUID)))
        #getSharedLock(connectionsResource...)
        lvm.removeVGbyUUID(vgUUID)


    def public_getTaskStatus(self, taskID, spUUID=None, options = None):
        """
        Gets the status of a task.

        :param taskID: The ID of the task you want the check.
        :type taskID: ID?
        :param spUUID: the UUID of the storage pool that the task is operating on. ??
        :type spUUID: UUID (deprecated)
        :param options: ?

        :returns: a dict containing the status information of the task.
        :rtype: dict
        """
        #getSharedLock(tasksResource...)
        taskStatus = self.taskMng.getTaskStatus(taskID=taskID)
        return dict(taskStatus=taskStatus)


    def public_getAllTasksStatuses(self, spUUID=None, options = None):
        """
        Gets the status of all public tasks.

        :param spUUID: The UUID of the storage pool that you want to check it's tasks.
        :type spUUID: UUID (deprecated)
        :options: ?
        """
        #getSharedLock(tasksResource...)
        allTasksStatus = self.taskMng.getAllTasksStatuses("spm")
        return dict(allTasksStatus=allTasksStatus)


    def public_getTaskInfo(self, taskID, spUUID=None, options = None):
        """
        Gets information about a Task.

        :param taskID: The ID of the task you want to get info on.
        :type taskID: ID ?
        :param spUUID: The UUID of the storage pool that owns this task. ?
        :type spUUID: UUID (deprecated)
        :para options: ?

        :returns: a dict with information about the task.
        :rtype: dict

        :raises: :exc:`storage_exception.UnknownTask` if a task with the specified taskID doesn't exist.
        """
        #getSharedLock(tasksResource...)
        inf = self.taskMng.getTaskInfo(taskID=taskID)
        return dict(TaskInfo=inf)


    def public_getAllTasksInfo(self, spUUID=None, options = None):
        """
        Get the information of all the tasks in a storage pool.

        :param spUUID: The UUID of the storage pool you that want to check it's tasks info.
        :type spUUID: UUID (deprecated)
        :param options: ?

        :returns: a dict of all the tasks information.
        :rtype: dict
        """
        #getSharedLock(tasksResource...)
        # TODO: if spUUID passed, make sure tasks are relevant only to pool
        allTasksInfo = self.taskMng.getAllTasksInfo("spm")
        return dict(allTasksInfo=allTasksInfo)


    def public_stopTask(self, taskID, spUUID=None, options = None):
        """
        Stops a task.

        :param taskID: The ID of the task you want to stop.
        :type taskID: ID?
        :param spUUID: The UUID of the storage pool that owns the task.
        :type spUUID: UUID (deprecated)
        :options: ?

        :returns: :keyword:`True` if task was stopped successfuly.
        :rtype: bool
        """
        force = False
        if options:
            try:
                force = options.get("force", False)
            except:
                self.log.warning("options %s are ignored" % str(options))
        #getExclusiveLock(tasksResource...)
        return self.taskMng.stopTask(taskID=taskID, force=force)


    def public_clearTask(self, taskID, spUUID=None, options = None):
        """
        Clears a task. ?

        :param taskID: The ID of the task you want to clear.
        :type taskID: ID?
        :param spUUID: The UUID of the storage pool that owns this task.
        :type spUUID: UUID (deprecated)
        :options: ?

        :returns: :keyword:`True` if task was cleared successfuly.
        :rtype: bool
        """
        #getExclusiveLock(tasksResource...)
        return self.taskMng.clearTask(taskID=taskID)


    def public_revertTask(self, taskID, spUUID=None, options = None):
        """
        Revert a task.

        :param taskID: The ID of the task you want to clear.
        :type taskID: ID?
        :param spUUID: The UUID of the storage pool that owns this task.
        :type spUUID: UUID (deprecated)
        :options: ?

        :returns:
        :rtype:
        """
        #getExclusiveLock(tasksResource...)
        return self.taskMng.revertTask(taskID=taskID)

    def public_getFileList(self, sdUUID, pattern='*', options=None):
        """
        Returns a list of all files in the domain filtered according to extension.

        :param sdUUID: The UUID of the storage domain you want to query.
        :type sdUUID: UUID
        :param pattern: the glob expression for filtering
        :type extension: str
        :options: ?

        :returns: a dict of all the volumes found.
        :rtype: dict
        """
        vars.task.setDefaultException(se.GetFileListError(sdUUID))
        vars.task.getSharedLock(STORAGE, sdUUID)

        dom = SDF.produce(sdUUID=sdUUID)
        if not dom.isISO or dom.getStorageType() != sd.NFS_DOMAIN:
            raise se.GetFileListError(sdUUID)
        filesDict = dom.getFileList(pattern=pattern, caseSensitive=True)
        return {'files':filesDict}

    def public_getIsoList(self, spUUID, extension='iso', options = None):
        """
        Gets a list of all ISO/Floppy volumes in a storage pool.

        :param spUUID: The UUID of the storage pool you want to query.
        :type spUUID: UUID
        :param extension: ?
        :type extension: str
        :options: ?

        :returns: a dict of all the volumes found.
        :rtype: dict
        """
        vars.task.setDefaultException(se.GetIsoListError(spUUID))
        vars.task.getSharedLock(STORAGE, spUUID)
        isoDom = self.getPool(spUUID).getIsoDomain()
        if not isoDom:
            raise se.GetIsoListError(isoDom.sdUUID)

        # Get full iso files dictionary
        isodict = isoDom.getFileList(pattern='*.' + extension, caseSensitive=False)
        # Get list of iso images with proper permissions only
        isolist = [key for key,value in isodict.items() if isodict[key]['status'] == 0]
        return {'isolist':isolist}


    def public_getFloppyList(self, spUUID, options = None):
        """
        Gets a list of all Floppy volumes if a storage pool.

        :param spUUID: The UUID of the storage pool you want to query.
        :type spUUID: UUID
        :param options: ?

        :returns: a dict of all the floppy volumes found.
        :rtype: dict
        """
        vars.task.setDefaultException(se.GetFloppyListError("%s" % str(spUUID)))
        return self.public_getIsoList(spUUID=spUUID, extension='vfd')


    def _log_connectStorageServer(self, domType, spUUID, conList):
        cons = storage_connection.StorageServerConnection.loggableConList(conList=conList)
        return "domType=%s, spUUID=%s, conList=%s" % (domType, spUUID, cons)

    def public_connectStorageServer(self, domType, spUUID, conList, options = None):
        """
        Connects to a storage low level entity (server).

        :param domType: The type of the domain ...?
        :type domType: Some enum?
        :param spUUID: The UUID of the storage pool ...?
        :type spUUID: UUID
        :param conList: A list of connections. ?
        :type conList: list
        :param options: ?

        :returns: a list of statuses ?
        :rtype: dict
        """
        cons = storage_connection.StorageServerConnection.loggableConList(conList=conList)
        vars.task.setDefaultException(se.StorageServerConnectionError("domType=%s, spUUID=%s, conList=%s" % (str(domType), str(spUUID), cons)))
        #getExclusiveLock(connectionsResource...)
        statusList = storage_connection.StorageServerConnection().connect(domType=domType, conList=conList)
        # Connecting new device may change the visible storage domain list
        # so invalidate caches
        SDF.invalidateStorage()
        return dict(statuslist=statusList)


    _log_validateStorageServerConnection = _log_connectStorageServer
    def public_validateStorageServerConnection(self, domType, spUUID, conList, options = None):
        """
        Validates if we can connect to a storage server.

        :param domType: The domain type ....?
        :type domType: Some enum?
        :param spUUID: The UUID of the storage pool ....?
        :type spUUID: UUID
        :param conList: a list of connections ...?
        :type conList: list
        :param options: ?
        """
        cons = storage_connection.StorageServerConnection.loggableConList(conList=conList)
        vars.task.setDefaultException(se.StorageServerValidationError("domType=%s, spUUID=%s, conList=%s" % (str(domType), str(spUUID), cons)))
        #getSharedLock(connectionsResource...)
        statusList = storage_connection.StorageServerConnection().validate(domType=domType, conList=conList)
        return dict(statuslist=statusList)


    _log_disconnectStorageServer = _log_connectStorageServer
    def public_disconnectStorageServer(self, domType, spUUID, conList, options = None):
        """
        Disconnects from a storage low level entity (server).

        :param domType: The type of the domain....?
        :type domType: Some enum?
        :param spUUID: The UUID of the storage pool that...
        :type spUUID: UUID
        :param options: ?

        :returns: a dict with a list of statuses
        :rtype: dict
        """
        cons = storage_connection.StorageServerConnection.loggableConList(conList=conList)
        vars.task.setDefaultException(se.StorageServerDisconnectionError("domType=%s, spUUID=%s, conList=%s" % (str(domType), str(spUUID), cons)))
        #getExclusiveLock(connectionsResource...)
        statusList = storage_connection.StorageServerConnection().disconnect(domType=domType, conList=conList)
        # Disconnecting a device may change the visible storage domain list
        # so invalidate the caches
        SDF.refreshStorage()
        return dict(statuslist=statusList)


    def public_getStorageConnectionsList(self, spUUID, options = None):
        """
        Gets a list of all the storage connections of the pool.

        .. warning::
                This method is not yet implemented and will allways fail.

        :param spUUID: The UUID of the storage pool you want to query.
        :type spUUID: UUID
        :param options: ?
        """
        vars.task.setDefaultException(se.StorageServerActionError("spUUID=%s" % str(spUUID)))
        raise se.NotImplementedException("getStorageConnectionsList")
        # Once implemented, return value should look something like this:
        #getSharedLock(connectionsResource...)
        #connectionslist = ""
        #return dict(connectionslist=connectionslist)


    def public_getStoragePoolInfo(self, spUUID, options = None):
        """
        Gets info about a storage pool.

        :param spUUID: The UUID of the storage pool you want to get info on.
        :type spUUID: UUID
        :param options: ?

        :returns: getPool(spUUID).getInfo
        """
        vars.task.setDefaultException(se.StoragePoolActionError("spUUID=%s" % str(spUUID)))
        vars.task.getSharedLock(STORAGE, spUUID)
        return self.getPool(spUUID).getInfo()


    def public_createStorageDomain(self, storageType, sdUUID, domainName,
                                    typeSpecificArg, domClass,
                                    domVersion=constants.SUPPORTED_DOMAIN_VERSIONS[0],
                                    options=None):
        """
        Creates a new storage domain.

        :param storageType: The storage type of the new storage domain (eg. NFS).
        :type storageType: int (as defined in sd.py).
        :param sdUUID: The UUID of the new storage domain.
        :type sdUUID: UUID
        :param domainName: The human readable name of the new storage domain.
        :type domainName: str
        :param typeSpecificArg: Arguments that are specific to the storage type.
        :type typeSpecificArg: dict
        :param domClass: The class of the new storage domain (eg. iso, data).
        :type domClass: int (as defined in sd.py)
        :param options: unused
        """
        msg = ("storageType=%s, sdUUID=%s, domainName=%s, domClass=%s, "
            "typeSpecificArg=%s domVersion=%s" % (storageType, sdUUID, domainName, domClass,
            typeSpecificArg, domVersion))
        domVersion = int(domVersion)
        vars.task.setDefaultException(se.StorageDomainCreationError(msg))
        misc.validateUUID(sdUUID, 'sdUUID')
        self.validateNonDomain(sdUUID)

        if not domClass in sd.DOMAIN_CLASSES.keys():
            raise se.StorageDomainClassError()

        sd.validateDomainVersion(domVersion)

        #getSharedLock(connectionsResource...)
        #getExclusiveLock(sdUUID...)
        SDF.create(sdUUID=sdUUID, storageType=storageType, domainName=domainName,
                domClass=domClass, typeSpecificArg=typeSpecificArg, version=domVersion)


    def public_validateStorageDomain(self, sdUUID, options = None):
        """
        Validates that the storage domain is accessible.

        :param sdUUID: The UUID of the storage domain you want to validate.
        :type sdUUID: UUID
        :param options: ?

        :returns: :keyword:`True` if storage domain is valid.
        :rtype: bool
        """
        vars.task.setDefaultException(se.StorageDomainCreationError("sdUUID=%s" % str(sdUUID)))
        return SDF.produce(sdUUID=sdUUID).validate()


    def public_formatStorageDomain(self, sdUUID, autoDetach = False, options = None):
        """
        Formats a detached storage domain.

        .. warning::
            This removes all data from the storage domain.

        :param sdUUID: The UUID for the storage domain you want to format.
        :type sdUUID: UUID
        :param options: ?

        :returns: Nothing
        """
        vars.task.setDefaultException(se.StorageDomainActionError("sdUUID=%s" % str(sdUUID)))
        #getSharedLock(connectionsResource...)

        vars.task.getExclusiveLock(STORAGE, sdUUID)
        for p in self.pools.values():
            # Avoid format if domain part of connected pool
            domDict = p.getDomains()
            if sdUUID in domDict.keys():
                raise se.CannotFormatStorageDomainInConnectedPool(sdUUID)

        # For domains that attached to disconnected pool, format domain if 'autoDetach' flag set
        if not misc.parseBool(autoDetach):
            # Allow format also for broken domain
            try:
                if len(SDF.produce(sdUUID=sdUUID).getPools()) > 0:
                    raise se.CannotFormatAttachedStorageDomain(sdUUID)

            except (se.StorageDomainMetadataNotFound, se.MetaDataGeneralError, se.MiscFileReadException,
                    se.MiscBlockReadException, se.MiscBlockReadIncomplete), e:
                self.log.warn("Domain %s has problem with metadata. Continue formating... (%s)", sdUUID, str(e))
            except se.CannotFormatAttachedStorageDomain:
                raise
            except Exception:
                self.log.warn("Domain %s can't be formated", sdUUID, exc_info=True)
                raise se.StorageDomainFormatError(sdUUID)

        SDF.recycle(sdUUID=sdUUID)


    def public_setStorageDomainDescription(self, sdUUID, description, options = None):
        """
        Sets a storage domain's description.

        :param sdUUID: The UUID of the storage domain you want to modify.
        :type sdUUID: UUID
        :param description: The new description.
        :type description: str
        :param options: ?
        """
        if len(description) > sd.MAX_DOMAIN_DESCRIPTION_SIZE:
            raise se.StorageDomainDescriptionTooLongError()

        vars.task.setDefaultException(se.StorageDomainActionError("sdUUID=%s, description=%s" % (str(sdUUID), str(description))))
        dom = SDF.produce(sdUUID=sdUUID)
        vars.task.getExclusiveLock(STORAGE, sdUUID)
        dom.setDescription(descr=description)


    def public_getStorageDomainInfo(self, sdUUID, options = None):
        """
        Gets the info of a storage domain.

        :param sdUUID: The UUID of the storage domain you want to get info about.
        :type sdUUID: UUID
        :param options: ?

        :returns: a dict containing the information about the domain.
        :rtype: dict
        """
        vars.task.setDefaultException(se.StorageDomainActionError("sdUUID=%s" % str(sdUUID)))
        self.validateSdUUID(sdUUID)
        #getSharedLock(connectionsResource...)

        vars.task.getSharedLock(STORAGE, sdUUID)
        dom = SDF.produce(sdUUID=sdUUID)
        info = dom.getInfo()
        # This only occurred because someone
        # thought it would be clever to return pool
        # information in the domain.getInfo() method
        # In a perfect world I would have just stopped
        # giving this information in the response.
        # This, of-course breaks backward compatibility.
        # These keys are not likely to change (also because of
        # BC) so it's not that horrible. In any case please
        # remove this when we can stop supporting this API.
        info.update({'lver' : -1, 'spm_id' : -1, 'master_ver' : 0})
        if dom.getDomainRole() == sd.MASTER_DOMAIN:
            # make sure it's THE master
            try:
                pool = self.getPool(dom.getPools()[0])
                master = pool.getMasterDomain()
                if master.sdUUID == sdUUID:
                    poolInfo = pool.getInfo()
                    for key in ['lver', 'spm_id', 'master_ver']:
                        info[key] = poolInfo['info'][key]
            except se.StoragePoolUnknown:
                # Its pool is not connected
                pass

        return dict(info=info)


    def public_getStorageDomainStats(self, sdUUID, options = None):
        """
        Gets a storage domain's statistics.

        :param sdUUID: The UUID of the storage domain that you want to get it's statistics.
        :type sdUUID: UUID
        :param options: ?

        :returns: a dict containing the statistics information.
        :rtype: dict
        """
        vars.task.setDefaultException(se.StorageDomainActionError("sdUUID=%s" % str(sdUUID)))
        vars.task.getSharedLock(STORAGE, sdUUID)
        dom = SDF.produce(sdUUID=sdUUID)
        dom.refresh()
        stats = dom.getStats()
        return dict(stats=stats)


    def public_getStorageDomainsList(self, spUUID = None, domainClass = None, storageType = None, remotePath = None, options = None):
        """
        Returns a List of all or pool specific storage domains.

        :param spUUID: The UUID of the the the storage pool you want to list.
                       If spUUID equals to :attr:`~volume.BLANK_UUID` all pools will be listed.
        :type spUUID: UUID
        :param options: ?

        :returns: a dict containing list of storage domains.
        :rtype: dict
        """
        vars.task.setDefaultException(se.StorageDomainActionError("spUUID: %s" % str(spUUID)))
        SDF.refreshStorage()
        if spUUID and spUUID != volume.BLANK_UUID:
            domList = self.getPool(spUUID).getDomains()
            domains = domList.keys()
        else:
            #getSharedLock(connectionsResource...)
            domains = SDF.getAllUUIDs()

        for sdUUID in domains[:]:
            try:
                dom = SDF.produce(sdUUID=sdUUID)
                # Filter domains according to 'storageType'
                if storageType and storageType != dom.getStorageType():
                    domains.remove(sdUUID)
                    continue
                # Filter domains according to 'domainClass'
                if domainClass and domainClass != dom.getDomainClass():
                    domains.remove(sdUUID)
                    continue
                # Filter domains according to 'remotePath'
                if remotePath and fileUtils.transformPath(remotePath) != dom.getRemotePath():
                    domains.remove(sdUUID)
                    continue
            except Exception:
                self.log.error("Unexpected error", exc_info=True)
                domains.remove(sdUUID)
                continue

        return dict(domlist=domains)

    def __getVGType(self, vg):
        """
        Returns the vg type as sd.DOMAIN_TYPES.

        multipath.DEV_* types with sd.DOMAIN_TYPES coupling.
        """
        try:
            pathtype = lvm.getVGType(vg.name)
        except se.VolumeGeneralException: #Unsupported
            vgtype = sd.DOMAIN_TYPES[sd.UNKNOWN_DOMAIN]
        else:
            vgtype = sd.name2type(pathtype)
        return vgtype

    def __fillVGDict(self, vg):
        """
        Returns the VG dict as required by mgmt.
        """
        vgtype = self.__getVGType(vg)
        vgstate = vg.partial
        #vg.attr._asdict() because nametuples are not pickled
        return {'name':vg.name, 'vgUUID':vg.uuid, 'vgsize':str(vg.size),
                'vgfree':str(vg.free), 'type':vgtype, 'attr':vg.attr._asdict(),
                'state':vgstate}

    def __fillPVDict(self, devInfo, pv, devtype):
        info = {}
        info["vendorID"] = devInfo["vendor"]
        info["productID"] = devInfo["product"]
        info["serial"] = devInfo["serial"]
        info["pathstatus"] = []
        for pathInfo in devInfo['paths']:
            pathInfo["lun"] = pathInfo["hbtl"].lun
            del pathInfo["hbtl"]
            del pathInfo["devnum"]
            info["pathstatus"].append(pathInfo)
        info["pathlist"] = devInfo["connections"]
        info["fwrev"] = "0000"
        info["devtype"] = devtype
        info["capacity"] = str(pv.size)
        info["vgUUID"] = str(pv.vg_uuid)
        info["pvUUID"] = str(pv.uuid)
        info["GUID"] = str(pv.guid)
        return info


    def public_getVGList(self, storageType=None, options = None):
        """
        Returns a list all VGs.

        :param options: ?

        :returns: a dict containing a list of all VGs.
        :rtype: dict
        """
        vars.task.setDefaultException(se.VolumeGroupActionError())
        SDF.refreshStorage()
        #getSharedLock(connectionsResource...)
        vglist = []
        vgs = self.__getVGsInfo()
        for vgInfo in vgs:
            del vgInfo["pvlist"]
            if storageType is not None:
                if vgInfo["type"] != storageType:
                    continue
            vglist.append(vgInfo)

        return dict(vglist=vglist)

    def __getVGsInfo(self, vgUUIDs=None):
        getGuid = lambda pvName: os.path.split(pvName)[-1]
        devNames = []
        vgInfos = []
        vgGuids = {}
        if vgUUIDs is None:
            vgList = lvm.getAllVGs()
        else:
            vgList = [lvm.getVGbyUUID(vgUUID) for vgUUID in vgUUIDs]

        for i, vg in enumerate(vgList):
            #Should be fresh from the cache
            devNames.extend(imap(getGuid, lvm.listPVNames(vg.name)))
            #vg.attr._asdict() because nametuples are not pickled
            vgInfo = {'name': vg.name, 'vgUUID': vg.uuid, 'vgsize': str(vg.size),
                      'vgfree': str(vg.free), 'type': "", 'attr': vg.attr._asdict(),
                      'state': vg.partial, "pvlist": [] }
            vgInfos.append(vgInfo)
            vgGuids[vg.uuid] = i

        pathDict = {}
        for dev in multipath.pathListIter(devNames):
            pathDict[dev["guid"]] = dev

        self.__processVGInfos(vgInfos, pathDict, getGuid)

        return vgInfos

    def __processVGInfos(self, vgInfos, pathDict, getGuid):
        vgType = None
        for vgInfo in vgInfos:
            for pv in lvm.listPVNames(vgInfo['name']):
                dev = pathDict.get(getGuid(pv))
                if dev is None:
                    self.log.warn("dev %s was not found %s", getGuid(pv), pathDict)
                    continue
                if vgType is None:
                    vgType = dev["devtype"]
                elif vgType != multipath.DEV_MIXED and vgType != dev["devtype"]:
                    vgType = multipath.DEV_MIXED

                pvInfo = lvm.getPV(pv)
                vgInfo['pvlist'].append(self.__fillPVDict(dev, pvInfo, vgType))

            if vgType == multipath.DEV_FCP:
                vgType = sd.FCP_DOMAIN
            elif vgType == multipath.DEV_ISCSI:
                vgType = sd.ISCSI_DOMAIN
            else: #TODO: Allow for mixed vgs to be specified as such in the API
                vgType = sd.ISCSI_DOMAIN

            vgInfo["type"] = vgType


    def public_getVGInfo(self, vgUUID, options = None):
        """
        Gets the info of a VG.

        :param vgUUID: The UUID of the VG.
        :type vgUUID: UUID
        :param options: ?

        :returns: a dict containing the info about the VG.
        :rtype: dict

        :raises: :exc:`storage_exception.VolumeGroupDoesNotExist`
                 if no VG with the specified UUID is found
        """
        vars.task.setDefaultException(se.VolumeGroupActionError("%s" % str(vgUUID)))
        #getSharedLock(connectionsResource...)
        return dict(info=self.__getVGsInfo([vgUUID])[0])

    def _log_discoverSendTargets(self, con, options = None):
        cons = storage_connection.StorageServerConnection.loggableConList(conList=[con])
        return "con=%s, options=%s" % (cons[0], options)

    @staticmethod
    def public_discoverSendTargets(con, options = None):
        """
        Discovers iSCSI targets.

        :param con: A dict containing connection information of some sort.?
        :type con: dict?
        :param options: ?

        :returns: a dict containing the send targets that were discovered.
        :rtype: dict
        """
        #vars.task.setDefaultException(se.ChangeMeError("%s" % str(args)))
        #getSharedLock(connectionsResource...)
        ip = con['connection']
        port = con['port']
        username = con['user']
        password = con['password']
        if username == "":
            username = password = None
        # This call to validateiSCSIParam() is not really needed,
        # since the first thing discoverSendTargets() is doing is calling
        # this validator (Similar to all the other iscsi functions.
        # We may revisit our strategy (where to validate parameters) later.
        # So I do not remove this call, but comment it out for now
        # iscsi.validateiSCSIParams(ip=ip, port=port, username=username, password=password)
        targets = iscsi.discoverSendTargets(ip=ip, port=port, username=username, password=password)
        partialTargets = [target.split()[1] for target in targets]

        return dict(targets=partialTargets, fullTargets=targets)


    def public_cleanupUnusedConnections(self, options = None):
        """
        .. warning::
            This method is not yet implemented.
        """
        #vars.task.setDefaultException(se.ChangeMeError("%s" % str(args)))
        #getExclusiveLock(connectionsResource...)
        # TODO: Implement
        pass


    def public_refreshVolume(self, sdUUID, spUUID, imgUUID, volUUID):
        """
        Refresh low level volume after change in the shared storage initiated from another host
        :param sdUUID: The UUID of the storage domain that owns the volume.
        :type sdUUID: UUID
        :param spUUID: The UUID of the storage pool that owns the volume.
        :type spUUID: UUID
        :param imgUUID: The UUID of the image contained on the volume.
        :type imgUUID: UUID
        :param volUUID: The UUID of the volume you want to refresh.
        :type volUUID: UUID

        :returns: Nothing ? Stuff not implemented
        """
        self.validatePoolSD(spUUID, sdUUID)

        vars.task.getSharedLock(STORAGE, sdUUID)
        return SDF.produce(sdUUID=sdUUID).produceVolume(imgUUID=imgUUID, volUUID=volUUID).refreshVolume()


    def public_getVolumeSize(self, sdUUID, spUUID, imgUUID, volUUID, options = None):
        """
        Gets the size of a volume.

        :param sdUUID: The UUID of the storage domain that owns the volume.
        :type sdUUID: UUID
        :param spUUID: The UUID of the storage pool that owns the volume.
        :type spUUID: UUID
        :param imgUUID: The UUID of the image contained on the volume.
        :type imgUUID: UUID
        :param volUUID: The UUID of the volume you want to know the size of.
        :type volUUID: UUID
        :param options: ?

        :returns: a dict with the size of the volume.
        :rtype: dict
        """
        self.validatePoolSD(spUUID, sdUUID)

        vars.task.getSharedLock(STORAGE, sdUUID)
        # Return string because xmlrpc's "int" is very limited
        apparentsize = str(volume.Volume.getVSize(sdUUID, imgUUID, volUUID, bs=1))
        truesize = str(volume.Volume.getVTrueSize(sdUUID, imgUUID, volUUID, bs=1))
        return dict(apparentsize=apparentsize, truesize=truesize)


    def public_getVolumeInfo(self, sdUUID, spUUID, imgUUID, volUUID, options = None):
        """
        Gets a volume's info.

        :param sdUUID: The UUID of the storage domain that owns the volume.
        :type sdUUID: UUID
        :param spUUID: The UUID of the storage pool that owns the volume.
        :type spUUID: UUID
        :param imgUUID: The UUID of the image contained on the volume.
        :type imgUUID: UUID
        :param volUUID: The UUID of the volume you want to get the info on.
        :type volUUID: UUID
        :param options: ?

        :returns: a dict with the info of the volume.
        :rtype: dict
        """
        #vars.task.setDefaultException(se.ChangeMeError("%s" % str(args)))
        self.validatePoolSD(spUUID, sdUUID)

        vars.task.getSharedLock(STORAGE, sdUUID)
        info = SDF.produce(sdUUID=sdUUID).produceVolume(imgUUID=imgUUID, volUUID=volUUID).getInfo()
        return dict(info=info)


    def public_getVolumePath(self, sdUUID, spUUID, imgUUID, volUUID, options = None):
        """
        Gets the path to a volume.

        :param sdUUID: The UUID of the storage domain that owns the volume.
        :type sdUUID: UUID
        :param spUUID: The UUID of the storage pool that owns the volume.
        :type spUUID: UUID
        :param imgUUID: The UUID of the image contained on the volume.
        :type imgUUID: UUID
        :param volUUID: The UUID of the volume you want to get it's path.
        :type volUUID: UUID
        :param options: ?

        :returns: a dict with the path to the volume.
        :rtype: dict
        """
        self.validatePoolSD(spUUID, sdUUID)

        vars.task.getSharedLock(STORAGE, sdUUID)
        path = SDF.produce(sdUUID=sdUUID).produceVolume(imgUUID=imgUUID, volUUID=volUUID).getVolumePath()
        return dict(path=path)


    def public_prepareVolume(self, sdUUID, spUUID, imgUUID, volUUID, rw=True, options = None):
        """
        Prepares a volume (used in SAN).
        Activates LV and rebuilds 'images' subtree.

        :param sdUUID: The UUID of the storage domain that owns the volume.
        :type sdUUID: UUID
        :param spUUID: The UUID of the storage pool that owns the volume.
        :type spUUID: UUID
        :param imgUUID: The UUID of the image contained on the volume.
        :type imgUUID: UUID
        :param volUUID: The UUID of the volume you want to prepare.
        :type volUUID: UUID
        :param rw: Should the voulme be set as RW. ?
        :type rw: bool
        :param options: ?
        """
        self.validatePoolSD(spUUID, sdUUID)

        vars.task.getSharedLock(STORAGE, sdUUID)
        imageResourcesNamespace = sd.getNamespace(sdUUID, IMAGE_NAMESPACE)
        imgResource = rmanager.acquireResource(imageResourcesNamespace, imgUUID, rm.LockType.exclusive)
        imgResource.autoRelease = False
        try:
            vol = SDF.produce(sdUUID=sdUUID).produceVolume(imgUUID=imgUUID, volUUID=volUUID)
            # NB We want to be sure that at this point HSM does not use stale LVM
            # cache info, so we call refresh explicitely. We may want to remove
            # this refresh later, when we come up with something better.
            vol.refreshVolume()
            vol.prepare(rw=rw)
        except:
            imgResource.autoRelease = True
            self.log.error("Prepare volume %s in domain %s failed", volUUID, sdUUID, exc_info=True)
            raise


    def public_teardownVolume(self, sdUUID, spUUID, imgUUID, volUUID, rw=False, options = None):
        """
        Tears down a volume (used in SAN).
        Deactivates LV.

        :param sdUUID: The UUID of the storage domain that owns the volume.
        :type sdUUID: UUID
        :param spUUID: The UUID of the storage pool that owns the volume.
        :type spUUID: UUID
        :param imgUUID: The UUID of the image contained on the volume.
        :type imgUUID: UUID
        :param volUUID: The UUID of the volume you want to teardown.
        :type volUUID: UUID
        :param rw: Should the voulme be set as RW. ?
        :type rw: bool
        :param options: ?
        """
        self.validatePoolSD(spUUID, sdUUID)

        vars.task.getSharedLock(STORAGE, sdUUID)
        volclass = SDF.produce(sdUUID).getVolumeClass()
        volclass.teardown(sdUUID=sdUUID, volUUID=volUUID)
        imageResourcesNamespace = sd.getNamespace(sdUUID, IMAGE_NAMESPACE)
        rmanager.releaseResource(imageResourcesNamespace, imgUUID)


    def public_getVolumesList(self, sdUUID, spUUID, imgUUID=volume.BLANK_UUID, options = None):
        """
        Gets a list of all volumes.

        :param spUUID: The UUID of the storage pool that manages the storage domain you want to query.
        :type spUUID: UUID
        :param sdUUID: The UUID of the storage domain you want to query.
        :type sdUUID: UUID
        :param imgUUID: The UUID of the an image you want to filter the results.
                        if imgUUID equals :attr:`~volume.BLANK_UUID` no filtering will be done.
        """
        self.validatePoolSD(spUUID, sdUUID)

        vars.task.getSharedLock(STORAGE, sdUUID)
        dom = SDF.produce(sdUUID=sdUUID)
        if imgUUID == volume.BLANK_UUID:
            images = dom.getAllImages()
        else:
            images = [imgUUID]

        uuidlist = []
        repoPath = os.path.join(self.storage_repository, spUUID)
        for img in images:
            uuidlist += dom.getVolumeClass().getImageVolumes(repoPath, sdUUID, img)
        self.log.info("List of volumes is %s", str(uuidlist))
        return dict(uuidlist=uuidlist)


    def public_getImagesList(self, sdUUID, options = None):
        """
        Gets a list of all the images of specific domain.

        :param sdUUID: The UUID of the storage domain you want to query.
        :type sdUUID: UUID.
        :param options: ?

        :returns: a dict with a list of the images belonging to the specified domain.
        :rtype: dict
        """
        vars.task.getSharedLock(STORAGE, sdUUID)
        imageslist = SDF.produce(sdUUID=sdUUID).getAllImages()
        return dict(imageslist=imageslist)


    def public_getImageDomainsList(self, spUUID, imgUUID, datadomains=True, options = None):
        """
        Gets a list of all domains in the pool that contains imgUUID.

        :param spUUID: The UUID of the storage pool you want to query.
        :type spUUID: UUID
        :param imgUUID: The UUID of the image you want to filter by.
        :type spUUID: UUID
        :param datadomains: Should the search only be limited to only data domains.
        :type datadomains: bool
        :param options: ?

        :returns: a dict containing the list of domains found.
        :rtype: dict
        """
        vars.task.setDefaultException(se.GetStorageDomainListError("spUUID=%s imgUUID=%s" % (str(spUUID), str(imgUUID))))
        vars.task.getSharedLock(STORAGE, spUUID)
        pool = self.getPool(spUUID)
        # Find out domain list from the pool metadata
        domList = sorted(pool.getDomains().keys())
        for sdUUID in domList:
            vars.task.getSharedLock(STORAGE, sdUUID)

        domainslist = pool.getImageDomainsList(imgUUID=imgUUID, datadomains=datadomains)
        return dict(domainslist=domainslist)


    def public_prepareForShutdown(self, options = None):
        """
        Prepares to shutdown host.
        Stops all tasks.

        .. note::
            shutdown cannot be cancelled, must stop all actions.

        :param options: ?
        """
        # TODO: Implement!!!! TBD: required functionality (stop hsm tasks, stop spm tasks if spm etc.)
        try:
            self.spm.prepareForShutdown()
            # Stop hsmMailer and repoStat threads
            for spUUID in self.pools:
                # Stop hsmMailer thread
                if self.pools[spUUID].hsmMailer:
                    self.pools[spUUID].hsmMailer.stop()

                # Stop repoStat threads
                try:
                    domDict = self.pools[spUUID].getDomains(activeOnly=True)
                except Exception:
                    self.log.warning("Failed to get domains list", exc_info=True)
                    continue

                for dom in domDict:
                    try:
                        self.pools[spUUID].stopRepoStats(dom)
                    except Exception:
                        self.log.warning("Failed to stop RepoStats thread", exc_info=True)
                        continue

            self.taskMng.prepareForShutdown()
        except:
            pass


    def public_repoStats(self, options = None):
        """
        Collects a storage repository's information and stats.

        :param options: ?

        :returns: result
        """
        result = {}
        for p in self.pools.values():
            # Find the master domains
            try:
                master = p.getMasterDomain()
            except se.StorageException:
                self.log.error("Unexpected error", exc_info=True)
                master = None
            # Get the stats results
            repo_stats = p.getRepoStats()

            # Master requires extra post processing, since
            # this is the only place where it makes sense that we are
            # connected to the pool yet the master domain is not available,
            # seeing as the purpose of this method is to monitor
            # domains' health.
            # There are situations in the life cycle of Storage Pool when
            # its master domain is inactive (i.e. attached, but not active),
            # while the pool itself is connected. In that case there would
            # be no stats collected for it, so just skip extra validation.
            # NB This is not a shallow copy !
            master_stats = repo_stats.get(master.sdUUID)
            if master and master_stats:

                # Master validation makes sense for SPM only
                # So we should analyze the 'getRepoStats' return value
                if self.spm.isActive(contend=False):
                    # The SPM case
                    valid = (master_stats['masterValidate']['mount'] and
                        master_stats['masterValidate']['valid'])
                else:
                    # The HSM case
                    valid = not (master_stats['masterValidate']['mount'] and
                        isinstance(master, blockSD.BlockStorageDomain))

                if not valid:
                    self.log.warning("repoStats detected invalid master:%s %s",
                        master.sdUUID, master_stats)
                    if int(master_stats['result']['code']) == 0:
                        master_stats['result']['code'] = se.StorageDomainMasterError.code

            # Copy the 'result' out
            for d in repo_stats:
                result[d] = repo_stats[d]['result']

        return result
