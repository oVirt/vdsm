#
# Copyright 2009-2012 Red Hat, Inc.
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

"""
This is the Host Storage Manager module.
"""

import os
import threading
import logging
import glob
from fnmatch import fnmatch
from copy import deepcopy
from itertools import imap
from collections import defaultdict
from functools import partial, wraps
import errno
import time
import signal
import types
import math
import stat

from vdsm.config import config
import sp
import sd
import blockSD
import nfsSD
import glusterSD
import localFsSD
import lvm
import fileUtils
import multipath
import outOfProcess as oop
from sdc import sdCache
import image
import volume
import iscsi
import misc
from misc import deprecated
import taskManager
import clusterlock
import storage_exception as se
from threadLocal import vars
from vdsm import constants
from storageConstants import STORAGE
from task import Job
from resourceFactories import IMAGE_NAMESPACE
import resourceManager as rm
import devicemapper
import logUtils
import mount
import dispatcher
import supervdsm
import storageServer
from vdsm import utils
from vdsm import qemuImg

GUID = "guid"
NAME = "name"
UUID = "uuid"
TYPE = "type"
INITIALIZED = "initialized"
CAPACITY = "capacity"
PATHLIST = "pathlist"

logged = partial(
    logUtils.logcall, "dispatcher", "Run and protect: %s",
    resPattern="Run and protect: %(name)s, Return response: %(result)s")

rmanager = rm.ResourceManager.getInstance()

# FIXME: moved from spm.py but this should be somewhere else
SECTOR_SIZE = 512

STORAGE_CONNECTION_DIR = os.path.join(constants.P_VDSM_LIB, "connections/")

QEMU_READABLE_TIMEOUT = 30


def public(f=None, **kwargs):
    if f is None:
        return partial(public, **kwargs)

    publicFunctionLogger = kwargs.get("logger", logged())

    return dispatcher.exported(wraps(f)(publicFunctionLogger(f)))


def loggableCon(con):
    conCopy = con.copy()
    for key in conCopy:
        if key.upper() == 'PASSWORD':
            conCopy[key] = '******'
    return conCopy


def loggableConList(conList):
    cons = []
    for con in conList:
        conCopy = loggableCon(con)
        cons.append(conCopy)

    return cons


def connectionListPrinter(conList):
    return repr(loggableConList(conList))


def connectionPrinter(con):
    return repr(loggableCon(con))

# Connection Management API competability code
# Remove when deprecating dis\connectStorageServer

CON_TYPE_ID_2_CON_TYPE = {
    sd.LOCALFS_DOMAIN: 'localfs',
    sd.NFS_DOMAIN: 'nfs',
    sd.ISCSI_DOMAIN: 'iscsi',
    # FCP domain shouldn't even be on the list but VDSM use to just
    # accept this type as iscsi so we are stuck with it
    sd.FCP_DOMAIN: 'iscsi',
    sd.POSIXFS_DOMAIN: 'posixfs',
    sd.GLUSTERFS_DOMAIN: 'glusterfs'}


def _BCInitiatorNameResolve(ifaceName):
    if not ifaceName:
        return iscsi.IscsiInterface('default')

    for iface in iscsi.iterateIscsiInterfaces():
        if iface.name == ifaceName:
            return iface

    iface = iscsi.IscsiInterface(ifaceName, initiatorName=ifaceName)
    iface.create()
    return iface


def _connectionDict2ConnectionInfo(conTypeId, conDict):
    def getIntParam(optDict, key, default):
        res = optDict.get(key, default)
        if res is None:
            return res

        try:
            return int(res)
        except ValueError:
            raise se.InvalidParameterException(key, res)

    # FIXME: Remove when nfs_mount_options is no longer supported.  This is
    # in the compatibility layer so that the NFSConnection class stays clean.
    # Engine options have precendence, so use deprecated nfs_mount_options
    # only if engine passed nothing (indicated by default params of 'None').
    def tryDeprecatedNfsParams(conDict):
        if (conDict.get('protocol_version', None),
                conDict.get('retrans', None),
                conDict.get('timeout', None)) == (None, None, None):
            conf_options = config.get(
                'irs', 'nfs_mount_options').replace(' ', '')
            if (frozenset(conf_options.split(',')) !=
                    frozenset(storageServer.NFSConnection.DEFAULT_OPTIONS)):
                logging.warning("Using deprecated nfs_mount_options from"
                                " vdsm.conf to mount %s: %s",
                                conDict.get('connection', '(unknown)'),
                                conf_options)
                return storageServer.PosixFsConnectionParameters(
                    conDict.get('connection', None), 'nfs', conf_options)
        return None

    typeName = CON_TYPE_ID_2_CON_TYPE[conTypeId]
    if typeName == 'localfs':
        params = storageServer.LocaFsConnectionParameters(
            conDict.get('connection', None))
    elif typeName == 'nfs':
        params = tryDeprecatedNfsParams(conDict)
        if params is not None:
            # Hack to support vdsm.conf nfs_mount_options
            typeName = 'posixfs'
        else:
            version = conDict.get('protocol_version', "3")
            version = str(version)
            if version == "auto":
                version = None

            params = storageServer.NfsConnectionParameters(
                conDict.get('connection', None),
                getIntParam(conDict, 'retrans', None),
                getIntParam(conDict, 'timeout', None),
                version)
    elif typeName == 'posixfs':
        params = storageServer.PosixFsConnectionParameters(
            conDict.get('connection', None),
            conDict.get('vfs_type', None),
            conDict.get('mnt_options', None))
    elif typeName == 'glusterfs':
        params = storageServer.GlusterFsConnectionParameters(
            conDict.get('connection', None),
            conDict.get('vfs_type', None),
            conDict.get('mnt_options', None))
    elif typeName == 'iscsi':
        portal = iscsi.IscsiPortal(
            conDict.get('connection', None),
            int(conDict.get('port', None)))
        tpgt = None

        target = iscsi.IscsiTarget(portal, tpgt, conDict.get('iqn', None))

        iface = _BCInitiatorNameResolve(conDict.get('initiatorName', None))

        cred = None
        username = conDict.get('user', None)
        password = conDict.get('password', None)
        if username or password:
            cred = iscsi.ChapCredentials(username, password)

        params = storageServer.IscsiConnectionParameters(target, iface, cred)
    else:
        raise se.StorageServerActionError()

    return storageServer.ConnectionInfo(typeName, params)


class HSM:
    """
    This is the HSM class. It controls all the stuff relate to the Host.
    Further more it doesn't change any pool metadata.

    .. attribute:: tasksDir

        A string containing the path of the directory where backups of tasks a
        saved on the disk.
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
        sdDom = sdCache.produce(sdUUID=sdUUID)
        sdDom.validate()
        return sdDom

    @classmethod
    def validateBackupDom(cls, sdUUID):
        """
        Validates a backup domain.

        :param sdUUID: the UUID of the storage domain you want to validate.
        :type sdUUID: UUID

        If the domain doesn't exist an exception will be thrown.
        If the domain isn't a backup domain a
        :exc:`storage_exception.StorageDomainTypeNotBackup` exception
        will be raised.
        """
        if not sdCache.produce(sdUUID=sdUUID).isBackup():
            raise se.StorageDomainTypeNotBackup(sdUUID)

    @classmethod
    def validateNonDomain(cls, sdUUID):
        """
        Validates that there is no domain with this UUID.

        :param sdUUID: The UUID to test.
        :type sdUUID: UUID

        :raises: :exc:`storage_exception.StorageDomainAlreadyExists` exception
        if a domain with this UUID exists.
        """
        try:
            sdCache.produce(sdUUID=sdUUID)
            raise se.StorageDomainAlreadyExists(sdUUID)
        # If partial metadata exists the method will throw MetadataNotFound.
        # Though correct the logical response in this context
        # is StorageDomainNotEmpty.
        except se.StorageDomainMetadataNotFound:
            raise se.StorageDomainNotEmpty()
        except se.StorageDomainDoesNotExist:
            pass

    def validateSPM(self, spUUID):
        pool = self.getPool(spUUID)
        if pool.spmRole != sp.SPM_ACQUIRED:
            raise se.SpmStatusError(spUUID)

    def validateNotSPM(self, spUUID):
        pool = self.getPool(spUUID)
        if pool.spmRole != sp.SPM_FREE:
            raise se.IsSpm(spUUID)

    @classmethod
    def getPool(cls, spUUID):
        if spUUID not in cls.pools:
            raise se.StoragePoolUnknown(spUUID)
        return cls.pools[spUUID]

    def __init__(self):
        """
        The HSM Constructor

        :param defExcFunc: The function that will set the default exception
                           for this thread
        :type defExcFun: function
        """
        rm.ResourceManager.getInstance().registerNamespace(
            STORAGE, rm.SimpleResourceFactory())
        self.storage_repository = config.get('irs', 'repository')
        self.sd_validate_timeout = config.getint('irs', 'sd_validate_timeout')
        self.taskMng = taskManager.TaskManager()

        mountBasePath = os.path.join(self.storage_repository,
                                     sd.DOMAIN_MNT_POINT)
        fileUtils.createdir(mountBasePath)
        storageServer.MountConnection.setLocalPathBase(mountBasePath)
        storageServer.LocalDirectoryConnection.setLocalPathBase(mountBasePath)
        self._connectionAliasRegistrar = \
            storageServer.ConnectionAliasRegistrar(STORAGE_CONNECTION_DIR)
        self._connectionMonitor = \
            storageServer.ConnectionMonitor(self._connectionAliasRegistrar)
        self._connectionMonitor.startMonitoring()

        sp.StoragePool.cleanupMasterMount()
        self.__releaseLocks()

        self._preparedVolumes = defaultdict(list)

        if not multipath.isEnabled():
            multipath.setupMultipath()

        self.__validateLvmLockingType()

        # cleanStorageRepoitory uses tasksDir value, this must be assigned
        # before calling it
        self.tasksDir = config.get('irs', 'hsm_tasks')

        # This part should be in same thread to prevent race on mounted path,
        # otherwise, storageRefresh can unlink path that is used by another
        # thread that was initiated in the same time and tried to use the
        # same link.
        try:
            # This call won't get stuck if mount is inaccessible thanks to
            # misc.walk, this sync call won't delay hsm initialization.
            self.__cleanStorageRepository()
        except Exception:
            self.log.warn("Failed to clean Storage Repository.", exc_info=True)

        def storageRefresh():
            lvm._lvminfo.bootstrap()
            sdCache.refreshStorage()

            fileUtils.createdir(self.tasksDir)
            # TBD: Should this be run in connectStoragePool? Should tasksDir
            # exist under pool link as well (for hsm tasks)
            self.taskMng.loadDumpedTasks(self.tasksDir)
            self.taskMng.recoverDumpedTasks()

            _poolsTmpDir = config.get('irs', 'pools_data_dir')
            dirList = os.listdir(_poolsTmpDir)
            for spUUID in dirList:
                poolPath = os.path.join(self.storage_repository, spUUID)
                try:
                    if os.path.exists(poolPath):
                        self._connectStoragePool(spUUID, None,
                                                 None, None, None)
                        # TODO Once we support simultaneous connection to
                        # multiple pools, remove following line (break)
                        break
                except Exception:
                    self.log.error("Unexpected error", exc_info=True)

        storageRefreshThread = threading.Thread(target=storageRefresh,
                                                name="storageRefresh")
        storageRefreshThread.daemon = True
        storageRefreshThread.start()

    def __validateLvmLockingType(self):
        """
        Check lvm locking type.
        """
        rc, out, err = misc.execCmd([constants.EXT_LVM, "dumpconfig",
                                     "global/locking_type"],
                                    sudo=True)
        if rc != 0:
            self.log.error("Can't validate lvm locking_type. %d %s %s",
                           rc, out, err)
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
        Cleanup the storage repository leftovers
        """

        self.log.debug("Started cleaning storage "
                       "repository at '%s'", self.storage_repository)

        mountList = []
        whiteList = [
            self.tasksDir,
            os.path.join(self.tasksDir, "*"),
            os.path.join(self.storage_repository, 'mnt'),
        ]

        def isInWhiteList(path):
            fullpath = os.path.abspath(path)

            # The readlink call doesn't follow nested symlinks like
            # realpath but it doesn't hang on inaccessible mount points
            if os.path.islink(fullpath):
                symlpath = os.readlink(fullpath)

                # If any os.path.join component is an absolute path all the
                # previous paths will be discarded; therefore symlpath will
                # be used when it is an absolute path.
                basepath = os.path.dirname(fullpath)
                fullpath = os.path.abspath(os.path.join(basepath, symlpath))

            # Taking advantage of the any lazy evaluation
            return any(fnmatch(fullpath, x) for x in whiteList)

        # Add mounted folders to mountlist
        for mnt in mount.iterMounts():
            mountPoint = os.path.abspath(mnt.fs_file)
            if mountPoint.startswith(self.storage_repository):
                mountList.append(mountPoint)

        self.log.debug("White list: %s", whiteList)
        self.log.debug("Mount list: %s", mountList)

        self.log.debug("Cleaning leftovers")
        rmDirList = []

        # We can't list files form top to bottom because the process
        # would descend into mountpoints and an unreachable NFS storage
        # could freeze the vdsm startup. Since we will ignore files in
        # mounts anyway using out of process file operations is useless.
        # We just clean all directories before removing them from the
        # innermost to the outermost.
        for base, dirs, files in misc.walk(self.storage_repository,
                                           blacklist=mountList):
            for directory in dirs:
                fullPath = os.path.join(base, directory)

                if isInWhiteList(fullPath):
                    dirs.remove(directory)
                else:
                    rmDirList.insert(0, os.path.join(base, fullPath))

            for fname in files:
                fullPath = os.path.join(base, fname)

                if isInWhiteList(fullPath):
                    continue

                try:
                    os.unlink(os.path.join(base, fullPath))
                except Exception:
                    self.log.warn("Cold not delete file "
                                  "'%s'", fullPath, exc_info=True)

        for directory in rmDirList:
            try:
                # os.walk() can see a link to a directory as a directory
                if os.path.islink(directory):
                    os.unlink(directory)
                else:
                    os.rmdir(directory)
            except Exception:
                self.log.warn("Cold not delete directory "
                              "'%s'", directory, exc_info=True)

        self.log.debug("Finished cleaning storage "
                       "repository at '%s'", self.storage_repository)

    @public
    def getConnectedStoragePoolsList(self, options=None):
        """
        Get a list of all the connected storage pools.

        :param options: Could be one or more of the following:
                * OptionA - A good option. Chosen by most
                * OptionB - A much more complex option. Only for the brave

        :type options: list
        """
        vars.task.setDefaultException(se.StoragePoolActionError())

        return dict(poollist=self.pools.keys())

    @public
    def spmStart(self, spUUID, prevID, prevLVER, recoveryMode, scsiFencing,
                 maxHostID=clusterlock.MAX_HOST_ID, domVersion=None,
                 options=None):
        """
        Starts an SPM.

        :param spUUID: The storage pool you want managed.
        :type spUUID: UUID
        :param prevID: The previous ID of the SPM that managed this pool.
        :type prevID: int
        :param prevLVER: The previous version of the pool that was managed by
                         the SPM.
        :type prevLVER: int
        :param recoveryMode: The mode in which to recover the SPM.
        :type recoveryMode: RecoveryEnum?
        :param scsiFencing: Do you want to fence the scsi.
        :type scsiFencing: bool
        :param maxHostID: The maximum Host ID in the cluster.
        :type maxHostID: int
        :param options: unused

        :returns: The UUID of the started task.
        :rtype: UUID
        """

        argsStr = ("spUUID=%s, prevID=%s, prevLVER=%s, recoveryMode=%s, "
                   "scsiFencing=%s, maxHostID=%s, domVersion=%s" %
                   (spUUID, prevID, prevLVER, recoveryMode, scsiFencing,
                    maxHostID, domVersion))
        vars.task.setDefaultException(se.SpmStartError("%s" % (argsStr)))

        if domVersion is not None:
            domVersion = int(domVersion)
            sd.validateDomainVersion(domVersion)

        # This code is repeated twice for performance reasons
        # Avoid waiting for the lock for validate.
        self.getPool(spUUID)
        self.validateNotSPM(spUUID)

        vars.task.getExclusiveLock(STORAGE, spUUID)
        pool = self.getPool(spUUID)
        # We should actually just return true if we are SPM after lock,
        # but seeing as it would break the API with Engine,
        # it's easiest to fail.
        self.validateNotSPM(spUUID)

        vars.task.setTag("hsm")
        vars.task.setManager(self.taskMng)
        vars.task.setRecoveryPolicy("auto")
        vars.task.addJob(Job("spmStart", pool.startSpm, prevID, prevLVER,
                             scsiFencing, maxHostID, domVersion))

    @public
    def spmStop(self, spUUID, options=None):
        """
        Stops the SPM functionality.

        :param spUUID: The UUID of the storage pool you want to
                       stop it manager.
        :type spUUID: UUID
        :param options: ?

        :raises: :exc:`storage_exception.TaskInProgress`
                 if there are tasks running for this pool.

        """
        vars.task.setDefaultException(se.SpmStopError(spUUID))
        vars.task.getExclusiveLock(STORAGE, spUUID)

        pool = self.getPool(spUUID)
        pool.stopSpm()

    @public
    def getSpmStatus(self, spUUID, options=None):
        pool = self.getPool(spUUID)
        try:
            status = {'spmStatus': pool.spmRole, 'spmLver': pool.getSpmLver(),
                      'spmId': pool.getSpmId()}
        except (se.LogicalVolumeRefreshError, IOError):
            # This happens when we cannot read the MD LV
            self.log.error("Can't read LV based metadata", exc_info=True)
            raise se.StorageDomainMasterError("Can't read LV based metadata")
        except se.StorageException as e:
            self.log.error("MD read error: %s", str(e), exc_info=True)
            raise se.StorageDomainMasterError("MD read error")
        except (KeyError, ValueError):
            self.log.error("Non existent or invalid MD key", exc_info=True)
            raise se.StorageDomainMasterError("Version or spm id invalid")

        return dict(spm_st=status)

    @public
    def extendVolume(self, sdUUID, spUUID, imgUUID, volumeUUID, size,
                     isShuttingDown=None, options=None):
        """
        Extends an existing volume.

        .. note::
            This method is valid for SAN only.

        :param sdUUID: The UUID of the storage domain that contains the volume.
        :type sdUUID: UUID
        :param spUUID: The UUID of the storage pool that contains the volume.
        :type spUUID: UUID
        :param imgUUID: The UUID of the new image that is contained
                        on the volume.
        :type imgUUID: UUID
        :param volumeUUID: The UUID of the volume you want to extend.
        :type volumeUUID: UUID
        :param size: Target volume size in MB (desired final size, not by
                     how much to increase)
        :type size: number (anything parsable by int(size))
        :param isShuttingDown: ?
        :type isShuttingDown: bool
        :param options: ?
        """
        vars.task.setDefaultException(
            se.VolumeExtendingError(
                "spUUID=%s, sdUUID=%s, volumeUUID=%s, size=%s" %
                (spUUID, sdUUID, volumeUUID, size)))
        size = misc.validateN(size, "size")
        # ExtendVolume expects size in MB
        size = math.ceil(size / 2 ** 20)

        pool = self.getPool(spUUID)
        pool.validatePoolSD(sdUUID)
        pool.extendVolume(sdUUID, volumeUUID, size, isShuttingDown)

    @public
    def extendVolumeSize(self, spUUID, sdUUID, imgUUID, volUUID, newSize):
        pool = self.getPool(spUUID)
        newSizeBytes = misc.validateN(newSize, "newSize")
        newSizeSectors = (newSizeBytes + SECTOR_SIZE - 1) / SECTOR_SIZE
        vars.task.getSharedLock(STORAGE, sdUUID)
        self._spmSchedule(
            spUUID, "extendVolumeSize", pool.extendVolumeSize, sdUUID,
            imgUUID, volUUID, newSizeSectors)

    @public
    def updateVolumeSize(self, spUUID, sdUUID, imgUUID, volUUID, newSize):
        """
        Update the volume size with the given newSize (in bytes).

        This synchronous method is intended to be used only with COW volumes
        where the size can be updated simply changing the qcow2 header.
        """
        newSizeBytes = int(newSize)
        domain = self.validateSdUUID(sdUUID)
        volToExtend = domain.produceVolume(imgUUID, volUUID)
        volPath = volToExtend.getVolumePath()
        volFormat = volToExtend.getFormat()

        if not volToExtend.isLeaf():
            raise se.VolumeNonWritable(self.volUUID)

        if volFormat != volume.COW_FORMAT:
            # This method is used only with COW volumes (see docstring),
            # for RAW volumes we just return the volume size.
            return dict(size=str(self.getVolumeSize(bs=1)))

        qemuImgFormat = volume.fmt2str(volume.COW_FORMAT)

        volToExtend.prepare()
        try:
            imgInfo = qemuImg.info(volPath, qemuImgFormat)
            if imgInfo['virtualsize'] > newSizeBytes:
                self.log.error(
                    "volume %s size %s is larger than the size requested "
                    "for the extension %s", volUUID, imgInfo['virtualsize'],
                    newSizeBytes)
                raise se.VolumeResizeValueError(str(newSizeBytes))
            # Uncommit the current size
            volToExtend.setSize(0)
            qemuImg.resize(volPath, newSizeBytes, qemuImgFormat)
            roundedSizeBytes = qemuImg.info(volPath,
                                            qemuImgFormat)['virtualsize']
        finally:
            volToExtend.teardown(sdUUID, volUUID)

        volToExtend.setSize(
            (roundedSizeBytes + SECTOR_SIZE - 1) / SECTOR_SIZE)

        return dict(size=str(roundedSizeBytes))

    @public
    def extendStorageDomain(self, sdUUID, spUUID, devlist,
                            force=False, options=None):
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
        vars.task.setDefaultException(
            se.StorageDomainActionError(
                "sdUUID=%s, devlist=%s" % (sdUUID, devlist)))

        vars.task.getSharedLock(STORAGE, sdUUID)
        # We need to let the domain to extend itself
        pool = self.getPool(spUUID)
        pool.validatePoolSD(sdUUID)
        pool.extendSD(sdUUID, devlist, force)

    @public
    def forcedDetachStorageDomain(self, sdUUID, spUUID, options=None):
        """Forced detach a storage domain from a storage pool.
           This removes the storage domain entry in the storage pool meta-data
           and leaves the storage domain in 'unattached' status.
           This action can only be performed on regular (i.e. non master)
           domains.
        """
        vars.task.setDefaultException(
            se.StorageDomainActionError(
                "sdUUID=%s, spUUID=%s" % (sdUUID, spUUID)))
        vars.task.getExclusiveLock(STORAGE, spUUID)
        pool = self.getPool(spUUID)
        if sdUUID == pool.masterDomain.sdUUID:
            raise se.CannotDetachMasterStorageDomain(sdUUID)
        pool.forcedDetachSD(sdUUID)

    @public
    def detachStorageDomain(self, sdUUID, spUUID, msdUUID=None,
                            masterVersion=None, options=None):
        """
        Detaches a storage domain from a storage pool.
        This removes the storage domain entry in the storage pool meta-data
        and leaves the storage domain in 'unattached' status.

        :param sdUUID: The UUID of the storage domain that you want to detach.
        :type sdUUID: UUID
        :param spUUID: The UUID of the storage pool that contains the storage
                       domain being detached.
        :type spUUID: UUID
        :param msdUUID: Obsolete (was: the UUID of the master domain).
        :type msdUUID: UUID
        :param masterVersion: Obsolete (was: the version of the pool).
        :type masterVersion: int
        :param options: ?
        """
        vars.task.setDefaultException(
            se.StorageDomainActionError(
                "sdUUID=%s, spUUID=%s, msdUUID=%s, masterVersion=%s" %
                (sdUUID, spUUID, msdUUID, masterVersion)))

        vars.task.getExclusiveLock(STORAGE, spUUID)
        vars.task.getExclusiveLock(STORAGE, sdUUID)
        pool = self.getPool(spUUID)
        pool.validatePoolSD(sdUUID)
        pool.detachSD(sdUUID)

    @public
    def sendExtendMsg(self, spUUID, volDict, newSize, callbackFunc):
        """
        Send an extended message?

        :param spUUID: The UUID of the storage pool you want to
                       send the message to.
        :type spUUID: UUID
        :param volDict: ?
        :param newSize: ?
        :param callbackFun: A function to run once the operation is done. ?

        .. note::
            If the pool doesn't exist the function will fail silently and the
            callback will never be called.

        """
        newSize = misc.validateN(newSize, "newSize") / 2 ** 20
        try:
            pool = self.getPool(spUUID)
        except se.StoragePoolUnknown:
            pass
        else:
            if pool.hsmMailer:
                pool.hsmMailer.sendExtendMsg(volDict, newSize, callbackFunc)

    def _spmSchedule(self, spUUID, name, func, *args):
        self.validateSPM(spUUID)
        pool = self.getPool(spUUID)
        self.taskMng.scheduleJob("spm", pool.tasksDir, vars.task,
                                 name, func, *args)

    @public
    def refreshStoragePool(self, spUUID, msdUUID,
                           masterVersion, options=None):
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

        :raises: a :exc:`Storage_Exception.StoragePoolMaterNotFound`
                 if the storage pool and the master storage domain don't
                 exist or don't match.

        """
        vars.task.setDefaultException(
            se.StoragePoolActionError(
                "spUUID=%s, msdUUID=%s, masterVersion=%s" %
                (spUUID, msdUUID, masterVersion)))

        vars.task.getSharedLock(STORAGE, spUUID)

        try:
            # The refreshStoragePool command is an HSM command and
            # should not be issued (and executed) on the SPM. At the
            # moment we just ignore it for legacy reasons but in the
            # future vdsm could raise an exception.
            self.validateNotSPM(spUUID)
        except se.IsSpm:
            self.log.info("Ignoring the refreshStoragePool request "
                          "(the host is the SPM)")
            return

        pool = self.getPool(spUUID)

        try:
            self.validateSdUUID(msdUUID)
            pool.refresh(msdUUID, masterVersion)
        except:
            self._disconnectPool(pool, pool.id, pool.scsiKey, False)
            raise

        if pool.hsmMailer:
            pool.hsmMailer.flushMessages()

    @public
    def createStoragePool(self, poolType, spUUID, poolName, masterDom,
                          domList, masterVersion, lockPolicy=None,
                          lockRenewalIntervalSec=None, leaseTimeSec=None,
                          ioOpTimeoutSec=None, leaseRetries=None,
                          options=None):
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
        :param masterDom: The UUID of the master storage domain that
                          contains\will contain the pool's metadata.
        :type masterDom: UUID
        :param domList: A list of all the UUIDs of the storage domains managed
                        by this storage pool.
        :type domList: UUID list
        :param masterVersion: The master version of the storage pool meta data.
        :type masterVersion: uint
        :param lockPolicy: ?
        :param lockRenewalIntervalSec: ?
        :param leaseTimeSec: ?
        :param ioOpTimeoutSec: The default timeout for IO operations
                               in seconds.?
        :type ioOpTimroutSec: uint
        :param leaseRetries: ?
        :param options: ?

        :returns: The newly created storage pool object.
        :rtype: :class:`sp.StoragePool`

        :raises: an :exc:`Storage_Exception.InvalidParameterException` if the
                 master domain is not supplied in the domain list.
        """
        leaseParams = sd.packLeaseParams(
            lockRenewalIntervalSec=lockRenewalIntervalSec,
            leaseTimeSec=leaseTimeSec,
            ioOpTimeoutSec=ioOpTimeoutSec,
            leaseRetries=leaseRetries)
        vars.task.setDefaultException(
            se.StoragePoolCreationError(
                "spUUID=%s, poolName=%s, masterDom=%s, domList=%s, "
                "masterVersion=%s, clusterlock params: (%s)" %
                (spUUID, poolName, masterDom, domList, masterVersion,
                 leaseParams)))
        misc.validateUUID(spUUID, 'spUUID')
        if masterDom not in domList:
            raise se.InvalidParameterException("masterDom", str(masterDom))

        if len(poolName) > sp.MAX_POOL_DESCRIPTION_SIZE:
            raise se.StoragePoolDescriptionTooLongError()

        msd = sdCache.produce(sdUUID=masterDom)
        msdType = msd.getStorageType()
        msdVersion = msd.getVersion()
        if (msdType in sd.BLOCK_DOMAIN_TYPES and
                msdVersion in blockSD.VERS_METADATA_LV and
                len(domList) > sp.MAX_DOMAINS):
            raise se.TooManyDomainsInStoragePoolError()

        for sdUUID in domList:
            try:
                dom = sdCache.produce(sdUUID=sdUUID)
                # TODO: consider removing validate() from here, as the domains
                # are going to be accessed much later, and may loose validity
                # until then.
                dom.validate()
            except:
                raise se.StorageDomainAccessError(sdUUID)
            # If you remove this condition, remove it from
            # StoragePool.attachSD() too.
            if dom.isData() and (dom.getVersion() > msdVersion):
                raise se.MixedSDVersionError(dom.sdUUID, dom.getVersion(),
                                             msd.sdUUID, msdVersion)

        vars.task.getExclusiveLock(STORAGE, spUUID)
        for dom in sorted(domList):
            vars.task.getExclusiveLock(STORAGE, dom)

        return sp.StoragePool(
            spUUID, self.taskMng).create(poolName, masterDom, domList,
                                         masterVersion, leaseParams)

    @public
    def connectStoragePool(self, spUUID, hostID, scsiKey,
                           msdUUID, masterVersion, options=None):
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

        :raises: :exc:`storage_exception.ConnotConnectMultiplePools` when
                 storage pool is not connected to the system.
        """
        vars.task.setDefaultException(
            se.StoragePoolConnectionError(
                "spUUID=%s, msdUUID=%s, masterVersion=%s, hostID=%s, "
                "scsiKey=%s" % (spUUID, msdUUID, masterVersion,
                                hostID, scsiKey)))
        return self._connectStoragePool(spUUID, hostID, scsiKey, msdUUID,
                                        masterVersion, options)

    def _connectStoragePool(self, spUUID, hostID, scsiKey, msdUUID,
                            masterVersion, options=None):
        misc.validateUUID(spUUID, 'spUUID')

        # TBD: To support multiple pool connection on single host,
        # we'll need to remove this validation
        if len(self.pools) and spUUID not in self.pools:
            raise se.CannotConnectMultiplePools(str(self.pools.keys()))

        try:
            self.getPool(spUUID)
        except se.StoragePoolUnknown:
            pass  # pool not connected yet
        else:
            with rmanager.acquireResource(STORAGE, spUUID, rm.LockType.shared):
                pool = self.getPool(spUUID)
                if not msdUUID or not masterVersion:
                    hostID, scsiKey, msdUUID, masterVersion = \
                        pool.getPoolParams()
                misc.validateN(hostID, 'hostID')
                # getMasterDomain is called because produce is required here
                # since the master domain can be changed by the SPM if it is
                # the refreshPool flow.
                pool.getMasterDomain(msdUUID=msdUUID,
                                     masterVersion=masterVersion)
                return

        with rmanager.acquireResource(STORAGE, spUUID, rm.LockType.exclusive):
            try:
                pool = self.getPool(spUUID)
            except se.StoragePoolUnknown:
                pass  # pool not connected yet
            else:
                if not msdUUID or not masterVersion:
                    hostID, scsiKey, msdUUID, masterVersion = \
                        pool.getPoolParams()
                misc.validateN(hostID, 'hostID')
                # Idem. See above.
                pool.getMasterDomain(msdUUID=msdUUID,
                                     masterVersion=masterVersion)
                return

            pool = sp.StoragePool(spUUID, self.taskMng)
            if not hostID or not scsiKey or not msdUUID or not masterVersion:
                hostID, scsiKey, msdUUID, masterVersion = pool.getPoolParams()
            res = pool.connect(hostID, scsiKey, msdUUID, masterVersion)
            if res:
                self.pools[spUUID] = pool
            return res

    @public
    def disconnectStoragePool(self, spUUID, hostID, scsiKey, remove=False,
                              options=None):
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
            if storage pool is not connected or doesn't exist the operation
            will log and exit silently.
        """
        vars.task.setDefaultException(
            se.StoragePoolDisconnectionError(
                "spUUID=%s, hostID=%s, scsiKey=%s" %
                (spUUID, hostID, scsiKey)))
        misc.validateN(hostID, 'hostID')
        # already disconnected/or pool is just unknown - return OK
        try:
            pool = self.getPool(spUUID)
        except se.StoragePoolUnknown:
            self.log.warning("disconnect sp: %s failed. Known pools %s",
                             spUUID, self.pools)
            return

        self.validateNotSPM(spUUID)

        vars.task.getExclusiveLock(STORAGE, spUUID)
        pool = self.getPool(spUUID)

        return self._disconnectPool(pool, hostID, scsiKey, remove)

    def _disconnectPool(self, pool, hostID, scsiKey, remove):
        self.validateNotSPM(pool.spUUID)
        res = pool.disconnect()
        del self.pools[pool.spUUID]
        return res

    @public
    def destroyStoragePool(self, spUUID, hostID, scsiKey, options=None):
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
        vars.task.setDefaultException(
            se.StoragePoolDestroyingError(
                "spUUID=%s, hostID=%s, scsiKey=%s" %
                (spUUID, hostID, scsiKey)))
        self.log.info("spUUID=%s", spUUID)

        pool = self.getPool(spUUID)
        if not pool.id == hostID:
            raise se.HostIdMismatch(spUUID)

        vars.task.getExclusiveLock(STORAGE, pool.spUUID)
        # Find out domain list from the pool metadata
        domList = sorted(pool.getDomains().keys())
        for sdUUID in domList:
            vars.task.getExclusiveLock(STORAGE, sdUUID)

        pool.detachAllDomains()
        return self._disconnectPool(pool, hostID, scsiKey, remove=True)

    @public
    def attachStorageDomain(self, sdUUID, spUUID, options=None):
        """
        Attach a storage domain to a storage pool.
        This marks the storage domain as status 'attached' and link it to the
        storage pool

        .. note::
            The target domain must be accessible in this point
            (storage connected)

        :param sdUUID: The UUID of the storage domain that you want to attach.
        :type sdUUID: UUID
        :param spUUID: The UUID of the storage pool that contains the storage
                       domain being attached.
        :type spUUID: UUID
        :param options: ?
        """
        vars.task.setDefaultException(
            se.StorageDomainActionError(
                "sdUUID=%s, spUUID=%s" % (sdUUID, spUUID)))

        vars.task.getExclusiveLock(STORAGE, spUUID)
        vars.task.getExclusiveLock(STORAGE, sdUUID)
        pool = self.getPool(spUUID)
        pool.attachSD(sdUUID)

    @public
    def deactivateStorageDomain(self, sdUUID, spUUID, msdUUID,
                                masterVersion, options=None):
        """
        1. Deactivates a storage domain.
        2. Validates that the storage domain is owned by the storage pool.
        3. Disables access to that storage domain.
        4. Changes storage domain status to 'Inactive' in the storage pool
           meta-data.

        .. note::
            Disconnected storage domains are not monitored by the host.

        :param sdUUID: The UUID of the storage domain that you want to
                       deactivate.
        :type sdUUID: UUID
        :param spUUID: The UUID of the storage pool that contains the storage
                       domain being deactivated.
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
                (sdUUID, spUUID, msdUUID, masterVersion)
            )
        )

        vars.task.getExclusiveLock(STORAGE, spUUID)
        vars.task.getExclusiveLock(STORAGE, sdUUID)
        pool = self.getPool(spUUID)
        pool.validatePoolSD(sdUUID)
        pool.deactivateSD(sdUUID, msdUUID, masterVersion)

    @public
    def activateStorageDomain(self, sdUUID, spUUID, options=None):
        """
        Activates a storage domain that is already a member in a storage pool.

        :param sdUUID: The UUID of the storage domain that you want to
                       activate.
        :type sdUUID: UUID
        :param spUUID: The UUID of the storage pool that contains the storage
                       domain being activated.
        :type spUUID: UUID
        :param options: ?
        """
        vars.task.setDefaultException(
            se.StorageDomainActionError(
                "sdUUID=%s, spUUID=%s" % (sdUUID, spUUID)))

        vars.task.getExclusiveLock(STORAGE, spUUID)
        vars.task.getExclusiveLock(STORAGE, sdUUID)
        pool = self.getPool(spUUID)
        pool.activateSD(sdUUID)

    @public
    def setStoragePoolDescription(self, spUUID, description, options=None):
        """
        Sets the storage pool's description.

        :param spUUID: The UUID of the storage pool that you want to set it's
                       description.
        :type spUUID: UUID
        :param description: A human readable description of the storage pool.
        :type description: str
        :param options: ?
        """
        vars.task.setDefaultException(
            se.StoragePoolActionError(
                "spUUID=%s, descr=%s" % (spUUID, description)))
        vars.task.getExclusiveLock(STORAGE, spUUID)
        pool = self.getPool(spUUID)
        pool.setDescription(description)

    @public
    def setVolumeDescription(self, sdUUID, spUUID, imgUUID, volUUID,
                             description, options=None):
        """
        Sets a Volume's Description

        :param spUUID: The UUID of the storage pool that contains the volume
                       being modified.
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
        vars.task.getSharedLock(STORAGE, sdUUID)
        pool = self.getPool(spUUID)
        pool.validatePoolSD(sdUUID)
        pool.setVolumeDescription(sdUUID, imgUUID, volUUID, description)

    @public
    def setVolumeLegality(self, sdUUID, spUUID, imgUUID, volUUID, legality,
                          options=None):
        """
        Sets a Volume's Legality

        :param spUUID: The UUID of the storage pool that contains the volume
                       being modified.
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
        vars.task.getSharedLock(STORAGE, sdUUID)

        pool = self.getPool(spUUID)
        pool.validatePoolSD(sdUUID)
        pool.setVolumeLegality(sdUUID, imgUUID, volUUID, legality)

    @public
    def updateVM(self, spUUID, vmList, sdUUID=None, options=None):
        """
        Updates a VM list in a storage pool or in a Backup domain.
        Creates the VMs if a domain with the specified UUID does not exist.

        .. note::
            Should be called by VDC for every change of VM (add or remove
            snapshots, updates, ...)

        :param spUUID: The UUID of the storage pool that contains the VMs
                       being updated or created.
        :type spUUID: UUID
        :param vmList: The list of VMs being updated.?
        :type vmList: list
        :param sdUUID: The UUID of the backup domain you want to update or
                       :keyword:`None` if you want something something. ?
        :type sdUUID: UUID
        :param options: ?
        """
        vars.task.getSharedLock(STORAGE, spUUID)
        pool = self.getPool(spUUID)
        if sdUUID and sdUUID != sd.BLANK_UUID:
            pool.validatePoolSD(sdUUID)
            self.validateSdUUID(sdUUID)
        else:
            sdUUID = pool.masterDomain.sdUUID

        vmUUIDs = [vmDesc['vm'] for vmDesc in vmList]
        vmUUIDs.sort()
        for vmUUID in vmUUIDs:
            vars.task.getExclusiveLock(STORAGE, "%s_%s" % (vmUUID, sdUUID))
        pool.updateVM(vmList=vmList, sdUUID=sdUUID)

    @public
    def removeVM(self, spUUID, vmUUID, sdUUID=None, options=None):
        """
        Removes a VM list from a storage pool or from a Backup domain.

        :param spUUID: The UUID of the storage pool that contains the VMs
                       being removed.
        :type spUUID: UUID
        :param vmUUID: The UUID of VM being removed.
        :type vmUUID: UUID
        :param sdUUID: The UUID of the backup domain you want to update or
                       :keyword:`None` if you want something something. ?
        :type sdUUID: UUID
        :param options: ?
        """
        vars.task.getSharedLock(STORAGE, spUUID)
        pool = self.getPool(spUUID)
        if sdUUID and sdUUID != sd.BLANK_UUID:
            pool.validatePoolSD(sdUUID)
            self.validateSdUUID(sdUUID)
        else:
            sdUUID = pool.masterDomain.sdUUID
        vars.task.getExclusiveLock(STORAGE, "%s_%s" % (vmUUID, sdUUID))
        pool.removeVM(vmUUID=vmUUID, sdUUID=sdUUID)

    @public
    def getVmsList(self, spUUID, sdUUID=None, options=None):
        """
        Gets a list of VMs from the pool.
        If 'sdUUID' is given and it's a backup domain the function will get
        the list of VMs from it

        :param spUUID: The UUID of the storage pool that you want to query.
        :type spUUID: UUID
        :param sdUUID: The UUID of the backup domain that the you want to
                       query or :keyword:`None`.
        :type sdUUID: UUID
        :param options: ?
        """
        pool = self.getPool(spUUID)
        if sdUUID and sdUUID != sd.BLANK_UUID:
            pool.validatePoolSD(sdUUID)
            self.validateSdUUID(sdUUID)
        else:
            sdUUID = pool.masterDomain.sdUUID
        vars.task.getSharedLock(STORAGE, sdUUID)
        vms = pool.getVmsList(sdUUID)
        return dict(vmlist=vms)

    @public
    def getVmsInfo(self, spUUID, sdUUID, vmList=None, options=None):
        """
        Gets a list of VMs with their info from the pool.

        * If 'sdUUID' is given and it's a backup domain then get the list of
          VMs from it.
        * If 'vmList' is given get info for these VMs only.

        :param spUUID: The UUID of the storage pool that you want to query.
        :type spUUID: UUID
        :param sdUUID: The UUID of the backup domain that the you want to
                       query or :keyword:`None`.
        :type sdUUID: UUID
        :param vmList: A UUID list of the VMs you want info on or
                       :keyword:`None` for all VMs in pool or backup domain.
        :param options: ?
        """
        pool = self.getPool(spUUID)
        if sdUUID and sdUUID != sd.BLANK_UUID:
            pool.validatePoolSD(sdUUID)
            # Only backup domains are allowed in this path
            self.validateBackupDom(sdUUID)
        else:
            sdUUID = pool.masterDomain.sdUUID
        vars.task.getSharedLock(STORAGE, sdUUID)
        vms = pool.getVmsInfo(sdUUID, vmList)
        return dict(vmlist=vms)

    @public
    def uploadVolume(self, sdUUID, spUUID, imgUUID, volUUID, srcPath, size,
                     method="rsync", options=None):
        """
        Uploads a volume to the server. (NFS only?)

        :param spUUID: The UUID of the storage pool that will contain the
                       new volume.
        :type spUUID: UUID
        :param sdUUID: The UUID of the backup domain that will contain the
                       new volume.
        :type sdUUID: UUID
        :param imgUUID: The UUID of image you want associated with that volume.
        :type imgUUID: UUID
        :param volUUID: The UUID that the new volume will have after upload.
        :type volUUID: UUID
        :param size: The size of the volume being uploaded in ...?
        :type size: ?
        :param method: The desired method of upload. Currently only *'wget'*
                       and *'rsync'* are supported.
        :type method: str
        :param options: ?
        """
        vars.task.getSharedLock(STORAGE, spUUID)
        pool = self.getPool(spUUID)
        pool.uploadVolume(sdUUID, imgUUID, volUUID, srcPath, size,
                          method="rsync")

    @public
    def createVolume(self, sdUUID, spUUID, imgUUID, size, volFormat,
                     preallocate, diskType, volUUID, desc,
                     srcImgUUID=volume.BLANK_UUID,
                     srcVolUUID=volume.BLANK_UUID):
        """
        Create a new volume
            Function Type: SPM
            Parameters:
            Return Value:
        """
        argsStr = ("sdUUID=%s, spUUID=%s, imgUUID=%s, size=%s, volFormat=%s, "
                   "preallocate=%s, diskType=%s, volUUID=%s, desc=%s, "
                   "srcImgUUID=%s, srcVolUUID=%s" %
                   (sdUUID, spUUID, imgUUID, size, volFormat, preallocate,
                    diskType, volUUID, desc,
                    srcImgUUID, srcVolUUID))
        vars.task.setDefaultException(se.VolumeCreationError(argsStr))
        # Validates that the pool is connected. WHY?
        pool = self.getPool(spUUID)
        sdDom = self.validateSdUUID(sdUUID)
        misc.validateUUID(imgUUID, 'imgUUID')
        misc.validateUUID(volUUID, 'volUUID')
        # TODO: For backwards compatibility, we need to support accepting
        # number of sectors as int type Updated interface is accepting string
        # type in bytes (ugly, get rid of this when possible)
        if not isinstance(size, types.IntType):
            size = misc.validateN(size, "size")
            size = (size + SECTOR_SIZE - 1) / SECTOR_SIZE

        if srcImgUUID:
            misc.validateUUID(srcImgUUID, 'srcImgUUID')
        if srcVolUUID:
            misc.validateUUID(srcVolUUID, 'srcVolUUID')
        # Validate volume type and format
        sdDom.validateCreateVolumeParams(volFormat, preallocate, srcVolUUID)

        vars.task.getSharedLock(STORAGE, sdUUID)
        self._spmSchedule(spUUID, "createVolume", pool.createVolume, sdUUID,
                          imgUUID, size, volFormat, preallocate, diskType,
                          volUUID, desc, srcImgUUID, srcVolUUID)

    @public
    def deleteVolume(self, sdUUID, spUUID, imgUUID, volumes, postZero=False,
                     force=False):
        """
        Delete a volume
        """
        argsStr = "sdUUID=%s, spUUID=%s, imgUUID=%s, volumes=%s, " \
                  "postZero=%s, force=%s" % (sdUUID, spUUID, imgUUID, volumes,
                                             postZero, force)
        vars.task.setDefaultException(se.CannotDeleteVolume(argsStr))
        # Validates that the pool is connected. WHY?
        pool = self.getPool(spUUID)
        sdDom = self.validateSdUUID(sdUUID)
        misc.validateUUID(imgUUID, 'imgUUID')

        vars.task.getSharedLock(STORAGE, sdUUID)
        # Do not validate if forced.
        if not misc.parseBool(force):
            for volUUID in volumes:
                sdDom.produceVolume(imgUUID, volUUID).validateDelete()

        self._spmSchedule(spUUID, "deleteVolume", pool.deleteVolume, sdUUID,
                          imgUUID, volumes, misc.parseBool(postZero),
                          misc.parseBool(force))

    @public
    def deleteImage(self, sdUUID, spUUID, imgUUID, postZero=False,
                    force=False):
        """
        Delete Image folder with all volumes

        force parameter is deprecated and not evaluated.
        """
        # vars.task.setDefaultException(se.ChangeMeError("%s" % args))
        self.getPool(spUUID)  # Validates that the pool is connected. WHY?
        dom = self.validateSdUUID(sdUUID)

        vars.task.getExclusiveLock(STORAGE, imgUUID)
        vars.task.getSharedLock(STORAGE, sdUUID)
        allVols = dom.getAllVolumes()
        volsByImg = sd.getVolsOfImage(allVols, imgUUID)
        if not volsByImg:
            self.log.error("Empty or not found image %s in SD %s. %s",
                           imgUUID, sdUUID, allVols)
            raise se.ImageDoesNotExistInSD(imgUUID, sdUUID)

        # on data domains, images should not be deleted if they are templates
        # being used by other images.
        isTemplateWithChildren = False
        for v in volsByImg.itervalues():
            if len(v.imgs) > 1 and v.imgs[0] == imgUUID:
                isTemplateWithChildren = True
                break

        if not isTemplateWithChildren:
            needFake = False
        elif dom.isBackup():
            needFake = True
        else:
            raise se.CannotDeleteSharedVolume("Cannot delete shared image %s. "
                                              "volImgs: %s" % (imgUUID,
                                                               volsByImg))

        # zeroImage will delete zeroed volumes at the end.
        if misc.parseBool(postZero):
            # postZero implies block domain. Backup domains are always NFS
            # hence no need to create fake template if postZero is true.
            self._spmSchedule(spUUID, "zeroImage_%s" % imgUUID, dom.zeroImage,
                              sdUUID, imgUUID, volsByImg)
        else:
            dom.deleteImage(sdUUID, imgUUID, volsByImg)
            # This is a hack to keep the interface consistent
            # We currently have race conditions in delete image, to quickly fix
            # this we delete images in the "synchronous" state. This only works
            # because Engine does not send two requests at a time. This hack is
            # intended to quickly fix the integration issue with Engine. In 2.3
            # we should use the new resource system to synchronize the process
            # an eliminate all race conditions
            if needFake:
                img = image.Image(os.path.join(self.storage_repository,
                                               spUUID))
                tName = volsByImg.iterkeys()[0]
                tParams = dom.produceVolume(imgUUID, tName).getVolumeParams()
                img.createFakeTemplate(sdUUID=sdUUID, volParams=tParams)
            self._spmSchedule(spUUID, "deleteImage_%s" % imgUUID, lambda: True)

    def validateImageMove(self, srcDom, dstDom, imgUUID):
        """
        Determines if the image move is legal.

        Moving an image based on a template to a data domain is only allowed if
        the template exists on the target domain.
        Moving a template from a data domain is only allowed if there are no
        images based on it in the source data domain.
        """
        srcAllVols = srcDom.getAllVolumes()
        dstAllVols = dstDom.getAllVolumes()

        # Filter volumes related to this image
        srcVolsImgs = sd.getVolsOfImage(srcAllVols, imgUUID)
        # Find the template
        for volName, imgsPar in srcVolsImgs.iteritems():
            if len(imgsPar.imgs) > 1:
                # This is the template. Should be only one.
                tName, tImgs = volName, imgsPar.imgs
                # Template self image is the 1st entry
                if imgUUID != tImgs[0] and tName not in dstAllVols.keys():
                    self.log.error(
                        "img %s can't be moved to dom %s because template "
                        "%s is absent on it", imgUUID, dstDom.sdUUID, tName)
                    e = se.ImageDoesNotExistInSD(imgUUID, dstDom.sdUUID)
                    e.absentTemplateUUID = tName
                    e.absentTemplateImageUUID = tImgs[0]
                    raise e
                elif imgUUID == tImgs[0] and not srcDom.isBackup():
                    raise se.MoveTemplateImageError(imgUUID)
                break

        return True

    @public
    def moveImage(self, spUUID, srcDomUUID, dstDomUUID, imgUUID, vmUUID,
                  op, postZero=False, force=False):
        """
        Move/Copy image between storage domains within same storage pool
        """
        argsStr = ("spUUID=%s, srcDomUUID=%s, dstDomUUID=%s, imgUUID=%s, "
                   "vmUUID=%s, op=%s, force=%s, postZero=%s force=%s" %
                   (spUUID, srcDomUUID, dstDomUUID, imgUUID, vmUUID, op,
                    force, postZero, force))
        vars.task.setDefaultException(se.MoveImageError("%s" % argsStr))
        if srcDomUUID == dstDomUUID:
            raise se.InvalidParameterException(
                "srcDom", "must be different from dstDom: %s" % argsStr)

        srcDom = self.validateSdUUID(srcDomUUID)
        dstDom = self.validateSdUUID(dstDomUUID)
        # Validates that the pool is connected. WHY?
        pool = self.getPool(spUUID)
        try:
            self.validateImageMove(srcDom, dstDom, imgUUID)
        except se.ImageDoesNotExistInSD, e:
            if not dstDom.isBackup():
                raise
            else:
                # Create an ad-hoc fake template only on a backup SD
                tName = e.absentTemplateUUID
                tImgUUID = e.absentTemplateImageUUID
                tParams = srcDom.produceVolume(tImgUUID,
                                               tName).getVolumeParams()
                image.Image(os.path.join(self.storage_repository, spUUID)
                            ).createFakeTemplate(dstDom.sdUUID, tParams)

        domains = [srcDomUUID, dstDomUUID]
        domains.sort()

        for dom in domains:
            vars.task.getSharedLock(STORAGE, dom)

        self._spmSchedule(
            spUUID, "moveImage_%s" % imgUUID, pool.moveImage, srcDomUUID,
            dstDomUUID, imgUUID, vmUUID, op, misc.parseBool(postZero),
            misc.parseBool(force))

    @public
    def cloneImageStructure(self, spUUID, sdUUID, imgUUID, dstSdUUID):
        """
        Clone an image structure (volume chain) to a destination domain within
        the same pool.
        """
        self.validateSdUUID(sdUUID)
        self.validateSdUUID(dstSdUUID)

        for dom in sorted((sdUUID, dstSdUUID)):
            vars.task.getSharedLock(STORAGE, dom)

        pool = self.getPool(spUUID)
        self._spmSchedule(spUUID, "cloneImageStructure",
                          pool.cloneImageStructure, sdUUID, imgUUID, dstSdUUID)

    @public
    def syncImageData(self, spUUID, sdUUID, imgUUID, dstSdUUID, syncType):
        """
        Copy the internal data between image structures (volume chain) within
        the same pool.
        """
        self.validateSdUUID(sdUUID)
        self.validateSdUUID(dstSdUUID)

        for dom in sorted((sdUUID, dstSdUUID)):
            vars.task.getSharedLock(STORAGE, dom)

        pool = self.getPool(spUUID)
        self._spmSchedule(spUUID, "syncImageData", pool.syncImageData,
                          sdUUID, imgUUID, dstSdUUID, syncType)

    @public
    def uploadImage(self, methodArgs, spUUID, sdUUID, imgUUID, volUUID=None):
        """
        Upload an image to a remote endpoint using the specified method and
        methodArgs.
        """
        self.validateSdUUID(sdUUID)
        pool = self.getPool(spUUID)
        # NOTE: this could become an hsm task
        self._spmSchedule(spUUID, "uploadImage", pool.uploadImage,
                          methodArgs, sdUUID, imgUUID, volUUID)

    @public
    def downloadImage(self, methodArgs, spUUID, sdUUID, imgUUID, volUUID=None):
        """
        Download an image from a remote endpoint using the specified method
        and methodArgs.
        """
        self.validateSdUUID(sdUUID)
        pool = self.getPool(spUUID)
        # NOTE: this could become an hsm task, in such case the LV extension
        # required to prepare the destination should go through the mailbox.
        self._spmSchedule(spUUID, "downloadImage", pool.downloadImage,
                          methodArgs, sdUUID, imgUUID, volUUID)

    @deprecated
    @public
    def moveMultipleImages(self, spUUID, srcDomUUID, dstDomUUID, imgDict,
                           vmUUID, force=False):
        """
        Move multiple images between storage domains within same storage pool
        """
        argsStr = ("spUUID=%s, srcDomUUID=%s, dstDomUUID=%s, imgDict=%s, "
                   "vmUUID=%s force=%s" %
                   (spUUID, srcDomUUID, dstDomUUID, imgDict, vmUUID, force))
        vars.task.setDefaultException(
            se.MultipleMoveImageError("%s" % argsStr))
        if srcDomUUID == dstDomUUID:
            raise se.InvalidParameterException("dstDomUUID", dstDomUUID)

        # Validates that the pool is connected. WHY?
        pool = self.getPool(spUUID)

        srcDom = self.validateSdUUID(srcDomUUID)
        dstDom = self.validateSdUUID(dstDomUUID)
        images = {}
        for (imgUUID, pZero) in imgDict.iteritems():
            images[imgUUID.strip()] = misc.parseBool(pZero)
            try:
                self.validateImageMove(srcDom, dstDom, imgUUID)
            except se.ImageDoesNotExistInSD, e:
                if not dstDom.isBackup():
                    raise
                else:
                    # Create an ad-hoc fake template only on a backup SD
                    tName = e.absentTemplateUUID
                    tImgUUID = e.absentTemplateImageUUID
                    tParams = srcDom.produceVolume(tImgUUID,
                                                   tName).getVolumeParams()
                    image.Image(os.path.join(self.storage_repository, spUUID)
                                ).createFakeTemplate(dstDom.sdUUID, tParams)

        domains = sorted([srcDomUUID, dstDomUUID])
        for dom in domains:
            vars.task.getSharedLock(STORAGE, dom)

        self._spmSchedule(
            spUUID, "moveMultipleImages", pool.moveMultipleImages,
            srcDomUUID, dstDomUUID, images, vmUUID, misc.parseBool(force))

    @public
    def copyImage(
            self, sdUUID, spUUID, vmUUID, srcImgUUID, srcVolUUID, dstImgUUID,
            dstVolUUID, description='', dstSdUUID=sd.BLANK_UUID,
            volType=volume.SHARED_VOL, volFormat=volume.UNKNOWN_VOL,
            preallocate=volume.UNKNOWN_VOL, postZero=False, force=False):
        """
        Create new template/volume from VM.
        Do it by collapse and copy the whole chain (baseVolUUID->srcVolUUID)
        """
        argsStr = ("sdUUID=%s, spUUID=%s, vmUUID=%s, srcImgUUID=%s, "
                   "srcVolUUID=%s, dstImgUUID=%s, dstVolUUID=%s, "
                   "description=%s, dstSdUUID=%s, volType=%s, volFormat=%s, "
                   "preallocate=%s force=%s, postZero=%s" %
                   (sdUUID, spUUID, vmUUID, srcImgUUID, srcVolUUID,
                    dstImgUUID, dstVolUUID, description, dstSdUUID, volType,
                    volFormat, preallocate, force, postZero))
        vars.task.setDefaultException(se.TemplateCreationError("%s" % argsStr))
        # Validate imgUUID in case of copy inside source domain itself
        if dstSdUUID in (sdUUID, sd.BLANK_UUID):
            if srcImgUUID == dstImgUUID:
                raise se.InvalidParameterException("dstImgUUID", dstImgUUID)
        pool = self.getPool(spUUID)
        self.validateSdUUID(sdUUID)

        # Avoid VM copy if one of its volume (including template if exists)
        # ILLEGAL/FAKE
        pool.validateVolumeChain(sdUUID, srcImgUUID)
        # Validate volume type and format
        if dstSdUUID != sd.BLANK_UUID:
            dom = dstSdUUID
        else:
            dom = sdUUID
        sdCache.produce(dom).validateCreateVolumeParams(
            volFormat, preallocate, volume.BLANK_UUID)

        # If dstSdUUID defined, means we copy image to it
        domains = [sdUUID]
        if dstSdUUID not in [sdUUID, sd.BLANK_UUID]:
            self.validateSdUUID(dstSdUUID)
            domains.append(dstSdUUID)
            domains.sort()

        for dom in domains:
            vars.task.getSharedLock(STORAGE, dom)

        self._spmSchedule(
            spUUID, "copyImage_%s" % dstImgUUID, pool.copyImage, sdUUID,
            vmUUID, srcImgUUID, srcVolUUID, dstImgUUID, dstVolUUID,
            description, dstSdUUID, volType, volFormat, preallocate,
            misc.parseBool(postZero), misc.parseBool(force))

    @public
    def mergeSnapshots(self, sdUUID, spUUID, vmUUID, imgUUID, ancestor,
                       successor, postZero=False):
        """
        Merge source volume to the destination volume.
        """
        argsStr = ("sdUUID=%s, spUUID=%s, vmUUID=%s, imgUUID=%s, "
                   "ancestor=%s, successor=%s, postZero=%s" %
                   (sdUUID, spUUID, vmUUID, imgUUID, ancestor, successor,
                    postZero))
        vars.task.setDefaultException(se.MergeSnapshotsError("%s" % argsStr))
        pool = self.getPool(spUUID)
        self.validateSdUUID(sdUUID)
        vars.task.getSharedLock(STORAGE, sdUUID)
        self._spmSchedule(
            spUUID, "mergeSnapshots", pool.mergeSnapshots, sdUUID, vmUUID,
            imgUUID, ancestor, successor, misc.parseBool(postZero))

    @public
    def reconstructMaster(self, spUUID, poolName, masterDom, domDict,
                          masterVersion, lockPolicy=None,
                          lockRenewalIntervalSec=None, leaseTimeSec=None,
                          ioOpTimeoutSec=None, leaseRetries=None, hostId=None,
                          options=None):
        """
        Reconstruct Master Domains - rescue action: can be issued even when
        pool is not connected.

        :param spUUID: The UUID of the storage pool you want to reconstruct.
        :type spUUID: UUID
        :param masterDom: The new master domain UUID.
        :type masterDom: UUID
        :param domDict: Dict. of domain and statuses
                        ``{'sdUUID1':status1, 'sdUUID2':status2}``
        :type domDict: dict
        :param masterVersion: The new version of master domain.
        :type masterVersion: int
        :param lockPolicy: ?
        :param lockRenewalIntervalSec: ?
        :param leaseTimeSec: ?
        :param ioOpTimeoutSec: The timeout of IO operations in seconds. ?
        :type ioOpTimeoutSec: int
        :param leaseRetries: ?
        :param hostId: The host id to be used during the reconstruct process.
        :param options: ?

        :returns: Nothing ? pool.reconstructMaster return nothing
        :rtype: ?
        """
        leaseParams = sd.packLeaseParams(
            lockRenewalIntervalSec=lockRenewalIntervalSec,
            leaseTimeSec=leaseTimeSec,
            ioOpTimeoutSec=ioOpTimeoutSec,
            leaseRetries=leaseRetries
        )

        vars.task.setDefaultException(
            se.ReconstructMasterError(
                "spUUID=%s, masterDom=%s, masterVersion=%s, clusterlock "
                "params: (%s)" % (spUUID, masterDom, masterVersion,
                                  leaseParams)))

        self.log.info("spUUID=%s master=%s", spUUID, masterDom)

        try:
            pool = self.getPool(spUUID)
        except se.StoragePoolUnknown:
            pool = sp.StoragePool(spUUID, self.taskMng)
        else:
            raise se.StoragePoolConnected(spUUID)

        self.validateSdUUID(masterDom)

        if hostId is not None:
            misc.validateN(hostId, 'hostId')

        vars.task.getExclusiveLock(STORAGE, spUUID)

        for d, status in domDict.iteritems():
            misc.validateUUID(d)
            try:
                sd.validateSDStatus(status)
            except:
                domDict[d] = sd.validateSDDeprecatedStatus(status)

        return pool.reconstructMaster(hostId, poolName, masterDom, domDict,
                                      masterVersion, leaseParams)

    def _logResp_getDeviceList(self, response):
        logableDevs = deepcopy(response)
        for dev in logableDevs['devList']:
            for con in dev['pathlist']:
                con['password'] = "******"
        return logableDevs

    @public(logger=logged(resPrinter=partial(_logResp_getDeviceList, None)))
    def getDeviceList(self, storageType=None, options={}):
        """
        List all Block Devices.

        :param storageType: Filter by storage type.
        :type storageType: Some enum?
        :param options: ?

        :returns: Dict containing a list of all the devices of the storage
                  type specified.
        :rtype: dict
        """
        vars.task.setDefaultException(se.BlockDeviceActionError())
        devices = self._getDeviceList(storageType)
        return dict(devList=devices)

    def _getDeviceList(self, storageType=None, guids=None):
        sdCache.refreshStorage()
        typeFilter = lambda dev: True
        if storageType:
            if sd.storageType(storageType) == sd.type2name(sd.ISCSI_DOMAIN):
                typeFilter = \
                    lambda dev: multipath.devIsiSCSI(dev.get("devtype"))
            elif sd.storageType(storageType) == sd.type2name(sd.FCP_DOMAIN):
                typeFilter = \
                    lambda dev: multipath.devIsFCP(dev.get("devtype"))

        devices = []
        pvs = {}
        if guids is not None:
            for guid in guids:
                try:
                    pv = lvm.getPV(guid)
                except se.StorageException:
                    self.log.warning("getPV failed for guid: %s", guid,
                                     exc_info=True)
                else:
                    if pv is None:
                        continue
                    pvs[os.path.basename(pv.name)] = pv
        else:
            for pv in lvm.getAllPVs():
                pvs[os.path.basename(pv.name)] = pv

        # FIXME: pathListIter() should not return empty records
        for dev in multipath.pathListIter(guids):
            if not typeFilter(dev):
                continue

            pv = pvs.get(dev.get('guid', ""))
            if pv is not None:
                pvuuid = pv.uuid
                vguuid = pv.vg_uuid
            else:
                pvuuid = ""
                vguuid = ""

            devInfo = {'GUID': dev.get("guid", ""), 'pvUUID': pvuuid,
                       'vgUUID': vguuid, 'vendorID': dev.get("vendor", ""),
                       'productID': dev.get("product", ""),
                       'fwrev': dev.get("fwrev", ""),
                       "serial": dev.get("serial", ""),
                       'capacity': dev.get("capacity", "0"),
                       'devtype': dev.get("devtype", ""),
                       'pathstatus': dev.get("paths", []),
                       'pathlist': dev.get("connections", []),
                       'logicalblocksize': dev.get("logicalblocksize", ""),
                       'physicalblocksize': dev.get("physicalblocksize", "")}
            devices.append(devInfo)

        # Look for devices that will probably fail if pvcreated.
        devNamesToPVTest = tuple(dev["GUID"] for dev in devices)
        unusedDevs, usedDevs = lvm.testPVCreate(
            devNamesToPVTest, metadataSize=blockSD.VG_METADATASIZE)
        # Assuming that unusables v unusables = None
        free = tuple(os.path.basename(d) for d in unusedDevs)
        used = tuple(os.path.basename(d) for d in usedDevs)
        for dev in devices:
            guid = dev['GUID']
            if guid in free:
                dev['status'] = "free"
            elif guid in used:
                dev['status'] = "used"
            else:
                raise KeyError("pvcreate response foresight is "
                               "can not be determined for %s", dev)

        return devices

    @public
    def getDeviceInfo(self, guid, options={}):
        """
        Get info of block device.

        :param guid: The GUID of the device you want to get info on.
        :type guid: UUID
        :param options: ?

        :returns: Dict of all the info on the device.
        :rtype: dict

        :raises: :exc:`storage_exception.DeviceNotFound` if a device with that
                 GUID doesn't exist.
        """
        vars.task.setDefaultException(
            se.BlockDeviceActionError("GUID: %s" % guid))
        # getSharedLock(connectionsResource...)
        try:
            devInfo = \
                self._getDeviceList(
                    guids=[guid],
                    includePartitioned=options.get('includePartitioned',
                                                   False))[0]
            for p in devInfo["pathstatus"]:
                if p.get("state", "error") == "active":
                    return {"info": devInfo}

            raise se.DeviceNotFound(str(guid))
        except KeyError:
            raise se.DeviceNotFound(str(guid))

    @public
    def scanDevicesVisibility(self, guids):
        visible = lambda guid: (guid, os.path.exists(
                                os.path.join("/dev/mapper", guid)))
        visibleDevs = map(visible, guids)
        if not all(visibleDevs):
            multipath.rescan()
            visibleDevs = map(visible, guids)
        return dict(visibleDevs)

    @public
    def getDevicesVisibility(self, guids, options=None):
        """
        Check which of the luns with specified guids are visible

        :param guids: List of device GUIDs to check.
        :type guids: list
        :param options: ?

        :returns: dictionary of specified guids and respective visibility
                  boolean
        :rtype: dict
        """
        visibility = self.scanDevicesVisibility(guids)
        for guid in guids:
            if visibility[guid]:
                visibility[guid] = (os.stat('/dev/mapper/' + guid).st_mode &
                                    stat.S_IRUSR != 0)
        return {'visible': visibility}

    @public
    def createVG(self, vgname, devlist, force=False, options=None):
        """
        Creates a volume group with the name 'vgname' out of the devices in
        'devlist'

        :param vgname: The human readable name of the vg.
        :type vgname: str
        :param devlist: A list of devices to be included in the VG.
                        The devices must be unattached.
        :type devlist: list
        :param options: ?

        :returns: the UUID of the new VG.
        :rtype: UUID
        """
        MINIMALVGSIZE = 10 * 1024 * constants.MEGAB

        vars.task.setDefaultException(
            se.VolumeGroupCreateError(str(vgname), str(devlist)))
        misc.validateUUID(vgname, 'vgname')
        # getSharedLock(connectionsResource...)
        knowndevs = set(multipath.getMPDevNamesIter())
        size = 0
        devices = []

        for dev in devlist:
            if dev in knowndevs:
                devices.append(dev)
                size += multipath.getDeviceSize(devicemapper.getDmId(dev))
            else:
                raise se.InvalidPhysDev(dev)

        # Minimal size check
        if size < MINIMALVGSIZE:
            raise se.VolumeGroupSizeError(
                "VG size must be more than %s MiB" %
                str(MINIMALVGSIZE / constants.MEGAB))

        lvm.createVG(vgname, devices, blockSD.STORAGE_UNREADY_DOMAIN_TAG,
                     metadataSize=blockSD.VG_METADATASIZE,
                     force=(force is True) or (isinstance(force, str) and
                                               (force.capitalize() == "True")))

        return dict(uuid=lvm.getVG(vgname).uuid)

    @deprecated
    @public
    def removeVG(self, vgUUID, options=None):
        """
        DEPRECATED: formatSD effectively removes the VG.

        Removes a volume group.

        :param vgUUID: The UUID of the VG you want removed.
        :type vgUUID: UUID
        :param options: ?
        """
        vars.task.setDefaultException(se.VolumeGroupActionError("%s" % vgUUID))
        # getSharedLock(connectionsResource...)
        try:
            lvm.removeVGbyUUID(vgUUID)
        except se.VolumeGroupDoesNotExist:
            pass

    @public
    def getTaskStatus(self, taskID, spUUID=None, options=None):
        """
        Gets the status of a task.

        :param taskID: The ID of the task you want the check.
        :type taskID: ID?
        :param spUUID: the UUID of the storage pool that the task is
                       operating on. ??
        :type spUUID: UUID (deprecated)
        :param options: ?

        :returns: a dict containing the status information of the task.
        :rtype: dict
        """
        # getSharedLock(tasksResource...)
        taskStatus = self.taskMng.getTaskStatus(taskID=taskID)
        return dict(taskStatus=taskStatus)

    @public
    def getAllTasksStatuses(self, spUUID=None, options=None):
        """
        Gets the status of all public tasks.

        :param spUUID: The UUID of the storage pool that you
                       want to check it's tasks.
        :type spUUID: UUID (deprecated)
        :options: ?
        """
        # getSharedLock(tasksResource...)
        try:
            sp = self.pools.values()[0]
        except IndexError:
            raise se.SpmStatusError()
        allTasksStatus = sp.getAllTasksStatuses()
        return dict(allTasksStatus=allTasksStatus)

    @public
    def getTaskInfo(self, taskID, spUUID=None, options=None):
        """
        Gets information about a Task.

        :param taskID: The ID of the task you want to get info on.
        :type taskID: ID ?
        :param spUUID: The UUID of the storage pool that owns this task. ?
        :type spUUID: UUID (deprecated)
        :para options: ?

        :returns: a dict with information about the task.
        :rtype: dict

        :raises: :exc:`storage_exception.UnknownTask` if a task with the
                 specified taskID doesn't exist.
        """
        # getSharedLock(tasksResource...)
        inf = self.taskMng.getTaskInfo(taskID=taskID)
        return dict(TaskInfo=inf)

    @public
    def getAllTasksInfo(self, spUUID=None, options=None):
        """
        Get the information of all the tasks in a storage pool.

        :param spUUID: The UUID of the storage pool you that want to check
                       it's tasks info.
        :type spUUID: UUID (deprecated)
        :param options: ?

        :returns: a dict of all the tasks information.
        :rtype: dict
        """
        # getSharedLock(tasksResource...)
        try:
            sp = self.pools.values()[0]
        except IndexError:
            raise se.SpmStatusError()
        allTasksInfo = sp.getAllTasksInfo()
        return dict(allTasksInfo=allTasksInfo)

    @public
    def getAllTasks(self):
        """
        Get the information for all tasks in the system.

        :returns: A dict of all tasks' information.
        :rtype: dict
        """
        ret = self.taskMng.getAllTasks()
        return dict(tasks=ret)

    @public
    def stopTask(self, taskID, spUUID=None, options=None):
        """
        Stops a task.

        :param taskID: The ID of the task you want to stop.
        :type taskID: ID?
        :param spUUID: The UUID of the storage pool that owns the task.
        :type spUUID: UUID (deprecated)
        :options: ?

        :returns: :keyword:`True` if task was stopped successfully.
        :rtype: bool
        """
        force = False
        if options:
            try:
                force = options.get("force", False)
            except:
                self.log.warning("options %s are ignored" % options)
        # getExclusiveLock(tasksResource...)
        return self.taskMng.stopTask(taskID=taskID, force=force)

    @public
    def clearTask(self, taskID, spUUID=None, options=None):
        """
        Clears a task. ?

        :param taskID: The ID of the task you want to clear.
        :type taskID: ID?
        :param spUUID: The UUID of the storage pool that owns this task.
        :type spUUID: UUID (deprecated)
        :options: ?

        :returns: :keyword:`True` if task was cleared successfully.
        :rtype: bool
        """
        # getExclusiveLock(tasksResource...)
        return self.taskMng.clearTask(taskID=taskID)

    @public
    def revertTask(self, taskID, spUUID=None, options=None):
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
        # getExclusiveLock(tasksResource...)
        return self.taskMng.revertTask(taskID=taskID)

    @public
    def getFileList(self, sdUUID, pattern='*', options=None):
        """
        Returns a list of all files in the domain filtered according to
        extension.

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

        dom = sdCache.produce(sdUUID=sdUUID)
        if not dom.isISO or dom.getStorageType() != sd.NFS_DOMAIN:
            raise se.GetFileListError(sdUUID)
        filesDict = dom.getFileList(pattern=pattern, caseSensitive=True)
        return {'files': filesDict}

    @public
    def getIsoList(self, spUUID, extension='iso', options=None):
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
            raise se.GetIsoListError(spUUID)

        # Get full iso files dictionary
        isodict = isoDom.getFileList(pattern='*.' + extension,
                                     caseSensitive=False)
        # Get list of iso images with proper permissions only
        isolist = [key for key, value in isodict.items()
                   if isodict[key]['status'] == 0]
        return {'isolist': isolist}

    @public
    def getFloppyList(self, spUUID, options=None):
        """
        Gets a list of all Floppy volumes if a storage pool.

        :param spUUID: The UUID of the storage pool you want to query.
        :type spUUID: UUID
        :param options: ?

        :returns: a dict of all the floppy volumes found.
        :rtype: dict
        """
        vars.task.setDefaultException(se.GetFloppyListError("%s" % spUUID))
        return self.getIsoList(spUUID=spUUID, extension='vfd')

    def __getSDTypeFindMethod(self, domType):
        # TODO: make sd.domain_types a real dictionary and remove this.
        # Storage Domain Types find methods
        SDTypeFindMethod = {sd.NFS_DOMAIN: nfsSD.findDomain,
                            sd.FCP_DOMAIN: blockSD.findDomain,
                            sd.ISCSI_DOMAIN: blockSD.findDomain,
                            sd.LOCALFS_DOMAIN: localFsSD.findDomain,
                            sd.POSIXFS_DOMAIN: nfsSD.findDomain,
                            sd.GLUSTERFS_DOMAIN: glusterSD.findDomain}
        return SDTypeFindMethod.get(domType)

    def __prefetchDomains(self, domType, conObj):
        uuidPatern = "????????-????-????-????-????????????"

        if domType in (sd.FCP_DOMAIN, sd.ISCSI_DOMAIN):
            uuids = tuple(blockSD.getStorageDomainsList())
        elif domType is sd.NFS_DOMAIN:
            lPath = conObj._mountCon._getLocalPath()
            self.log.debug("nfs local path: %s", lPath)
            goop = oop.getGlobalProcPool()
            uuids = tuple(os.path.basename(d) for d in
                          goop.glob.glob(os.path.join(lPath, uuidPatern)))
        elif domType is sd.POSIXFS_DOMAIN:
            lPath = conObj._getLocalPath()
            self.log.debug("posix local path: %s", lPath)
            goop = oop.getGlobalProcPool()
            uuids = tuple(os.path.basename(d) for d in
                          goop.glob.glob(os.path.join(lPath, uuidPatern)))
        elif domType is sd.GLUSTERFS_DOMAIN:
            glusterDomPath = os.path.join(sd.GLUSTERSD_DIR, "*")
            self.log.debug("glusterDomPath: %s", glusterDomPath)
            uuids = tuple(sdUUID for sdUUID, domainPath in
                          nfsSD.fileSD.scanDomains(glusterDomPath))
        elif domType is sd.LOCALFS_DOMAIN:
            lPath = conObj._path
            self.log.debug("local _path: %s", lPath)
            uuids = tuple(os.path.basename(d) for d in
                          glob.glob(os.path.join(lPath, uuidPatern)))
        else:
            uuids = tuple()
            self.log.warn("domType %s does not support prefetch")

        self.log.debug("Found SD uuids: %s", uuids)
        findMethod = self.__getSDTypeFindMethod(domType)
        return dict.fromkeys(uuids, findMethod)

    @deprecated
    @public(logger=logged(printers={'conList': connectionListPrinter}))
    def connectStorageServer(self, domType, spUUID, conList, options=None):
        """
        Connects to a storage low level entity (server).

        :param domType: The type of the connection sometimes expressed as the
                        corresponding domain type
        :param spUUID: deprecated, unused
        :param conList: A list of connections. Each connection being a dict
                        with keys depending on the type
        :type conList: list
        :param options: unused

        :returns: a list of statuses status will be 0 if connection was
                  successful
        :rtype: dict
        """
        cons = loggableConList(conList=conList)
        vars.task.setDefaultException(
            se.StorageServerConnectionError(
                "domType=%s, spUUID=%s, conList=%s" %
                (domType, spUUID, cons)))

        res = []
        for conDef in conList:
            conInfo = _connectionDict2ConnectionInfo(domType, conDef)
            conObj = storageServer.ConnectionFactory.createConnection(conInfo)
            try:
                self._connectStorageOverIser(conDef, conObj, domType)
                conObj.connect()
            except Exception as err:
                self.log.error(
                    "Could not connect to storageServer", exc_info=True)
                status, _ = self._translateConnectionError(err)
            else:
                status = 0
                try:
                    doms = self.__prefetchDomains(domType, conObj)
                except:
                    self.log.debug("prefetch failed: %s",
                                   sdCache.knownSDs,  exc_info=True)
                else:
                    sdCache.knownSDs.update(doms)

            self.log.debug("knownSDs: {%s}", ", ".join("%s: %s.%s" %
                           (k, v.__module__, v.__name__)
                           for k, v in sdCache.knownSDs.iteritems()))

            res.append({'id': conDef["id"], 'status': status})

        # Connecting new device may change the visible storage domain list
        # so invalidate caches
        sdCache.invalidateStorage()
        return dict(statuslist=res)

    @deprecated
    def _connectStorageOverIser(self, conDef, conObj, conTypeId):
        """
        Tries to connect the storage server over iSER.
        This applies if the storage type is iSCSI and 'iser' is in
        the configuration option 'iscsi_default_ifaces'.
        """
        # FIXME: remove this method when iface selection is in higher interface
        typeName = CON_TYPE_ID_2_CON_TYPE[conTypeId]
        if typeName == 'iscsi' and 'initiatorName' not in conDef:
            ifaces = config.get('irs', 'iscsi_default_ifaces').split(',')
            if 'iser' in ifaces:
                conObj._iface = iscsi.IscsiInterface('iser')
                try:
                    conObj.connect()
                    conObj.disconnect()
                except:
                    conObj._iface = iscsi.IscsiInterface('default')

    @deprecated
    @public(logger=logged(printers={'conList': connectionListPrinter}))
    def validateStorageServerConnection(self, domType, spUUID, conList,
                                        options=None):
        """
        This call is useless and slow. It check different things in different
        levels for different storage types and does not guarantee a future
        connect will succeed. Until someone actually defines what is actually
        being validated (params? hostname? permissions? authentication tokens?)
        I suggest to just disable this verb so it doesn't make any NFS
        connection take twice as long and be exponentially more complex.
        """
        res = []
        for con in conList:
            res.append({'id': con['id'], 'status': 0})

        return dict(statuslist=res)

    @deprecated
    @public(logger=logged(printers={'conList': connectionListPrinter}))
    def disconnectStorageServer(self, domType, spUUID, conList, options=None):
        """
        Disconnects from a storage low level entity (server).

        :param domType: The type of the connection expressed as the sometimes
                        corresponding domains type
        :param spUUID: deprecated, unused
        :param conList: A list of connections. Each connection being a dict
                        with keys depending on the type
        :type conList: list
        :param options: unused

        :returns: a list of statuses status will be 0 if disconnection was
                  successful
        :rtype: dict
        """
        cons = loggableConList(conList=conList)
        vars.task.setDefaultException(
            se.StorageServerDisconnectionError(
                "domType=%s, spUUID=%s, conList=%s" %
                (domType, spUUID, cons)))

        res = []
        for conDef in conList:
            conInfo = _connectionDict2ConnectionInfo(domType, conDef)
            conObj = storageServer.ConnectionFactory.createConnection(conInfo)
            try:
                conObj.disconnect()
                status = 0
            except Exception as err:
                self.log.error("Could not disconnect from storageServer",
                               exc_info=True)
                status, _ = self._translateConnectionError(err)

            res.append({'id': conDef["id"], 'status': status})

        # Disconnecting a device may change the visible storage domain list
        # so invalidate the caches
        sdCache.refreshStorage()
        return dict(statuslist=res)

    def _translateConnectionError(self, e):
        if e is None:
            return 0, ""

        if isinstance(e, mount.MountError):
            return se.MountError.code, se.MountError.message
        if isinstance(e, iscsi.iscsiadm.IscsiAuthenticationError):
            return se.iSCSILoginAuthError.code, se.iSCSILoginAuthError.message
        if isinstance(e, iscsi.iscsiadm.IscsiInterfaceError):
            return se.iSCSIifaceError.code, se.iSCSIifaceError.message
        if isinstance(e, iscsi.iscsiadm.IscsiError):
            return se.iSCSISetupError.code, se.iSCSISetupError.message

        if hasattr(e, 'code'):
            return e.code, e.message

        return se.GeneralException.code, str(e)

    @public
    def storageServer_ConnectionRefs_statuses(self):
        """
        Gets a list of all managed and active unmanaged storage connections and
        their current status.

        :rtype: a dict in the format of
           {id: {'connected': True/False,
                 'lastError': (errcode, message),
                 'connectionInfo': :class:: storageServer.ConnectionInfo}

        """
        vars.task.setDefaultException(se.StorageServerActionError())
        res = {}
        conMonitor = self._connectionMonitor
        managedConnections = conMonitor.getMonitoredConnectionsDict()
        for conId, con in managedConnections.iteritems():
            conErr = conMonitor.getLastError(conId)
            errInfo = self._translateConnectionError(conErr)
            conInfo = self._connectionAliasRegistrar.getConnectionInfo(conId)
            params = conInfo.params
            if conInfo.type == 'iscsi':
                conInfoDict = {'target':
                               {'portal':
                                   misc.namedtuple2dict(params.target.portal),
                                'tpgt': params.target.tpgt,
                                'iqn': params.target.iqn},
                               'iface': params.iface.name}
            else:
                conInfoDict = misc.namedtuple2dict(params)
                for key in conInfoDict:
                    if conInfoDict[key] is None:
                        conInfoDict[key] = 'default'

            r = {"connected": con.isConnected(),
                 "lastError": errInfo,
                 "connectionInfo": {
                     "type": conInfo.type,
                     "params": conInfoDict}}

            res[conId] = r

        return dict(connectionslist=res)

    @public
    def getStoragePoolInfo(self, spUUID, options=None):
        """
        Gets info about a storage pool.

        :param spUUID: The UUID of the storage pool you want to get info on.
        :type spUUID: UUID
        :param options: ?

        :returns: getPool(spUUID).getInfo
        """
        vars.task.setDefaultException(
            se.StoragePoolActionError("spUUID=%s" % spUUID))
        vars.task.getSharedLock(STORAGE, spUUID)
        return self.getPool(spUUID).getInfo()

    @public
    def createStorageDomain(self, storageType, sdUUID, domainName,
                            typeSpecificArg, domClass,
                            domVersion=constants.SUPPORTED_DOMAIN_VERSIONS[0],
                            options=None):
        """
        Creates a new storage domain.

        :param storageType: The storage type of the new storage
                            domain (eg. NFS).
        :type storageType: int (as defined in sd.py).
        :param sdUUID: The UUID of the new storage domain.
        :type sdUUID: UUID
        :param domainName: The human readable name of the new storage domain.
        :type domainName: str
        :param typeSpecificArg: Arguments that are specific to the
                                storage type.
        :type typeSpecificArg: dict
        :param domClass: The class of the new storage domain (eg. iso, data).
        :type domClass: int (as defined in sd.py)
        :param options: unused
        """
        msg = ("storageType=%s, sdUUID=%s, domainName=%s, "
               "domClass=%s, typeSpecificArg=%s domVersion=%s" %
               (storageType, sdUUID, domainName, domClass,
                typeSpecificArg, domVersion))
        domVersion = int(domVersion)
        vars.task.setDefaultException(se.StorageDomainCreationError(msg))
        misc.validateUUID(sdUUID, 'sdUUID')
        self.validateNonDomain(sdUUID)

        if not domClass in sd.DOMAIN_CLASSES.keys():
            raise se.StorageDomainClassError()

        sd.validateDomainVersion(domVersion)

        # getSharedLock(connectionsResource...)
        # getExclusiveLock(sdUUID...)
        if storageType in sd.BLOCK_DOMAIN_TYPES:
            newSD = blockSD.BlockStorageDomain.create(
                sdUUID, domainName, domClass, typeSpecificArg, storageType,
                domVersion)
        elif storageType in (sd.NFS_DOMAIN, sd.POSIXFS_DOMAIN):
            newSD = nfsSD.NfsStorageDomain.create(
                sdUUID, domainName, domClass, typeSpecificArg, storageType,
                domVersion)
        elif storageType == sd.GLUSTERFS_DOMAIN:
            newSD = glusterSD.GlusterStorageDomain.create(
                sdUUID, domainName, domClass, typeSpecificArg, storageType,
                domVersion)
        elif storageType == sd.LOCALFS_DOMAIN:
            newSD = localFsSD.LocalFsStorageDomain.create(
                sdUUID, domainName, domClass, typeSpecificArg, storageType,
                domVersion)
        else:
            raise se.StorageDomainTypeError(storageType)

        findMethod = self.__getSDTypeFindMethod(storageType)
        sdCache.knownSDs[sdUUID] = findMethod
        self.log.debug("knownSDs: {%s}", ", ".join("%s: %s.%s" %
                       (k, v.__module__, v.__name__)
                       for k, v in sdCache.knownSDs.iteritems()))

        sdCache.manuallyAddDomain(newSD)

    @public
    def validateStorageDomain(self, sdUUID, options=None):
        """
        Validates that the storage domain is accessible.

        :param sdUUID: The UUID of the storage domain you want to validate.
        :type sdUUID: UUID
        :param options: ?

        :returns: :keyword:`True` if storage domain is valid.
        :rtype: bool
        """
        vars.task.setDefaultException(
            se.StorageDomainCreationError("sdUUID=%s" % sdUUID))
        return sdCache.produce(sdUUID=sdUUID).validate()

    # TODO: Remove this  function when formatStorageDomain() is removed.
    def _recycle(self, dom):
        sdUUID = dom.sdUUID
        try:
            dom.format(dom.sdUUID)
            # dom is a DomainProxy, attribute operations will trigger the
            # domain added to sdCache again. Delete the local variable binding
            # here to avoid visiting its attribute accidentally.
            del dom
        finally:
            try:
                sdCache.manuallyRemoveDomain(sdUUID)
            except KeyError:
                self.log.warn("Storage domain %s doesn't exist in cache. "
                              "Leftovers are recycled.", sdUUID)

    @public
    def formatStorageDomain(self, sdUUID, autoDetach=False, options=None):
        """
        Formats a detached storage domain.

        .. warning::
            This removes all data from the storage domain.

        :param sdUUID: The UUID for the storage domain you want to format.
        :param autoDetach: DEPRECATED
        :type sdUUID: UUID
        :param options: ?

        :returns: Nothing
        """
        multipath.rescan()
        vars.task.setDefaultException(
            se.StorageDomainActionError("sdUUID=%s" % sdUUID))
        # getSharedLock(connectionsResource...)

        vars.task.getExclusiveLock(STORAGE, sdUUID)
        for p in self.pools.values():
            # Avoid format if domain part of connected pool
            domDict = p.getDomains()
            if sdUUID in domDict.keys():
                raise se.CannotFormatStorageDomainInConnectedPool(sdUUID)

        # For domains that attached to disconnected pool, format domain if
        # 'autoDetach' flag set
        sd = sdCache.produce(sdUUID=sdUUID)
        try:
            sd.invalidateMetadata()
            # TODO: autoDetach is True
            if not misc.parseBool(autoDetach) and sd.getPools():
                raise se.CannotFormatAttachedStorageDomain(sdUUID)
            # Allow format also for broken domain
        except (se.StorageDomainMetadataNotFound, se.MetaDataGeneralError,
                se.MiscFileReadException, se.MiscBlockReadException,
                se.MiscBlockReadIncomplete), e:
            self.log.warn("Domain %s has problem with metadata. Continue "
                          "formatting... (%s)", sdUUID, e)

        self._recycle(sd)

    @public
    def setStorageDomainDescription(self, sdUUID, description, options=None):
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

        vars.task.setDefaultException(
            se.StorageDomainActionError(
                "sdUUID=%s, description=%s" % (sdUUID, description)))
        dom = sdCache.produce(sdUUID=sdUUID)
        vars.task.getExclusiveLock(STORAGE, sdUUID)
        dom.setDescription(descr=description)

    @public
    def getStorageDomainInfo(self, sdUUID, options=None):
        """
        Gets the info of a storage domain.

        :param sdUUID: The UUID of the storage domain you want to get
                       info about.
        :type sdUUID: UUID
        :param options: ?

        :returns: a dict containing the information about the domain.
        :rtype: dict
        """
        vars.task.setDefaultException(
            se.StorageDomainActionError("sdUUID=%s" % sdUUID))
        dom = self.validateSdUUID(sdUUID)
        # getSharedLock(connectionsResource...)

        vars.task.getSharedLock(STORAGE, sdUUID)
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
        info.update({'lver': -1, 'spm_id': -1, 'master_ver': 0})
        if dom.getDomainRole() == sd.MASTER_DOMAIN:
            # make sure it's THE master
            try:
                pool = self.getPool(dom.getPools()[0])
                if pool.masterDomain.sdUUID == sdUUID:
                    poolInfo = pool.getInfo()
                    for key in ['lver', 'spm_id', 'master_ver']:
                        info[key] = poolInfo['info'][key]
            except se.StoragePoolUnknown:
                # Its pool is not connected
                pass

        return dict(info=info)

    @public
    def getStorageDomainStats(self, sdUUID, options=None):
        """
        Gets a storage domain's statistics.

        :param sdUUID: The UUID of the storage domain that you want to get
                       it's statistics.
        :type sdUUID: UUID
        :param options: ?

        :returns: a dict containing the statistics information.
        :rtype: dict
        """
        vars.task.setDefaultException(
            se.StorageDomainActionError("sdUUID=%s" % sdUUID))
        vars.task.getSharedLock(STORAGE, sdUUID)
        dom = sdCache.produce(sdUUID=sdUUID)
        dom.refresh()
        stats = dom.getStats()
        return dict(stats=stats)

    @public
    def getStorageDomainsList(
            self, spUUID=None, domainClass=None, storageType=None,
            remotePath=None, options=None):
        """
        Returns a List of all or pool specific storage domains.

        :param spUUID: The UUID of the the the storage pool you want to list.
                       If spUUID equals to :attr:`~volume.BLANK_UUID` all
                       pools will be listed.
        :type spUUID: UUID
        :param options: ?

        :returns: a dict containing list of storage domains.
        :rtype: dict
        """
        vars.task.setDefaultException(
            se.StorageDomainActionError("spUUID: %s" % spUUID))
        sdCache.refreshStorage()
        if spUUID and spUUID != volume.BLANK_UUID:
            domList = self.getPool(spUUID).getDomains()
            domains = domList.keys()
        else:
            # getSharedLock(connectionsResource...)
            domains = sdCache.getUUIDs()

        for sdUUID in domains[:]:
            try:
                dom = sdCache.produce(sdUUID=sdUUID)
                # Filter domains according to 'storageType'
                if storageType and storageType != dom.getStorageType():
                    domains.remove(sdUUID)
                    continue
                # Filter domains according to 'domainClass'
                if domainClass and domainClass != dom.getDomainClass():
                    domains.remove(sdUUID)
                    continue
                # Filter domains according to 'remotePath'
                if (remotePath and
                    fileUtils.transformPath(remotePath) !=
                        dom.getRemotePath()):
                    domains.remove(sdUUID)
                    continue
            except Exception:
                self.log.error("Unexpected error", exc_info=True)
                domains.remove(sdUUID)
                continue

        return dict(domlist=domains)

    def __fillPVDict(self, devInfo, pv, devtype):
        info = {}
        info["vendorID"] = devInfo["vendor"]
        info["productID"] = devInfo["product"]
        info["serial"] = devInfo["serial"]
        info["pathstatus"] = []
        for pathInfo in devInfo['paths']:
            info["pathstatus"].append(pathInfo)
        info["pathlist"] = devInfo["connections"]
        info["fwrev"] = "0000"
        info["devtype"] = devtype
        info["capacity"] = str(pv.size)
        info["devcapacity"] = str(pv.dev_size)
        info["vgUUID"] = str(pv.vg_uuid)
        info["pvUUID"] = str(pv.uuid)
        info["GUID"] = str(pv.guid)
        return info

    @public
    def getVGList(self, storageType=None, options=None):
        """
        Returns a list all VGs.

        :param options: ?

        :returns: a dict containing a list of all VGs.
        :rtype: dict
        """
        vars.task.setDefaultException(se.VolumeGroupActionError())
        sdCache.refreshStorage()
        # getSharedLock(connectionsResource...)
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
            # Should be fresh from the cache
            devNames.extend(imap(getGuid, lvm.listPVNames(vg.name)))
            # dict(vg.attr._asdict()) because nametuples and OrderedDict are
            # not properly marshalled
            vgInfo = {'name': vg.name, 'vgUUID': vg.uuid,
                      'vgsize': str(vg.size), 'vgfree': str(vg.free),
                      'type': "", 'attr': dict(vg.attr._asdict()),
                      'state': vg.partial, "pvlist": []}
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
                    self.log.warn("dev %s was not found %s",
                                  getGuid(pv), pathDict)
                    continue
                if vgType is None:
                    vgType = dev["devtype"]
                elif (vgType != multipath.DEV_MIXED and
                      vgType != dev["devtype"]):
                    vgType = multipath.DEV_MIXED

                pvInfo = lvm.getPV(pv)
                vgInfo['pvlist'].append(self.__fillPVDict(dev, pvInfo, vgType))

            if vgType == multipath.DEV_FCP:
                vgType = sd.FCP_DOMAIN
            elif vgType == multipath.DEV_ISCSI:
                vgType = sd.ISCSI_DOMAIN
            else:
                # TODO: Allow for mixed vgs to be specified as such in the API
                vgType = sd.ISCSI_DOMAIN

            vgInfo["type"] = vgType

    @public
    def getVGInfo(self, vgUUID, options=None):
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
        vars.task.setDefaultException(se.VolumeGroupActionError("%s" % vgUUID))
        # getSharedLock(connectionsResource...)
        return dict(info=self.__getVGsInfo([vgUUID])[0])

    @public(logger=logged(printers={'con': connectionPrinter}))
    def discoverSendTargets(self, con, options=None):
        """
        Discovers iSCSI targets.

        :param con: A dict containing connection information of some sort.?
        :type con: dict?
        :param options: ?

        :returns: a dict containing the send targets that were discovered.
        :rtype: dict
        """
        ip = con['connection']
        port = int(con['port'])
        username = con['user']
        password = con['password']
        if username == "":
            username = password = None

        iface = iscsi.IscsiInterface("default")
        portal = iscsi.IscsiPortal(ip, port)
        cred = None
        if username or password:
            cred = iscsi.ChapCredentials(username, password)

        try:
            targets = iscsi.discoverSendTargets(iface, portal, cred)
        except iscsi.iscsiadm.IscsiError as e:
            self.log.error("Discovery failed", exc_info=True)
            raise se.iSCSIDiscoveryError(portal, e)
        # I format the data to it's original textual representation the
        # response. Why you ask? Backward compatibility! At least now if
        # iscsiadm changes the output we can handle it gracefully
        fullTargets = []
        partialTargets = []
        for target in targets:
            fullTargets.append("%s:%d,%d %s" %
                               (target.portal.hostname, target.portal.port,
                                target.tpgt, target.iqn))
            partialTargets.append(target.iqn)

        return dict(targets=partialTargets, fullTargets=fullTargets)

    @public
    def cleanupUnusedConnections(self, options=None):
        """
        .. warning::
            This method is not yet implemented.
        """
        # vars.task.setDefaultException(se.ChangeMeError("%s" % args))
        # getExclusiveLock(connectionsResource...)
        # TODO: Implement
        pass

    @public
    def refreshVolume(self, sdUUID, spUUID, imgUUID, volUUID):
        """
        Refresh low level volume after change in the shared storage initiated
        from another host
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
        return sdCache.produce(
            sdUUID=sdUUID).produceVolume(imgUUID=imgUUID,
                                         volUUID=volUUID).refreshVolume()

    @public
    def getVolumeSize(self, sdUUID, spUUID, imgUUID, volUUID, options=None):
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
        # Return string because xmlrpc's "int" is very limited
        apparentsize = str(volume.Volume.getVSize(sdUUID, imgUUID,
                                                  volUUID, bs=1))
        truesize = str(volume.Volume.getVTrueSize(sdUUID, imgUUID,
                                                  volUUID, bs=1))
        return dict(apparentsize=apparentsize, truesize=truesize)

    @public
    def setVolumeSize(self, sdUUID, spUUID, imgUUID, volUUID, capacity):
        capacity = int(capacity)
        vol = sdCache.produce(sdUUID).produceVolume(imgUUID, volUUID)
        # Values lower than 1 are used to uncommit (marking as inconsisent
        # during a transaction) the volume size.
        if capacity > 0:
            sectors = (capacity + SECTOR_SIZE - 1) / SECTOR_SIZE
        else:
            sectors = capacity
        vol.setSize(sectors)

    @public
    def getVolumeInfo(self, sdUUID, spUUID, imgUUID, volUUID, options=None):
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
        vars.task.getSharedLock(STORAGE, sdUUID)
        info = sdCache.produce(
            sdUUID=sdUUID).produceVolume(imgUUID=imgUUID,
                                         volUUID=volUUID).getInfo()
        return dict(info=info)

    @public
    def getVolumePath(self, sdUUID, spUUID, imgUUID, volUUID, options=None):
        """
        Gets the path to a volume.

        .. warning::
            This method is obsolete and is kept only for testing purposes;
            use prepareImage instead.

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
        vars.task.getSharedLock(STORAGE, sdUUID)
        path = sdCache.produce(
            sdUUID=sdUUID).produceVolume(imgUUID=imgUUID,
                                         volUUID=volUUID).getVolumePath()
        return dict(path=path)

    @public
    def appropriateDevice(self, guid, thiefId):
        """
        Change ownership of the guid device to vdsm:qemu

        Warning: Internal use only.
        """
        supervdsm.getProxy().appropriateDevice(guid, thiefId)
        supervdsm.getProxy().udevTrigger(guid)
        devPath = devicemapper.DMPATH_FORMAT % guid
        utils.retry(partial(fileUtils.validateQemuReadable, devPath),
                    expectedException=OSError,
                    timeout=QEMU_READABLE_TIMEOUT)

    @public
    def inappropriateDevices(self, thiefId):
        """
        Warning: Internal use only.
        """
        fails = supervdsm.getProxy().rmAppropriateRules(thiefId)
        if fails:
            self.log.error("Failed to remove the following rules: %s", fails)

    @public
    def prepareVolume(self, sdUUID, spUUID, imgUUID, volUUID, rw=True,
                      options=None):
        """
        Prepares a volume (used in SAN).
        Activates LV and rebuilds 'images' subtree.

        .. warning::
            This method is obsolete and is kept only for testing purposes;
            use prepareImage instead.

        :param sdUUID: The UUID of the storage domain that owns the volume.
        :type sdUUID: UUID
        :param spUUID: The UUID of the storage pool that owns the volume.
        :type spUUID: UUID
        :param imgUUID: The UUID of the image contained on the volume.
        :type imgUUID: UUID
        :param volUUID: The UUID of the volume you want to prepare.
        :type volUUID: UUID
        :param rw: Should the volume be set as RW. ?
        :type rw: bool
        :param options: ?
        """
        vars.task.getSharedLock(STORAGE, sdUUID)
        imageResourcesNamespace = sd.getNamespace(sdUUID, IMAGE_NAMESPACE)
        lockType = rm.LockType.exclusive if rw else rm.LockType.shared
        timeout = config.getint('irs', 'prepare_image_timeout') / 1000.0

        imgResource = rmanager.acquireResource(imageResourcesNamespace,
                                               imgUUID, lockType, timeout)
        try:
            vol = sdCache.produce(sdUUID=sdUUID).produceVolume(
                imgUUID=imgUUID, volUUID=volUUID)
            # NB We want to be sure that at this point HSM does not use stale
            # LVM cache info, so we call refresh explicitly. We may want to
            # remove this refresh later, when we come up with something better.
            vol.refreshVolume()
            vol.prepare(rw=rw)

            self._preparedVolumes[sdUUID + volUUID].append(imgResource)
        except:
            self.log.error("Prepare volume %s in domain %s failed",
                           volUUID, sdUUID, exc_info=True)
            imgResource.release()
            raise

    @public
    def teardownVolume(self, sdUUID, spUUID, imgUUID, volUUID, rw=False,
                       options=None):
        """
        Tears down a volume (used in SAN).
        Deactivates LV.

        .. warning::
            This method is obsolete and is kept only for testing purposes;
            use teardownImage instead.

        :param sdUUID: The UUID of the storage domain that owns the volume.
        :type sdUUID: UUID
        :param spUUID: The UUID of the storage pool that owns the volume.
        :type spUUID: UUID
        :param imgUUID: The UUID of the image contained on the volume.
        :type imgUUID: UUID
        :param volUUID: The UUID of the volume you want to teardown.
        :type volUUID: UUID
        :param rw: deprecated
        :param options: ?
        """
        vars.task.getSharedLock(STORAGE, sdUUID)
        try:
            imgResource = self._preparedVolumes[sdUUID + volUUID].pop()
        except IndexError:
            raise se.VolumeWasNotPreparedBeforeTeardown()

        imgResource.release()

        try:
            volclass = sdCache.produce(sdUUID).getVolumeClass()
            volclass.teardown(sdUUID=sdUUID, volUUID=volUUID)
        except Exception:
            self.log.warn("Problem tearing down volume", exc_info=True)

    @public
    def prepareImage(self, sdUUID, spUUID, imgUUID, volUUID=None):
        """
        Prepare an image activating the needed volumes.
        Returns the path to the leaf and the ordered volume chain.

        :param sdUUID: The UUID of the storage domain that owns the volume.
        :type sdUUID: UUID
        :param spUUID: The UUID of the storage pool that owns the volume.
        :type spUUID: UUID
        :param imgUUID: The UUID of the image contained on the volume.
        :type imgUUID: UUID
        """
        vars.task.getSharedLock(STORAGE, sdUUID)

        img = image.Image(os.path.join(self.storage_repository, spUUID))
        imgVolumes = img.prepare(sdUUID, imgUUID, volUUID)

        chain = []
        dom = sdCache.produce(sdUUID=sdUUID)

        for vol in imgVolumes:
            volInfo = {'domainID': sdUUID, 'imageID': imgUUID,
                       'volumeID': vol.volUUID, 'path': vol.getVolumePath(),
                       'vmVolInfo': vol.getVmVolumeInfo()}

            if config.getboolean('irs', 'use_volume_leases'):
                leasePath, leaseOffset = dom.getVolumeLease(vol.imgUUID,
                                                            vol.volUUID)

                if leasePath and leaseOffset is not None:
                    volInfo.update({
                        'leasePath': leasePath,
                        'leaseOffset': leaseOffset,
                        'shared': (vol.getVolType() ==
                                   volume.type2name(volume.SHARED_VOL)),
                    })

            chain.append(volInfo)

        return {'path': chain[-1]['path'], 'info': chain[-1]['vmVolInfo'],
                'chain': chain}

    @public
    def teardownImage(self, sdUUID, spUUID, imgUUID, volUUID=None):
        """
        Teardown an image deactivating the volumes.

        :param sdUUID: The UUID of the storage domain that owns the volume.
        :type sdUUID: UUID
        :param spUUID: The UUID of the storage pool that owns the volume.
        :type spUUID: UUID
        :param imgUUID: The UUID of the image contained on the volume.
        :type imgUUID: UUID
        """
        vars.task.getSharedLock(STORAGE, sdUUID)

        img = image.Image(os.path.join(self.storage_repository, spUUID))
        img.teardown(sdUUID, imgUUID, volUUID)

    @public
    def getVolumesList(self, sdUUID, spUUID, imgUUID=volume.BLANK_UUID,
                       options=None):
        """
        Gets a list of all volumes.

        :param spUUID: The UUID of the storage pool that manages the storage
                       domain you want to query.
        :type spUUID: UUID
        :param sdUUID: The UUID of the storage domain you want to query.
        :type sdUUID: UUID
        :param imgUUID: The UUID of the an image you want to filter the
                        results.
                        if imgUUID equals :attr:`~volume.BLANK_UUID` no
                        filtering will be done.
        """
        vars.task.getSharedLock(STORAGE, sdUUID)
        dom = sdCache.produce(sdUUID=sdUUID)
        if imgUUID == volume.BLANK_UUID:
            images = list(dom.getAllImages())
        else:
            images = [imgUUID]

        uuidlist = []
        repoPath = os.path.join(self.storage_repository, spUUID)
        for img in images:
            uuidlist += (dom.getVolumeClass().
                         getImageVolumes(repoPath, sdUUID, img))
        self.log.info("List of volumes is %s", uuidlist)
        return dict(uuidlist=uuidlist)

    @public
    def getImagesList(self, sdUUID, options=None):
        """
        Gets a list of all the images of specific domain.

        :param sdUUID: The UUID of the storage domain you want to query.
        :type sdUUID: UUID.
        :param options: ?

        :returns: a dict with a list of the images belonging to the specified
                  domain.
        :rtype: dict
        """
        vars.task.getSharedLock(STORAGE, sdUUID)
        dom = sdCache.produce(sdUUID=sdUUID)
        images = dom.getAllImages()
        return dict(imageslist=list(images))

    @public
    def storageServer_ConnectionRefs_acquire(self, conRefArgs):
        """
        Acquire connection references.

        The method will persist the connection info in VDSM and create a
        connection reference. Connection references can be accessed by their
        IDs where appropriate.

        Once a connection reference is created VDSM will try and connect to the
        target specified by the connection information if not already
        connected. VDSM will keep the target connected as long as a there is a
        reference pointing to the same connection information.

        :param conRefArgs: A map in the form of
        {id: :class:: storageServer.ConnectionInfo), ... }

        :rtype: dict {id: errcode, ...}

        """
        res = {}
        for refId, conInfo in conRefArgs.iteritems():
            status = 0
            try:
                conInfo = storageServer.dict2conInfo(conInfo)
            except Exception:
                res[refId] = se.StorageServerValidationError.code
                continue

            try:
                self._connectionAliasRegistrar.register(refId, conInfo)
            except KeyError:
                status = se.StorageServerConnectionRefIdAlreadyInUse.code
            except Exception as e:
                self.log.error("Could not acquire resource ref for '%s'",
                               refId, exc_info=True)
                status, _ = self._translateConnectionError(e)

            try:
                self._connectionMonitor.manage(refId)
            except Exception as e:
                self.log.error("Could not acquire resource ref for '%s'",
                               refId, exc_info=True)
                self._connectionAliasRegistrar.unregister(refId)
                status, _ = self._translateConnectionError(e)

            res[refId] = status

        return {"results": res}

    @public
    def storageServer_ConnectionRefs_release(self, refIDs):
        """
        Release connection references.

        Releases the references, if a connection becomes orphaned as a result
        of this action the connection will be disconnected. The connection
        might remain active if VDSM detects that it is still under use but
        will not be kept alive by VDSM anymore.

        :param refIDs: a list of strings, each string representing a refIDs.

        :rtype: dict {id: errcode, ...}

        """
        res = {}
        for refID in refIDs:
            status = 0
            try:
                self._connectionMonitor.unmanage(refID)
            except KeyError:
                # It's OK if we this alias is not managed
                pass

            try:
                self._connectionAliasRegistrar.unregister(refID)
            except KeyError:
                status = se.StorageServerConnectionRefIdAlreadyInUse.code
            except Exception as e:
                self.log.error("Could not release resource ref for '%s'",
                               refID, exc_info=True)
                status, _ = self._translateConnectionError(e)

            res[refID] = status

        return {"results": res}

    @deprecated
    @public
    def getImageDomainsList(self, spUUID, imgUUID, options=None):
        """
        Gets a list of all data domains in the pool that contains imgUUID.

        :param spUUID: The UUID of the storage pool you want to query.
        :type spUUID: UUID
        :param imgUUID: The UUID of the image you want to filter by.
        :type spUUID: UUID
        :param options: ?

        :returns: a dict containing the list of domains found.
        :rtype: dict
        """
        vars.task.setDefaultException(
            se.GetStorageDomainListError("spUUID=%s imgUUID=%s" %
                                         (spUUID, imgUUID)))
        vars.task.getSharedLock(STORAGE, spUUID)
        pool = self.getPool(spUUID)
        # Find out domain list from the pool metadata
        activeDoms = sorted(pool.getDomains(activeOnly=True).keys())
        imgDomains = []
        for sdUUID in activeDoms:
            dom = sdCache.produce(sdUUID=sdUUID)
            if dom.isData():
                with rmanager.acquireResource(STORAGE, sdUUID,
                                              rm.LockType.shared):
                    try:
                        imgs = dom.getAllImages()
                    except se.StorageDomainDoesNotExist:
                        self.log.error("domain %s can't be reached.",
                                       sdUUID, exc_info=True)
                    else:
                        if imgUUID in imgs:
                            imgDomains.append(sdUUID)

        return dict(domainslist=imgDomains)

    @public
    def prepareForShutdown(self, options=None):
        """
        Prepares to shutdown host.
        Stops all tasks.

        .. note::
            shutdown cannot be cancelled, must stop all actions.

        :param options: ?
        """
        # TODO: Implement!!!! TBD: required functionality (stop hsm tasks,
        #                          stop spm tasks if spm etc.)
        try:
            self._connectionMonitor.stopMonitoring()
            sp.StoragePool.cleanupMasterMount()
            self.__releaseLocks()

            for spUUID in self.pools:
                # Stop spmMailer thread
                if self.pools[spUUID].spmMailer:
                    self.pools[spUUID].spmMailer.stop()
                    self.pools[spUUID].spmMailer.tp.joinAll(waitForTasks=False)

                # Stop hsmMailer thread
                if self.pools[spUUID].hsmMailer:
                    self.pools[spUUID].hsmMailer.stop()

                # Stop repoStat threads
                for pool in self.pools.values():
                    try:
                        pool.stopMonitoringDomains()
                    except Exception:
                        self.log.warning("Failed to stop RepoStats thread",
                                         exc_info=True)
                        continue

            self.taskMng.prepareForShutdown()
        except:
            pass

    @classmethod
    def __releaseLocks(cls):
        """
        Releases all locks held by the machine.
        """
        # We are initializing the vdsm and should not be holding ANY lock
        # so we make sure no locks are held by the machine (e.g. because of
        # previous vdsm runs)
        # killall -INT will trigger lock release (proper shutdown)
        lockCmd = config.get('irs', 'lock_cmd')
        try:
            misc.killall(lockCmd, signal.SIGUSR1, group=True)
        except OSError as e:
            if e.errno == errno.ESRCH:
                return
            raise

        cls.log.warning("Found lease locks, releasing")
        for i in range(10):
            time.sleep(1)

            try:
                misc.killall(lockCmd, 0)
            except OSError as e:
                if e.errno == errno.ESRCH:
                    return

        cls.log.warning("Could not release locks, killing lock processes")
        misc.killall(lockCmd, signal.SIGKILL, group=True)

    @public
    def fenceSpmStorage(self, spUUID, lastOwner, lastLver, options=None):
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
        vars.task.setDefaultException(
            se.SpmFenceError("spUUID=%s, lastOwner=%s, lastLver=%s" %
                             (spUUID, lastOwner, lastLver)))
        pool = self.getPool(spUUID)
        pool.invalidateMetadata()
        vars.task.getExclusiveLock(STORAGE, spUUID)
        pool.forceFreeSpm()
        st = {'spmStatus': pool.spmRole, 'spmLver': pool.getSpmLver(),
              'spmId': pool.getSpmId()}
        return dict(spm_st=st)

    @public
    def upgradeStoragePool(self, spUUID, targetDomVersion):
        targetDomVersion = int(targetDomVersion)
        pool = self.getPool(spUUID)
        pool._upgradePool(targetDomVersion)
        return {"upgradeStatus": "started"}

    @public
    def repoStats(self, options=None):
        """
        Collects a storage repository's information and stats.

        :param options: ?

        :returns: result
        """
        result = {}

        for p in self.pools.values():
            repo_stats = p.getRepoStats()

            for d in repo_stats:
                result[d] = repo_stats[d]['result']

        return result
