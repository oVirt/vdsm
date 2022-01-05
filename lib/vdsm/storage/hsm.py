#
# Copyright 2009-2019 Red Hat, Inc.
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
from __future__ import absolute_import
from __future__ import division

import os
import logging
import glob
from fnmatch import fnmatch
from collections import defaultdict
from functools import partial
import errno
import time
import signal
import numbers
import stat

import six
from six.moves import map

from vdsm import jobs
from vdsm import utils
from vdsm.common import api
from vdsm.common import concurrent
from vdsm.common import exception
from vdsm.common import function
from vdsm.common import supervdsm
from vdsm.common.marks import deprecated
from vdsm.common.threadlocal import vars
from vdsm.common.time import monotonic_time
from vdsm.common.units import MiB, GiB
from vdsm.config import config
from vdsm.storage import blockSD
from vdsm.storage import clusterlock
from vdsm.storage import constants as sc
from vdsm.storage import devicemapper
from vdsm.storage import dispatcher
from vdsm.storage import exception as se
from vdsm.storage import fileUtils
from vdsm.storage import glusterSD
from vdsm.storage import image
from vdsm.storage import imagetickets
from vdsm.storage import iscsi
from vdsm.storage import localFsSD
from vdsm.storage import lvm
from vdsm.storage import merge
from vdsm.storage import mpathhealth
from vdsm.storage import misc
from vdsm.storage import monitor
from vdsm.storage import mount
from vdsm.storage import multipath
from vdsm.storage import nbd
from vdsm.storage import nfsSD
from vdsm.storage import outOfProcess as oop
from vdsm.storage import qemuimg
from vdsm.storage import resourceManager as rm
from vdsm.storage import sd
from vdsm.storage import securable
from vdsm.storage import sp
from vdsm.storage import storageServer
from vdsm.storage import taskManager
from vdsm.storage import transientdisk
from vdsm.storage import validators
from vdsm.storage.constants import STORAGE
from vdsm.storage.sdc import sdCache
from vdsm.storage.spbackends import MAX_POOL_DESCRIPTION_SIZE
from vdsm.storage.spbackends import StoragePoolDiskBackend
from vdsm.storage.spbackends import StoragePoolMemoryBackend

from vdsm.storage.sdm.api import (
    amend_volume,
    add_bitmap,
    copy_data,
    clear_bitmaps,
    merge as api_merge,
    move_device,
    reduce_domain,
    remove_bitmap,
    sparsify_volume,
    update_volume
)

GUID = "guid"
NAME = "name"
UUID = "uuid"
TYPE = "type"
INITIALIZED = "initialized"
CAPACITY = "capacity"

QEMU_READABLE_TIMEOUT = 30

HSM_DOM_MON_LOCK = "HsmDomainMonitorLock"

# a host is being assigned with a host id in a storage pool when it's
# connected to a pool.
# Some verbs can be executed by hosts that aren't connected to a pool
# and to be executed on domain that isn't part of the pool - therefore
# we can't rely on having a usable and meaningful host id for the operation.
# using 1 as the host id is good enough as those verbs should be executed
# only on storage domains that aren't being accessed by any other host
# - therefore this id shouldn't be in use.
DISCONNECTED_HOST_ID = 1


def public(f):
    logged = api.logged("vdsm.api")
    return dispatcher.exported(logged(f))


class HSM(object):
    """
    This is the HSM class. It controls all the stuff relate to the Host.
    Further more it doesn't change any pool metadata.

    .. attribute:: tasksDir

        A string containing the path of the directory where backups of tasks a
        saved on the disk.
    """
    _pool = sp.DisconnectedPool()
    log = logging.getLogger('storage.hsm')

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
        :exc:`storage.exception.StorageDomainTypeNotBackup` exception
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

        :raises: :exc:`storage.exception.StorageDomainAlreadyExists` exception
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

    @classmethod
    def getPool(cls, spUUID):
        if cls._pool.is_connected() and cls._pool.spUUID == spUUID:
            return cls._pool

        # Calling when pool is not connected or with wrong pool id is client
        # error.
        raise exception.expected(se.StoragePoolUnknown(spUUID))

    @classmethod
    def setPool(cls, pool):
        cls._pool = pool

    def __init__(self):
        """
        The HSM Constructor

        :param defExcFunc: The function that will set the default exception
                           for this thread
        :type defExcFun: function
        """
        self._start_time = monotonic_time()
        self._ready = False
        self.log.info("START HSM init")
        rm.registerNamespace(STORAGE, rm.SimpleResourceFactory())
        self.taskMng = taskManager.TaskManager()

        self.log.info("Creating data-center mount directory %r",
                      sc.REPO_MOUNT_DIR)
        fileUtils.createdir(sc.REPO_MOUNT_DIR)
        storageServer.MountConnection.setLocalPathBase(sc.REPO_MOUNT_DIR)
        storageServer.LocalDirectoryConnection.setLocalPathBase(
            sc.REPO_MOUNT_DIR)

        sp.StoragePool.cleanupMasterMount()
        self.__releaseLocks()

        self._preparedVolumes = defaultdict(list)

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

        monitorInterval = config.getint('irs', 'sd_health_check_delay')
        self.mpathhealth_monitor = mpathhealth.Monitor(monitorInterval)
        self.mpathhealth_monitor.start()

        def storageRefresh():
            sdCache.refreshStorage()
            lvm.bootstrap(skiplvs=blockSD.SPECIAL_LVS_V4)
            self._ready = True
            self.log.info("FINISH HSM init succeeded in %.2f seconds",
                          monotonic_time() - self._start_time)

        storageRefreshThread = concurrent.thread(storageRefresh,
                                                 name="hsm/init",
                                                 log=self.log)
        storageRefreshThread.start()

        self.domainMonitor = monitor.DomainMonitor(monitorInterval)

    @property
    def ready(self):
        return self._ready

    @public
    def registerDomainStateChangeCallback(self, callbackFunc):
        """
        Register a state change callback function with the domain monitor.
        """
        self.domainMonitor.onDomainStateChange.register(callbackFunc)

    def _hsmSchedule(self, name, func, *args):
        self.taskMng.scheduleJob("hsm", None, vars.task, name, func, *args)

    def __cleanStorageRepository(self):
        """
        Cleanup the storage repository leftovers
        """

        self.log.debug("Started cleaning storage "
                       "repository at '%s'", sc.REPO_DATA_CENTER)

        mountList = []
        KEEP_PATTERNS = [
            self.tasksDir,
            os.path.join(self.tasksDir, "*"), sc.REPO_MOUNT_DIR,
        ]

        def should_keep(path):
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
            return any(fnmatch(fullpath, x) for x in KEEP_PATTERNS)

        # Add mounted folders to mountlist
        for mnt in mount.iterMounts():
            mountPoint = os.path.abspath(mnt.fs_file)
            if mountPoint.startswith(sc.REPO_DATA_CENTER):
                mountList.append(mountPoint)

        self.log.debug("Kept patterns: %s", KEEP_PATTERNS)
        self.log.debug("Mount list: %s", mountList)

        self.log.debug("Cleaning leftovers")
        rmDirList = []

        # We can't list files from top to bottom because the process
        # would descend into mountpoints and an unreachable NFS storage
        # could freeze the vdsm startup. Since we will ignore files in
        # mounts anyway using out of process file operations is useless.
        # We just clean all directories before removing them from the
        # innermost to the outermost.
        for base, dirs, files in misc.walk(sc.REPO_DATA_CENTER,
                                           skip=mountList):
            for directory in dirs:
                fullPath = os.path.join(base, directory)

                if should_keep(fullPath):
                    dirs.remove(directory)
                else:
                    rmDirList.insert(0, os.path.join(base, fullPath))

            for fname in files:
                fullPath = os.path.join(base, fname)

                if should_keep(fullPath):
                    continue
                self.log.info("Unlinking file %r", fullPath)
                try:
                    os.unlink(os.path.join(base, fullPath))
                except Exception:
                    self.log.warn("Cold not delete file "
                                  "'%s'", fullPath, exc_info=True)

        for directory in rmDirList:
            try:
                # os.walk() can see a link to a directory as a directory
                if os.path.islink(directory):
                    self.log.info("Unlinking symlink %r", directory)
                    os.unlink(directory)
                else:
                    self.log.info("Removing directory %r", directory)
                    os.rmdir(directory)
            except Exception:
                self.log.warn("Cold not delete directory "
                              "'%s'", directory, exc_info=True)

        self.log.debug("Finished cleaning storage "
                       "repository at '%s'", sc.REPO_DATA_CENTER)

    @public
    def getConnectedStoragePoolsList(self):
        """
        Get a list of all the connected storage pools.
        """
        vars.task.setDefaultException(se.StoragePoolActionError())
        pools = [self._pool.spUUID] if self._pool.is_connected() else []
        return dict(poollist=pools)

    @public
    def spmStart(self, spUUID, prevID, prevLVER,
                 maxHostID=clusterlock.MAX_HOST_ID, domVersion=None):
        """
        Starts an SPM.

        :param spUUID: The storage pool you want managed.
        :type spUUID: UUID
        :param prevID: The previous ID of the SPM that managed this pool.
        :type prevID: int
        :param prevLVER: The previous version of the pool that was managed by
                         the SPM.
        :type prevLVER: int
        :param maxHostID: The maximum Host ID in the cluster.
        :type maxHostID: int

        :returns: The UUID of the started task.
        :rtype: UUID
        """

        vars.task.setDefaultException(se.SpmStartError(
            "spUUID=%s, prevID=%s, prevLVER=%s, maxHostID=%s, domVersion=%s"
            % (spUUID, prevID, prevLVER, maxHostID, domVersion)))

        if domVersion is not None:
            domVersion = int(domVersion)
            sd.StorageDomain.validate_version(domVersion)

        # We validate SPM status twice - once before taking the lock, so we can
        # return immediately if the SPM was already started, and once after
        # taking the lock, in case the SPM was stopped while we were waiting
        # for the lock.

        # Calling on the SPM is client error.
        try:
            self.getPool(spUUID).validateNotSPM()
        except se.IsSpm as e:
            raise exception.expected(e)

        vars.task.getExclusiveLock(STORAGE, spUUID)
        pool = self.getPool(spUUID)
        # We should actually just return true if we are SPM after lock,
        # but seeing as it would break the API with Engine,
        # it's easiest to fail.

        # Calling on the SPM is client error.
        try:
            pool.validateNotSPM()
        except se.IsSpm as e:
            raise exception.expected(e)

        self._hsmSchedule("spmStart", pool.startSpm, prevID, prevLVER,
                          maxHostID, domVersion)

    @public
    def spmStop(self, spUUID):
        """
        Stops the SPM functionality.

        :param spUUID: The UUID of the storage pool you want to
                       stop it manager.
        :type spUUID: UUID

        :raises: :exc:`storage.exception.TaskInProgress`
                 if there are tasks running for this pool.

        """
        vars.task.setDefaultException(se.SpmStopError(spUUID))
        vars.task.getExclusiveLock(STORAGE, spUUID)

        pool = self.getPool(spUUID)
        pool.stopSpm()

    @staticmethod
    def _getSpmStatusInfo(pool):
        return dict(
            zip(('spmStatus', 'spmLver', 'spmId'),
                (pool.spmRole,) + pool.getSpmStatus()))

    @public
    def getSpmStatus(self, spUUID):
        pool = self.getPool(spUUID)
        try:
            status = self._getSpmStatusInfo(pool)
        except (se.LogicalVolumeRefreshError, IOError):
            # This happens when we cannot read the MD LV
            self.log.error("Can't read LV based metadata", exc_info=True)
            raise se.StorageDomainMasterError("Can't read LV based metadata")
        except se.InspectNotSupportedError:
            self.log.error("Inspect spm status isn't supported by "
                           "the current cluster lock")
            raise
        except se.StorageException as e:
            self.log.error("MD read error: %s", str(e), exc_info=True)
            raise se.StorageDomainMasterError("MD read error")
        except (KeyError, ValueError):
            self.log.error("Non existent or invalid MD key", exc_info=True)
            raise se.StorageDomainMasterError("Version or spm id invalid")

        return dict(spm_st=status)

    @public
    def extendVolume(self, sdUUID, spUUID, imgUUID, volumeUUID, size):
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
        :param size: Target volume size in bytes (desired final size, not by
                     how much to increase)
        :type size: number (anything parsable by int(size))
        """
        vars.task.setDefaultException(
            se.VolumeExtendingError(
                "spUUID=%s, sdUUID=%s, volumeUUID=%s, size=%s" %
                (spUUID, sdUUID, volumeUUID, size)))
        size = misc.validateN(size, "size")
        # ExtendVolume expects size in MiB.
        size_mb = utils.round(size, MiB) // MiB

        pool = self.getPool(spUUID)
        # TODO: extendVolume should use bytes, not MiB.
        #  When we moved from sectors to bytes we left the code using MiB.
        #  But we should really convert all code to use bytes.
        #  Using MiB for lvm is not correct anyway since
        #  lvm default extent size is 4 MiB, and we use extent size of 128 MiB.
        pool.extendVolume(sdUUID, volumeUUID, size_mb)

    @public
    def reduceVolume(self, spUUID, sdUUID, imgUUID, volUUID,
                     allowActive=False):
        """
        Reduce the volume size to optimal.

        Arguments:
            spUUID (UUID) - storage pool UUID
            sdUUID (UUID) - storage domain UUID
            imgUUID (UUID) - image UUID
            volUUID (UUID) - volume UUID
            allowActive (boolean) - indicates whether the volume is active
        """
        msg = "spUUID=%s, sdUUID=%s, imgUUID=%s, volUUID=%s, " \
              "allowActive=%s" % \
              (spUUID, sdUUID, imgUUID, volUUID, allowActive)
        vars.task.setDefaultException(se.StorageException(msg))
        pool = self.getPool(spUUID)
        sdCache.produce(sdUUID)
        vars.task.getSharedLock(STORAGE, sdUUID)
        self._spmSchedule(spUUID, "reduceVolume", pool.reduceVolume,
                          sdUUID, imgUUID, volUUID, allowActive)

    @public
    def extendVolumeSize(self, spUUID, sdUUID, imgUUID, volUUID, newSize):
        pool = self.getPool(spUUID)
        new_capacity = misc.validateN(newSize, "newSize")
        new_capacity = utils.round(new_capacity, sc.BLOCK_SIZE_4K)
        vars.task.getSharedLock(STORAGE, sdUUID)
        self._spmSchedule(
            spUUID, "extendVolumeSize", pool.extendVolumeSize, sdUUID,
            imgUUID, volUUID, new_capacity)

    @public
    def updateVolumeSize(self, spUUID, sdUUID, imgUUID, volUUID, newSize):
        """
        Update the volume size with the given newSize (in bytes).

        This synchronous method is intended to be used only with COW volumes
        where the size can be updated simply changing the qcow2 header.
        """
        newSizeBytes = int(newSize)
        domain = sdCache.produce(sdUUID=sdUUID)
        volToExtend = domain.produceVolume(imgUUID, volUUID)
        volPath = volToExtend.getVolumePath()
        volFormat = volToExtend.getFormat()

        if not volToExtend.isLeaf():
            raise se.VolumeNonWritable(volUUID)

        if volFormat != sc.COW_FORMAT:
            # This method is used only with COW volumes (see docstring),
            # for RAW volumes we just return the volume size.
            return dict(size=str(volToExtend.getVolumeSize()))

        qemuImgFormat = sc.fmt2str(sc.COW_FORMAT)

        volToExtend.prepare()
        try:
            imgInfo = qemuimg.info(volPath, qemuImgFormat)
            if imgInfo['virtual-size'] > newSizeBytes:
                self.log.error(
                    "volume %s size %s is larger than the size requested "
                    "for the extension %s", volUUID, imgInfo['virtual-size'],
                    newSizeBytes)
                raise se.VolumeResizeValueError(str(newSizeBytes))
            # Uncommit the current size
            volToExtend.setCapacity(0)
            qemuimg.resize(volPath, newSizeBytes, qemuImgFormat)
            virtual_size = qemuimg.info(volPath,
                                        qemuImgFormat)['virtual-size']
        finally:
            volToExtend.teardown(sdUUID, volUUID)

        volToExtend.setCapacity(virtual_size)

        return dict(size=str(virtual_size))

    @public
    def extendStorageDomain(self, sdUUID, spUUID, guids, force=False):
        """
        Extends a VG. ?

        .. note::
            Currently the vg must be a storage domain.

        :param sdUUID: The UUID of the storage domain that owns the VG.
        :type sdUUID: UUID
        :param spUUID: The UUID of the storage pool that owns the VG.
        :type spUUID: UUID
        :param guids: The list of device guids you want to extend the VG to.
        :type guids: list of device guids. ``[guid1, guid2]``.
        """
        vars.task.setDefaultException(
            se.StorageDomainActionError(
                "sdUUID=%s, devlist=%s" % (sdUUID, guids)))

        vars.task.getSharedLock(STORAGE, sdUUID)
        # We need to let the domain to extend itself
        pool = self.getPool(spUUID)
        dmDevs = tuple(os.path.join(devicemapper.DMPATH_PREFIX, guid) for guid
                       in guids)
        pool.extendSD(sdUUID, dmDevs, force)

    @public
    def resizePV(self, sdUUID, spUUID, guid):
        """
        Calls pvresize with specified pv guid
        and returns the size after the resize

        :param sdUUID: The UUID of the storage domain that owns the PV.
        :type sdUUID: UUID
        :param spUUID: The UUID of the storage pool that owns the PV.
        :type spUUID: UUID
        :param guid: A block device GUID
        :type guid: str
        :returns: dictionary with one item :size
        :rtype: dict
        """
        vars.task.setDefaultException(
            se.StorageDomainActionError(
                "sdUUID=%s, PV=%s" % (sdUUID, guid)))

        vars.task.getSharedLock(STORAGE, sdUUID)
        pool = self.getPool(spUUID)
        pool.resizePV(sdUUID, guid)

        pv = lvm.getPV(guid)
        return dict(size=str(pv.size))

    def _detachStorageDomainFromOldPools(self, sdUUID):
        dom = sdCache.produce(sdUUID=sdUUID)
        try:
            host_id = self._pool.id
        except se.StoragePoolNotConnected:
            host_id = dom.getReservedId()
            self.log.warn(
                "Host not connected to pool, using reserved host id: %d",
                host_id)
        dom.acquireHostId(host_id)
        try:
            dom.acquireClusterLock(host_id)
            try:
                for domPoolUUID in dom.getPools():
                    dom.detach(domPoolUUID)
            finally:
                dom.releaseClusterLock()
        finally:
            dom.releaseHostId(host_id)

    @public
    def forcedDetachStorageDomain(self, sdUUID, spUUID):
        """Forced detach a storage domain from a storage pool.
           This removes the storage domain entry in the storage pool meta-data
           and leaves the storage domain in 'unattached' status.
           This action can only be performed on regular (i.e. non master)
           domains.
        """
        vars.task.setDefaultException(
            se.StorageDomainActionError(
                "sdUUID=%s, spUUID=%s" % (sdUUID, spUUID)))

        if spUUID == sd.BLANK_UUID:
            self._detachStorageDomainFromOldPools(sdUUID)
        else:
            vars.task.getExclusiveLock(STORAGE, spUUID)
            pool = self.getPool(spUUID)
            if sdUUID == pool.masterDomain.sdUUID:
                raise se.CannotDetachMasterStorageDomain(sdUUID)
            pool.forcedDetachSD(sdUUID)

    @public
    def detachStorageDomain(self, sdUUID, spUUID, msdUUID=None,
                            masterVersion=None):
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
        """
        vars.task.setDefaultException(
            se.StorageDomainActionError(
                "sdUUID=%s, spUUID=%s, msdUUID=%s, masterVersion=%s" %
                (sdUUID, spUUID, msdUUID, masterVersion)))

        vars.task.getExclusiveLock(STORAGE, spUUID)
        vars.task.getExclusiveLock(STORAGE, sdUUID)
        pool = self.getPool(spUUID)
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
        newSize = misc.validateN(newSize, "newSize")
        newSize_mb = utils.round(newSize, MiB) // MiB
        try:
            pool = self.getPool(spUUID)
        except se.StoragePoolUnknown:
            pass
        else:
            if pool.hsmMailer:
                pool.hsmMailer.sendExtendMsg(volDict, newSize_mb, callbackFunc)

    def _spmSchedule(self, spUUID, name, func, *args):
        pool = self.getPool(spUUID)

        # Calling when the host is not the SPM is a caller error.
        try:
            pool.validateSPM()
        except se.SpmStatusError as e:
            raise exception.expected(e)

        self.taskMng.scheduleJob("spm", pool.tasksDir, vars.task,
                                 name, func, *args)

    @public
    def createStoragePool(
            self, spUUID, poolName, masterDom, domList, masterVersion,
            lockRenewalIntervalSec=None, leaseTimeSec=None,
            ioOpTimeoutSec=None, leaseRetries=None):
        """
        Create new storage pool with single/multiple image data domain.
        The command will create new storage pool meta-data attach each
        storage domain to that storage pool.
        At least one data (images) domain must be provided

        .. note::
            The master domain needs to be also stated in the domain list

        :param spUUID: The UUID that the new storage pool will have
        :type spUUID: UUID
        :param poolName: The human readable name of the new pool.
        :type poolName: str
        :param masterDom: The UUID of the master storage domain that
                          contains the pool's metadata.
        :type masterDom: UUID
        :param domList: A list of all the UUIDs of the storage domains managed
                        by this storage pool.
        :type domList: UUID list
        :param masterVersion: The master version of the storage pool meta data.
        :type masterVersion: uint
        :param lockRenewalIntervalSec: ?
        :param leaseTimeSec: ?
        :param ioOpTimeoutSec: The default timeout for IO operations
                               in seconds.?
        :type ioOpTimroutSec: uint
        :param leaseRetries: ?

        :returns: The newly created storage pool object.
        :rtype: :class:`sp.StoragePool`

        :raises: an :exc:`storage.exception.InvalidParameterException` if the
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

        if len(poolName) > MAX_POOL_DESCRIPTION_SIZE:
            raise se.StoragePoolDescriptionTooLongError()

        vars.task.getExclusiveLock(STORAGE, spUUID)
        for dom in sorted(domList):
            vars.task.getExclusiveLock(STORAGE, dom)

        pool = sp.StoragePool(spUUID, self.domainMonitor, self.taskMng)
        pool.setBackend(StoragePoolDiskBackend(pool))

        return pool.create(poolName, masterDom, domList, masterVersion,
                           leaseParams)

    @public
    def connectStoragePool(self, spUUID, hostID, msdUUID, masterVersion,
                           domainsMap=None):
        """
        Connect a Host to a specific storage pool.

        :param spUUID: The UUID of the storage pool you want to connect to.
        :type spUUID: UUID
        :param hostID: The hostID to be used for clustered locking.
        :type hostID: int
        :param msdUUID: The UUID for the pool's master domain.
        :type msdUUID: UUID
        :param masterVersion: The expected master version. Used for validation.
        :type masterVersion: int

        :returns: :keyword:`True` if connection was successful.
        :rtype: bool

        :raises: :exc:`storage.exception.ConnotConnectMultiplePools` when
                 storage pool is not connected to the system.
        """
        vars.task.setDefaultException(
            se.StoragePoolConnectionError(
                "spUUID=%s, msdUUID=%s, masterVersion=%s, hostID=%s, "
                "domainsMap=%s" %
                (spUUID, msdUUID, masterVersion, hostID, domainsMap)))
        with rm.acquireResource(STORAGE, HSM_DOM_MON_LOCK, rm.EXCLUSIVE):
            return self._connectStoragePool(
                spUUID, hostID, msdUUID, masterVersion, domainsMap)

    @staticmethod
    def _updateStoragePool(pool, hostId, msdUUID, masterVersion, domainsMap):
        if hostId != pool.id:
            raise se.StoragePoolConnected(
                "hostId=%s, newHostId=%s" % (pool.id, hostId))

        if domainsMap is None:
            if not isinstance(pool.getBackend(), StoragePoolDiskBackend):
                raise se.StoragePoolConnected('Cannot downgrade pool backend')
        else:
            if isinstance(pool.getBackend(), StoragePoolMemoryBackend):
                pool.getBackend().updateVersionAndDomains(
                    masterVersion, domainsMap)
            else:
                # Live pool backend upgrade
                pool.setBackend(
                    StoragePoolMemoryBackend(pool, masterVersion, domainsMap))

        pool.refresh(msdUUID, masterVersion)

    def _connectStoragePool(
            self, spUUID, hostID, msdUUID, masterVersion, domainsMap=None):
        misc.validateUUID(spUUID, 'spUUID')
        if self._pool.is_connected() and self._pool.spUUID != spUUID:
            raise se.CannotConnectMultiplePools(self._pool.spUUID)

        try:
            self.getPool(spUUID)
        except se.StoragePoolUnknown:
            pass  # pool not connected yet
        else:
            with rm.acquireResource(STORAGE, spUUID, rm.SHARED):
                # FIXME: this breaks in case of a race as it assumes that the
                # pool is still available. At the moment we maintain this
                # behavior as it's inherited from the previous implementation
                # but the problem must be addressed (possibly improving the
                # entire locking pattern used in this method).
                self._updateStoragePool(self.getPool(spUUID), hostID, msdUUID,
                                        masterVersion, domainsMap)
                return True

        with rm.acquireResource(STORAGE, spUUID, rm.EXCLUSIVE):
            try:
                pool = self.getPool(spUUID)
            except se.StoragePoolUnknown:
                pass  # pool not connected yet
            else:
                self._updateStoragePool(pool, hostID, msdUUID, masterVersion,
                                        domainsMap)
                return True

            pool = sp.StoragePool(spUUID, self.domainMonitor, self.taskMng)
            pool.backend = StoragePoolDiskBackend(pool)

            if domainsMap is None:
                pool.setBackend(StoragePoolDiskBackend(pool))
            else:
                pool.setBackend(
                    StoragePoolMemoryBackend(pool, masterVersion, domainsMap))

            res = pool.connect(hostID, msdUUID, masterVersion)
            if res:
                self.setPool(pool)
            return res

    @public
    def disconnectStoragePool(self, spUUID, hostID, remove=False):
        """
        Disconnect a Host from a specific storage pool.

        :param spUUID: The UUID of the storage pool you want to disconnect.
        :type spUUID: UUID
        :param hostID: The ID of the host you want to disconnect the pool from.
        :type hostID: int
        :param remove: ?
        :type remove: bool

        :returns: :keyword:`True` if disconnection was successful.
        :rtype: bool

        .. note::
            if storage pool is not connected or doesn't exist the operation
            will log and exit silently.
        """
        vars.task.setDefaultException(
            se.StoragePoolDisconnectionError(
                "spUUID=%s, hostID=%s" % (spUUID, hostID)))
        misc.validateN(hostID, 'hostID')
        # already disconnected/or pool is just unknown - return OK
        try:
            pool = self.getPool(spUUID)
        except se.StoragePoolUnknown:
            self.log.warning("Already disconnected from %r", spUUID)
            return

        # Calling on the SPM is client error.
        try:
            self.getPool(spUUID).validateNotSPM()
        except se.IsSpm as e:
            raise exception.expected(e)

        vars.task.getExclusiveLock(STORAGE, spUUID)
        pool = self.getPool(spUUID)

        return self._disconnectPool(pool, hostID, remove)

    def _disconnectPool(self, pool, hostID, remove):
        # Calling on the SPM is client error.
        try:
            pool.validateNotSPM()
        except se.IsSpm as e:
            raise exception.expected(e)

        with rm.acquireResource(STORAGE, HSM_DOM_MON_LOCK, rm.EXCLUSIVE):
            res = pool.disconnect()
            self.setPool(sp.DisconnectedPool())
        return res

    @public
    def destroyStoragePool(self, spUUID, hostID):
        """
        Destroy a storage pool.
        The command will detach all inactive domains from the pool
        and delete the pool with all its links.

        :param spUUID: The UUID of the storage pool you want to destroy.
        :type spUUID: UUID
        :param hostID: The ID of the host managing this storage pool. ?
        :type hostID: int
        """
        vars.task.setDefaultException(
            se.StoragePoolDestroyingError(
                "spUUID=%s, hostID=%s" % (spUUID, hostID)))
        self.log.info("spUUID=%s", spUUID)

        pool = self.getPool(spUUID)
        if not pool.id == hostID:
            raise se.HostIdMismatch(spUUID)

        vars.task.getExclusiveLock(STORAGE, pool.spUUID)
        # Find out domain list from the pool metadata
        domList = sorted(pool.getDomains())
        for sdUUID in domList:
            vars.task.getExclusiveLock(STORAGE, sdUUID)

        pool.detachAllDomains()
        return self._disconnectPool(pool, hostID, remove=True)

    @public
    def attachStorageDomain(self, sdUUID, spUUID):
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
        """
        vars.task.setDefaultException(
            se.StorageDomainActionError(
                "sdUUID=%s, spUUID=%s" % (sdUUID, spUUID)))

        vars.task.getExclusiveLock(STORAGE, spUUID)
        vars.task.getExclusiveLock(STORAGE, sdUUID)
        pool = self.getPool(spUUID)
        pool.attachSD(sdUUID)

    @public
    def deactivateStorageDomain(self, sdUUID, spUUID, msdUUID, masterVersion):
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
        pool.deactivateSD(sdUUID, msdUUID, masterVersion)

    @public
    def switchMaster(self, storagepoolID, oldMasterUUID, newMasterUUID,
                     masterVersion):
        """
        Switches the master domain from oldMasterUUID to newMasterUUID.
        The old master domain will have a "Regular" role after this operation,
        while the new master domain will have the "Master" role.
        Can be called only on the SPM host.

        Arguments:
            storagepoolID (str): The storage pool that contains the storage
            domains being switched.
            oldMasterUUID (str): The current master storage domain UUID.
            newMasterUUID (str): The new master storage domain UUID.
            masterVersion (int): The version of the new master storage domain.
        """
        vars.task.getExclusiveLock(STORAGE, storagepoolID)
        pool = self.getPool(storagepoolID)
        self._spmSchedule(storagepoolID, "switchMaster", pool.switchMaster,
                          oldMasterUUID, newMasterUUID, masterVersion)

    @public
    def activateStorageDomain(self, sdUUID, spUUID):
        """
        Activates a storage domain that is already a member in a storage pool.

        :param sdUUID: The UUID of the storage domain that you want to
                       activate.
        :type sdUUID: UUID
        :param spUUID: The UUID of the storage pool that contains the storage
                       domain being activated.
        :type spUUID: UUID
        """
        vars.task.setDefaultException(
            se.StorageDomainActionError(
                "sdUUID=%s, spUUID=%s" % (sdUUID, spUUID)))

        vars.task.getExclusiveLock(STORAGE, spUUID)
        vars.task.getExclusiveLock(STORAGE, sdUUID)
        pool = self.getPool(spUUID)
        pool.activateSD(sdUUID)

    @deprecated
    @public
    def setStoragePoolDescription(self, spUUID, description):
        """
        Deprecated, as the storage pool's metadata is no longer persisted.
        TODO: Remove when the support for 3.5 clusters is dropped.
        """

    @deprecated
    @public
    def setVolumeDescription(self, sdUUID, spUUID, imgUUID, volUUID,
                             description):
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
        pool.setVolumeDescription(sdUUID, imgUUID, volUUID, description)

    @deprecated
    @public
    def setVolumeLegality(self, sdUUID, spUUID, imgUUID, volUUID, legality):
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
        pool.setVolumeLegality(sdUUID, imgUUID, volUUID, legality)

    @public
    def updateVM(self, spUUID, vmList, sdUUID=None):
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
        """
        vars.task.getSharedLock(STORAGE, spUUID)
        pool = self.getPool(spUUID)
        if not sdUUID or sdUUID == sd.BLANK_UUID:
            sdUUID = pool.masterDomain.sdUUID

        vars.task.getExclusiveLock(STORAGE, "vms_" + sdUUID)
        pool.updateVM(vmList=vmList, sdUUID=sdUUID)

    @public
    def removeVM(self, spUUID, vmUUID, sdUUID=None):
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
        """
        vars.task.getSharedLock(STORAGE, spUUID)
        pool = self.getPool(spUUID)
        if not sdUUID or sdUUID == sd.BLANK_UUID:
            sdUUID = pool.masterDomain.sdUUID

        vars.task.getSharedLock(STORAGE, "vms_" + sdUUID)
        vars.task.getExclusiveLock(STORAGE, "vms_%s_%s" % (vmUUID, sdUUID))
        pool.removeVM(vmUUID=vmUUID, sdUUID=sdUUID)

    @public
    def getVmsList(self, spUUID, sdUUID=None):
        """
        Gets a list of VMs from the pool.
        If 'sdUUID' is given and it's a backup domain the function will get
        the list of VMs from it

        :param spUUID: The UUID of the storage pool that you want to query.
        :type spUUID: UUID
        :param sdUUID: The UUID of the backup domain that the you want to
                       query or :keyword:`None`.
        :type sdUUID: UUID
        """
        pool = self.getPool(spUUID)
        if not sdUUID or sdUUID == sd.BLANK_UUID:
            sdUUID = pool.masterDomain.sdUUID
        vars.task.getSharedLock(STORAGE, sdUUID)
        vms = pool.getVmsList(sdUUID)
        return dict(vmlist=vms)

    @public
    def getVmsInfo(self, spUUID, sdUUID, vmList=None):
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
        """
        pool = self.getPool(spUUID)
        if sdUUID and sdUUID != sd.BLANK_UUID:
            # Only backup domains are allowed in this path
            self.validateBackupDom(sdUUID)
        else:
            sdUUID = pool.masterDomain.sdUUID
        vars.task.getSharedLock(STORAGE, sdUUID)
        vms = pool.getVmsInfo(sdUUID, vmList)
        return dict(vmlist=vms)

    @public
    def createVolume(self, sdUUID, spUUID, imgUUID, size, volFormat,
                     preallocate, diskType, volUUID, desc,
                     srcImgUUID=sc.BLANK_UUID,
                     srcVolUUID=sc.BLANK_UUID,
                     initialSize=None, addBitmaps=False,
                     legal=True):
        """
        Create a new volume
            Function Type: SPM
            Parameters:
            Return Value:
        """
        argsStr = ("sdUUID=%s, spUUID=%s, imgUUID=%s, size=%s, volFormat=%s, "
                   "preallocate=%s, diskType=%s, volUUID=%s, desc=%s, "
                   "srcImgUUID=%s, srcVolUUID=%s, initialSize=%s" %
                   (sdUUID, spUUID, imgUUID, size, volFormat, preallocate,
                    diskType, volUUID, desc, srcImgUUID, srcVolUUID,
                    initialSize))
        vars.task.setDefaultException(se.VolumeCreationError(argsStr))
        # Validates that the pool is connected. WHY?
        pool = self.getPool(spUUID)
        dom = sdCache.produce(sdUUID=sdUUID)
        misc.validateUUID(imgUUID, 'imgUUID')
        misc.validateUUID(volUUID, 'volUUID')
        capacity = misc.validateSize(size, "size")
        initial_size = None
        if initialSize is not None:
            initial_size = misc.validateSize(initialSize, "initialSize")

        if srcImgUUID:
            misc.validateUUID(srcImgUUID, 'srcImgUUID')
        if srcVolUUID:
            misc.validateUUID(srcVolUUID, 'srcVolUUID')
        # Validate volume type and format
        dom.validateCreateVolumeParams(
            volFormat, srcVolUUID, diskType=diskType, preallocate=preallocate,
            add_bitmaps=addBitmaps)

        vars.task.getSharedLock(STORAGE, sdUUID)
        self._spmSchedule(spUUID, "createVolume", pool.createVolume, sdUUID,
                          imgUUID, capacity, volFormat, preallocate, diskType,
                          volUUID, desc, srcImgUUID, srcVolUUID, initial_size,
                          addBitmaps, legal)

    @public
    def deleteVolume(self, sdUUID, spUUID, imgUUID, volumes, postZero=False,
                     force=False, discard=False):
        """
        Delete a volume
        """
        argsStr = "sdUUID=%s, spUUID=%s, imgUUID=%s, volumes=%s, " \
                  "postZero=%s, force=%s, discard=%s" %\
                  (sdUUID, spUUID, imgUUID, volumes, postZero, force, discard)
        vars.task.setDefaultException(se.CannotDeleteVolume(argsStr))
        # Validates that the pool is connected. WHY?
        pool = self.getPool(spUUID)
        misc.validateUUID(imgUUID, 'imgUUID')

        vars.task.getSharedLock(STORAGE, sdUUID)
        self._spmSchedule(spUUID, "deleteVolume", pool.deleteVolume, sdUUID,
                          imgUUID, volumes, misc.parseBool(postZero),
                          misc.parseBool(force), discard)

    @public
    def deleteImage(self, sdUUID, spUUID, imgUUID, postZero=False,
                    force=False, discard=False):
        """
        Delete Image folder with all volumes

        force parameter is deprecated and not evaluated.
        """
        # vars.task.setDefaultException(se.ChangeMeError("%s" % args))
        pool = self.getPool(spUUID)
        dom = sdCache.produce(sdUUID=sdUUID)

        # Taking an exclusive lock on both imgUUID and sdUUID since
        # an image can exist on two SDs concurrently (e.g. during LSM flow);
        # hence, we need a unique identifier.
        vars.task.getExclusiveLock(STORAGE, "%s_%s" % (imgUUID, sdUUID))
        vars.task.getSharedLock(STORAGE, sdUUID)
        allVols = dom.getAllVolumes()
        volsByImg = sd.getVolsOfImage(allVols, imgUUID)
        if not volsByImg:
            self.log.error("Empty or not found image %s in SD %s. %s",
                           imgUUID, sdUUID, allVols)
            raise se.ImageDoesNotExistInSD(imgUUID, sdUUID)

        # on data domains, images should not be deleted if they are templates
        # being used by other images.
        fakeTUUID = None
        for k, v in six.iteritems(volsByImg):
            if len(v.imgs) > 1 and v.imgs[0] == imgUUID:
                if dom.isBackup():
                    fakeTUUID = k
                else:
                    raise se.CannotDeleteSharedVolume("Cannot delete shared "
                                                      "image %s. volImgs: %s" %
                                                      (imgUUID, volsByImg))
                break

        # zeroImage will delete zeroed volumes at the end.
        if misc.parseBool(postZero):
            # postZero implies block domain. Backup domains are always NFS
            # hence no need to create fake template if postZero is true.
            self._spmSchedule(spUUID, "zeroImage_%s" % imgUUID, dom.zeroImage,
                              sdUUID, imgUUID, volsByImg, discard)
        else:
            if fakeTUUID:
                tParams = dom.produceVolume(imgUUID, fakeTUUID).\
                    getVolumeParams()
            pool.deleteImage(dom, imgUUID, volsByImg)
            if fakeTUUID:
                img = image.Image(os.path.join(sc.REPO_DATA_CENTER, spUUID))
                img.createFakeTemplate(sdUUID=sdUUID, volParams=tParams)
            self._spmSchedule(spUUID, "purgeImage_%s" % imgUUID,
                              pool.purgeImage, sdUUID, imgUUID, volsByImg,
                              discard)

    @public
    def verify_untrusted_volume(self, spUUID, sdUUID, imgUUID, volUUID):
        dom = sdCache.produce(sdUUID=sdUUID).manifest
        vol = dom.produceVolume(imgUUID, volUUID)
        qemu_info = qemuimg.info(vol.getVolumePath(), trusted_image=False)

        meta_format = sc.fmt2str(vol.getFormat())
        qemu_format = qemu_info["format"]
        if meta_format != qemu_format:
            raise se.ImageVerificationError(
                "Volume's format specified by QEMU is %s, while the format "
                "specified in VDSM metadata is %s" %
                (qemu_format, meta_format))

        # NOTE: Volume size is in blocks.
        meta_size = vol.getCapacity()
        qemu_size = qemu_info["virtual-size"]
        if meta_size < qemu_size:
            raise se.ImageVerificationError(
                "Image virtual size %r is bigger than volume size %r"
                % (qemu_size, meta_size))
        elif meta_size > qemu_size:
            # Engine < 4.2.6 rounds up disk size to a multiple of 1G, creating
            # disk size that does not match the undelying image. We cannot
            # fail verification for this case.
            # See https://bugzilla.redhat.com/1608716.
            self.log.warning(
                "Image virtual size %r is smaller than volume size %r, image "
                "should be resized",
                qemu_size, meta_size)

        meta_parent = vol.getParent()
        qemu_parent = qemu_info.get("backing-filename", sc.BLANK_UUID)
        if meta_parent != qemu_parent:
            raise se.ImageVerificationError(
                "Image backing file %r does not match volume parent uuid %r"
                % (qemu_parent, meta_parent))

        if qemu_format == qemuimg.FORMAT.QCOW2:
            # Vdsm depends on qemu-img 2.3.0 or later which always reports
            # 'compat' for qcow2 volumes.
            qemu_compat = qemu_info["format-specific"]["data"]["compat"]
            if not dom.supports_qcow2_compat(qemu_compat):
                raise se.ImageVerificationError(
                    "qcow2 compat %r not supported on this domain" %
                    qemu_compat)

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
        for volName, imgsPar in six.iteritems(srcVolsImgs):
            if len(imgsPar.imgs) > 1:
                # This is the template. Should be only one.
                tName, tImgs = volName, imgsPar.imgs
                # Template self image is the 1st entry
                if imgUUID != tImgs[0] and tName not in dstAllVols:
                    self.log.error(
                        "img %s can't be moved to dom %s because template "
                        "%s is absent on it", imgUUID, dstDom.sdUUID, tName)
                    raise se.ImageDoesNotExistInSD(
                        imgUUID,
                        dstDom.sdUUID,
                        tmpImgUUID=tImgs[0],
                        tmpVolUUID=tName)
                elif imgUUID == tImgs[0] and not srcDom.isBackup():
                    raise se.MoveTemplateImageError(imgUUID)
                break

        return True

    @public
    def moveImage(self, spUUID, srcDomUUID, dstDomUUID, imgUUID, vmUUID,
                  op, postZero=False, force=False, discard=False):
        """
        Move/Copy image between storage domains within same storage pool
        """
        argsStr = ("spUUID=%s, srcDomUUID=%s, dstDomUUID=%s, imgUUID=%s, "
                   "vmUUID=%s, op=%s, force=%s, postZero=%s, force=%s,"
                   "discard=%s" %
                   (spUUID, srcDomUUID, dstDomUUID, imgUUID, vmUUID, op,
                    force, postZero, force, discard))
        vars.task.setDefaultException(se.MoveImageError("%s" % argsStr))
        if srcDomUUID == dstDomUUID:
            raise se.InvalidParameterException(
                "srcDom", "must be different from dstDom: %s" % argsStr)

        srcDom = sdCache.produce(sdUUID=srcDomUUID)
        dstDom = sdCache.produce(sdUUID=dstDomUUID)
        # Validates that the pool is connected. WHY?
        pool = self.getPool(spUUID)
        try:
            self.validateImageMove(srcDom, dstDom, imgUUID)
        except se.ImageDoesNotExistInSD as e:
            if not dstDom.isBackup():
                raise
            else:
                # Create an ad-hoc fake template only on a backup SD
                tmpVol = srcDom.produceVolume(e.tmpImgUUID, e.tmpVolUUID)
                img = image.Image(os.path.join(sc.REPO_DATA_CENTER, spUUID))
                img.createFakeTemplate(dstDom.sdUUID, tmpVol.getVolumeParams())

        domains = [srcDomUUID, dstDomUUID]
        domains.sort()

        for dom in domains:
            vars.task.getSharedLock(STORAGE, dom)

        self._spmSchedule(
            spUUID, "moveImage_%s" % imgUUID, pool.moveImage, srcDomUUID,
            dstDomUUID, imgUUID, vmUUID, op, misc.parseBool(postZero),
            misc.parseBool(force), discard)

    @public
    def cloneImageStructure(self, spUUID, sdUUID, imgUUID, dstSdUUID):
        """
        Clone an image structure (volume chain) to a destination domain within
        the same pool.
        """
        sdCache.produce(sdUUID=sdUUID)
        sdCache.produce(sdUUID=dstSdUUID)

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
        sdCache.produce(sdUUID=sdUUID)
        sdCache.produce(sdUUID=dstSdUUID)

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
        sdCache.produce(sdUUID)
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
        sdCache.produce(sdUUID)
        pool = self.getPool(spUUID)
        # NOTE: this could become an hsm task, in such case the LV extension
        # required to prepare the destination should go through the mailbox.
        self._spmSchedule(spUUID, "downloadImage", pool.downloadImage,
                          methodArgs, sdUUID, imgUUID, volUUID)

    @public
    def uploadImageToStream(self, methodArgs, callback, startEvent, spUUID,
                            sdUUID, imgUUID, volUUID=None):
        """
        Uploads an image to a stream.

        Warning: Internal use only.
        """
        sdCache.produce(sdUUID)
        pool = self.getPool(spUUID)
        # NOTE: this could become an hsm task
        self._spmSchedule(spUUID, "uploadImageToStream",
                          pool.uploadImageToStream, methodArgs, callback,
                          startEvent, sdUUID, imgUUID, volUUID)

    @public
    def downloadImageFromStream(self, methodArgs, callback, spUUID, sdUUID,
                                imgUUID, volUUID=None):
        """
        Download an image from a stream.

        Warning: Internal use only.
        """
        sdCache.produce(sdUUID)
        pool = self.getPool(spUUID)
        # NOTE: this could become an hsm task, in such case the LV extension
        # required to prepare the destination should go through the mailbox.
        self._spmSchedule(spUUID, "downloadImageFromStream",
                          pool.downloadImageFromStream, methodArgs, callback,
                          sdUUID, imgUUID, volUUID)

    @deprecated
    @public
    def copyImage(
            self, sdUUID, spUUID, vmUUID, srcImgUUID, srcVolUUID, dstImgUUID,
            dstVolUUID, description='', dstSdUUID=sd.BLANK_UUID,
            volType=sc.SHARED_VOL, volFormat=sc.UNKNOWN_VOL,
            preallocate=sc.UNKNOWN_VOL, postZero=False, force=False,
            discard=False):
        """
        Create new template/volume from VM.
        Do it by collapse and copy the whole chain (baseVolUUID->srcVolUUID)
        """
        argsStr = ("sdUUID=%s, spUUID=%s, vmUUID=%s, srcImgUUID=%s, "
                   "srcVolUUID=%s, dstImgUUID=%s, dstVolUUID=%s, "
                   "description=%s, dstSdUUID=%s, volType=%s, volFormat=%s, "
                   "preallocate=%s force=%s, postZero=%s, discard=%s" %
                   (sdUUID, spUUID, vmUUID, srcImgUUID, srcVolUUID,
                    dstImgUUID, dstVolUUID, description, dstSdUUID, volType,
                    volFormat, preallocate, force, postZero, discard))
        vars.task.setDefaultException(se.TemplateCreationError("%s" % argsStr))
        # Validate imgUUID in case of copy inside source domain itself
        if dstSdUUID in (sdUUID, sd.BLANK_UUID):
            if srcImgUUID == dstImgUUID:
                raise se.InvalidParameterException("dstImgUUID", dstImgUUID)
        pool = self.getPool(spUUID)
        sdCache.produce(sdUUID=sdUUID)

        # Avoid VM copy if one of its volume (including template if exists)
        # ILLEGAL/FAKE
        pool.validateVolumeChain(sdUUID, srcImgUUID)
        # Validate volume type and format
        if dstSdUUID != sd.BLANK_UUID:
            dom = dstSdUUID
        else:
            dom = sdUUID

        sdCache.produce(dom).validateCreateVolumeParams(
            volFormat, sc.BLANK_UUID, preallocate=preallocate)

        # If dstSdUUID defined, means we copy image to it
        domains = [sdUUID]
        if dstSdUUID not in [sdUUID, sd.BLANK_UUID]:
            sdCache.produce(sdUUID=dstSdUUID)
            domains.append(dstSdUUID)
            domains.sort()

        for dom in domains:
            vars.task.getSharedLock(STORAGE, dom)

        self._spmSchedule(
            spUUID, "copyImage_%s" % dstImgUUID, pool.copyImage, sdUUID,
            vmUUID, srcImgUUID, srcVolUUID, dstImgUUID, dstVolUUID,
            description, dstSdUUID, volType, volFormat, preallocate,
            misc.parseBool(postZero), misc.parseBool(force), discard)

    @public
    def imageSyncVolumeChain(self, sdUUID, imgUUID, volUUID, newChain):
        """
        Update storage metadata for an image chain after a live merge
        completes.  Since this is called from the HSM where the VM is running,
        we cannot modify the LVM tag that stores the parent UUID for block
        volumes.  In this case we update the chain in the metadata LV only.
        The LV tag will be fixed when the unlinked volume is deleted by an SPM.
        """
        argsStr = ("sdUUID=%s, imgUUID=%s, volUUID=%s, newChain=%s" %
                   (sdUUID, imgUUID, volUUID, newChain))
        vars.task.setDefaultException(se.StorageException("%s" % argsStr))
        sdDom = sdCache.produce(sdUUID=sdUUID)
        repoPath = os.path.join(sc.REPO_DATA_CENTER, sdDom.getPools()[0])

        imageResourcesNamespace = rm.getNamespace(sc.IMAGE_NAMESPACE, sdUUID)
        with rm.acquireResource(imageResourcesNamespace, imgUUID, rm.SHARED):
            image.Image(repoPath).syncVolumeChain(sdUUID, imgUUID, volUUID,
                                                  newChain)

    @public
    def reconcileVolumeChain(self, spUUID, sdUUID, imgUUID, leafVolUUID):
        """
        In some situations (such as when a live merge is interrupted), the
        vdsm volume chain could become out of sync with the actual chain as
        understood by qemu.  This API uses qemu-img to determine the correct
        chain and synchronizes vdsm metadata accordingly.  Returns the correct
        volume chain.  NOT for use on images of running VMs.
        """
        argsStr = ("spUUID=%s, sdUUID=%s, imgUUID=%s, leafVolUUID=%s" %
                   (spUUID, sdUUID, imgUUID, leafVolUUID))
        vars.task.setDefaultException(se.StorageException("%s" % argsStr))
        pool = self.getPool(spUUID)
        sdCache.produce(sdUUID=sdUUID)
        vars.task.getSharedLock(STORAGE, sdUUID)
        return pool.reconcileVolumeChain(sdUUID, imgUUID, leafVolUUID)

    @public
    def reconstructMaster(self, spUUID, poolName, masterDom, domDict,
                          masterVersion, lockRenewalIntervalSec,
                          leaseTimeSec, ioOpTimeoutSec, leaseRetries, hostId):
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
        :param lockRenewalIntervalSec: ?
        :param leaseTimeSec: ?
        :param ioOpTimeoutSec: The timeout of IO operations in seconds. ?
        :type ioOpTimeoutSec: int
        :param leaseRetries: ?
        :param hostId: The host id to be used during the reconstruct process.

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
            pool = sp.StoragePool(spUUID, self.domainMonitor, self.taskMng)
            pool.setBackend(StoragePoolDiskBackend(pool))
        else:
            raise se.StoragePoolConnected(spUUID)

        self.validateSdUUID(masterDom)

        misc.validateN(hostId, 'hostId')

        vars.task.getExclusiveLock(STORAGE, spUUID)

        for d, status in six.iteritems(domDict):
            misc.validateUUID(d)
            try:
                sd.validateSDStatus(status)
            except:
                domDict[d] = sd.validateSDDeprecatedStatus(status)

        return pool.reconstructMaster(hostId, poolName, masterDom, domDict,
                                      masterVersion, leaseParams)

    @public
    def getDeviceList(self, storageType=None, guids=(), checkStatus=True,
                      refresh=True):
        """
        List all Block Devices.

        :param storageType: Filter by storage type.
        :type storageType: Some enum?
        :param guids: List of device GUIDs to retrieve info.
        :type guids: list
        :param checkStatus: if true the status will be checked for the given
                            devices. This operation is an expensive operation
                            and should be used only with specific devices
                            using the guids argument.The default is True for
                            backward compatibility.
        :type checkStatus: bool
        :param refresh: if true the storage will be refreshed as part of
                        the operation.
        :type checkStatus: bool

        :returns: Dict containing a list of all the devices of the storage
                  type specified.
        :rtype: dict
        """
        vars.task.setDefaultException(se.BlockDeviceActionError())

        if checkStatus and not guids:
            # Engine stopped using this since 3.6, but there are other callers
            # in the field that use this.
            # See https://bugzilla.redhat.com/1426429#c11
            self.log.warning(
                "Calling Host.getDeviceList with checkStatus=True without "
                "specifying guids is very slow. It is recommended to use "
                "checkStatus=False when getting all devices.")

        devices = self._getDeviceList(storageType=storageType, guids=guids,
                                      checkStatus=checkStatus, refresh=refresh)
        return dict(devList=devices)

    def _getDeviceList(self, storageType=None, guids=(), checkStatus=True,
                       refresh=True):
        if refresh:
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
        pvs = {os.path.basename(pv.name): pv for pv in lvm.getAllPVs()}

        # FIXME: pathListIter() should not return empty records
        for dev in multipath.pathListIter(guids):
            if not typeFilter(dev):
                continue

            pv = pvs.get(dev.get('guid', ""))
            if pv is not None:
                pvuuid = pv.uuid
                pvsize = pv.size
                vguuid = pv.vg_uuid
            else:
                pvuuid = ""
                pvsize = ""
                vguuid = ""

            devInfo = {
                'GUID': dev.get("guid", ""),
                'capacity': dev.get("capacity", "0"),
                'devtype': dev.get("devtype", ""),
                'fwrev': dev.get("fwrev", ""),
                'logicalblocksize': dev.get("logicalblocksize", ""),
                'pathlist': dev.get("connections", []),
                'pathstatus': dev.get("paths", []),
                'physicalblocksize': dev.get("physicalblocksize", ""),
                'productID': dev.get("product", ""),
                'pvUUID': pvuuid,
                'pvsize': str(pvsize),
                'serial': dev.get("serial", ""),
                'vendorID': dev.get("vendor", ""),
                'vgUUID': vguuid,
                "discard_max_bytes": dev["discard_max_bytes"],
                # For backward compatibility with old engines.
                "discard_zeroes_data": 0,
            }
            if not checkStatus:
                devInfo["status"] = "unknown"
            devices.append(devInfo)

        if checkStatus:
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
    def getDevicesVisibility(self, guids):
        """
        Check which of the luns with specified guids are visible

        :param guids: List of device GUIDs to check.
        :type guids: list

        :returns: dictionary of specified guids and respective visibility
                  boolean
        :rtype: dict
        """
        def _isVisible(guid):
            try:
                res = (os.stat('/dev/mapper/' + guid).st_mode &
                       stat.S_IRUSR != 0)
            except OSError as e:
                if e.errno != errno.ENOENT:
                    raise
                res = False
            return res

        visibility = [_isVisible(guid) for guid in guids]
        if not all(visibility):
            multipath.rescan()
            visibility = [_isVisible(guid) for guid in guids]
        visibility = dict(zip(guids, visibility))

        # After multipath.rescan, existing devices may disapper, and new
        # devices may appear, making lvm filter stale.
        lvm.invalidateFilter()

        return {'visible': visibility}

    @public
    def createVG(self, vgname, devlist, force=False):
        """
        Creates a volume group with the name 'vgname' out of the devices in
        'devlist'

        :param vgname: The human readable name of the vg.
        :type vgname: str
        :param devlist: A list of devices to be included in the VG.
                        The devices must be unattached.
        :type devlist: list

        :returns: the UUID of the new VG.
        :rtype: UUID
        """
        # TODO: remove support for string value
        force = force in (True, "true", "True")

        MINIMALVGSIZE = 10 * GiB

        misc.validateUUID(vgname, 'vgname')
        # getSharedLock(connectionsResource...)
        knowndevs = set(os.path.basename(p) for p
                        in multipath.getMPDevNamesIter())
        size = 0
        devices = []
        unknowndevs = []

        for dev in devlist:
            if dev in knowndevs:
                devices.append(dev)
                size += multipath.getDeviceSize(devicemapper.getDmId(dev))
            else:
                unknowndevs.append(dev)

        if unknowndevs:
            raise se.InaccessiblePhysDev(unknowndevs)

        # Minimal size check
        if size < MINIMALVGSIZE:
            raise se.VolumeGroupSizeError(
                "VG size must be at least %s MiB" %
                str(MINIMALVGSIZE // MiB))

        lvm.createVG(vgname, devices, blockSD.STORAGE_UNREADY_DOMAIN_TAG,
                     metadataSize=blockSD.VG_METADATASIZE,
                     force=force)

        return dict(uuid=lvm.getVG(vgname).uuid)

    @deprecated
    @public
    def removeVG(self, vgUUID):
        """
        DEPRECATED: formatSD effectively removes the VG.

        Removes a volume group.

        :param vgUUID: The UUID of the VG you want removed.
        :type vgUUID: UUID
        """
        vars.task.setDefaultException(se.VolumeGroupActionError("%s" % vgUUID))
        # getSharedLock(connectionsResource...)
        try:
            lvm.removeVGbyUUID(vgUUID)
        except se.VolumeGroupDoesNotExist:
            pass

    @public
    def getTaskStatus(self, taskID):
        """
        Gets the status of a task.

        :param taskID: The ID of the task you want the check.
        :type taskID: ID?

        :returns: a dict containing the status information of the task.
        :rtype: dict
        """
        # getSharedLock(tasksResource...)
        taskStatus = self.taskMng.getTaskStatus(taskID=taskID)
        return dict(taskStatus=taskStatus)

    @public
    def getAllTasksStatuses(self):
        """
        Gets the status of all public tasks.
        """
        # getSharedLock(tasksResource...)
        # Calling on non-SPM is client error.
        if not self._pool.is_connected():
            raise exception.expected(se.SpmStatusError())

        # Calling on non-SPM is client error.
        try:
            allTasksStatus = self._pool.getAllTasksStatuses()
        except securable.SecureError:
            raise exception.expected(se.SpmStatusError())

        return dict(allTasksStatus=allTasksStatus)

    @public
    def getTaskInfo(self, taskID):
        """
        Gets information about a Task.

        :param taskID: The ID of the task you want to get info on.
        :type taskID: ID ?

        :returns: a dict with information about the task.
        :rtype: dict

        :raises: :exc:`storage.exception.UnknownTask` if a task with the
                 specified taskID doesn't exist.
        """
        # getSharedLock(tasksResource...)
        inf = self.taskMng.getTaskInfo(taskID=taskID)
        return dict(TaskInfo=inf)

    @public
    def getAllTasksInfo(self):
        """
        Get the information of all the tasks in a storage pool.

        :returns: a dict of all the tasks information.
        :rtype: dict
        """
        # getSharedLock(tasksResource...)
        # Calling on non-SPM is client error.
        if not self._pool.is_connected():
            raise exception.expected(se.SpmStatusError())

        # Calling on non-SPM is client error.
        try:
            allTasksInfo = self._pool.getAllTasksInfo()
        except securable.SecureError:
            raise exception.expected(se.SpmStatusError())

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
    def stopTask(self, taskID):
        """
        Stops a task.

        :param taskID: The ID of the task you want to stop.
        :type taskID: ID?

        :returns: :keyword:`True` if task was stopped successfully.
        :rtype: bool
        """
        return self.taskMng.stopTask(taskID=taskID, force=False)

    @public
    def clearTask(self, taskID):
        """
        Clears a task. ?

        :param taskID: The ID of the task you want to clear.
        :type taskID: ID?

        :returns: :keyword:`True` if task was cleared successfully.
        :rtype: bool
        """
        # getExclusiveLock(tasksResource...)
        return self.taskMng.clearTask(taskID=taskID)

    @public
    def revertTask(self, taskID):
        """
        Revert a task.

        :param taskID: The ID of the task you want to clear.
        :type taskID: ID?

        :returns:
        :rtype:
        """
        # getExclusiveLock(tasksResource...)
        return self.taskMng.revertTask(taskID=taskID)

    @public
    def getFileStats(self, sdUUID, pattern='*', caseSensitive=False):
        """
        Returns statistics of all files in the domain filtered according to
        pattern.

        :param sdUUID: The UUID of the storage domain you want to query.
        :type sdUUID: UUID
        :param pattern: The glob expression for filtering.
        :type pattern: str
        :param caseSensitive: Enables case-sensitive matching.
        :type caseSensitive: bool

        :returns: file statistics for files matching pattern.
        :rtype: dict
        """
        vars.task.setDefaultException(se.GetFileStatsError(sdUUID))
        vars.task.getSharedLock(STORAGE, sdUUID)

        dom = sdCache.produce(sdUUID=sdUUID)
        if not dom.isISO or dom.getStorageType() not in sd.FILE_DOMAIN_TYPES:
            raise se.GetFileStatsError(sdUUID)

        fileStats = dom.getFileList(pattern=pattern,
                                    caseSensitive=caseSensitive)
        return {'fileStats': fileStats}

    def _getSDTypeFindMethod(self, domType):
        # TODO: make sd.domain_types a real dictionary and remove this.
        # Storage Domain Types find methods
        SDTypeFindMethod = {sd.NFS_DOMAIN: nfsSD.findDomain,
                            sd.FCP_DOMAIN: blockSD.findDomain,
                            sd.ISCSI_DOMAIN: blockSD.findDomain,
                            sd.LOCALFS_DOMAIN: localFsSD.findDomain,
                            sd.POSIXFS_DOMAIN: nfsSD.findDomain,
                            sd.GLUSTERFS_DOMAIN: glusterSD.findDomain}
        return SDTypeFindMethod.get(domType)

    def _prefetchDomains(self, domType, conObj):
        uuidPatern = "????????-????-????-????-????????????"

        if domType in (sd.FCP_DOMAIN, sd.ISCSI_DOMAIN):
            uuids = tuple(blockSD.getStorageDomainsList())
        elif domType is sd.NFS_DOMAIN:
            lPath = conObj._mountCon._getLocalPath()
            self.log.debug("nfs local path: %s", lPath)
            goop = oop.getProcessPool(sc.GLOBAL_OOP)
            uuids = tuple(os.path.basename(d) for d in
                          goop.glob.glob(os.path.join(lPath, uuidPatern)))
        elif domType is sd.POSIXFS_DOMAIN:
            lPath = conObj._getLocalPath()
            self.log.debug("posix local path: %s", lPath)
            goop = oop.getProcessPool(sc.GLOBAL_OOP)
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
        findMethod = self._getSDTypeFindMethod(domType)
        return dict.fromkeys(uuids, findMethod)

    @deprecated
    @public
    def connectStorageServer(self, domType, spUUID, conList):
        """
        Connects to a storage low level entity (server).

        :param domType: The type of the connection sometimes expressed as the
                        corresponding domain type
        :param spUUID: deprecated, unused
        :param conList: A list of connections. Each connection being a dict
                        with keys depending on the type
        :type conList: list

        :returns: a list of statuses status will be 0 if connection was
                  successful
        :rtype: dict
        """
        vars.task.setDefaultException(
            se.StorageServerConnectionError(
                "domType=%s, spUUID=%s, conList=%s" %
                (domType, spUUID, conList)))

        results = storageServer.connect(domType, conList)

        # In case there were changes in devices size
        # while the VDSM was not connected, we need to
        # call refreshStorage.
        if domType in (sd.FCP_DOMAIN, sd.ISCSI_DOMAIN):
            sdCache.refreshStorage()

        for con, status in results:
            if status != 0:
                continue

            try:
                doms = self._prefetchDomains(domType, con)
            except:
                self.log.debug("prefetch failed: %s",
                               sdCache.knownSDs, exc_info=True)
            else:
                # Any pre-existing domains in sdCache stand the chance of
                # being invalid, since there is no way to know what happens
                # to them while the storage is disconnected.
                for sdUUID in doms:
                    sdCache.manuallyRemoveDomain(sdUUID)
                sdCache.knownSDs.update(doms)

        self.log.debug("knownSDs: {%s}", ", ".join("%s: %s.%s" %
                       (k, v.__module__, v.__name__)
                       for k, v in six.iteritems(sdCache.knownSDs)))

        # Connecting new device may change the visible storage domain list
        # so invalidate caches
        sdCache.invalidateStorage()
        status_list = [{"id": con.id, "status": status}
                       for con, status in results]
        return dict(statuslist=status_list)

    @deprecated
    @public
    def disconnectStorageServer(self, domType, spUUID, conList):
        """
        Disconnects from a storage low level entity (server).

        :param domType: The type of the connection expressed as the sometimes
                        corresponding domains type
        :param spUUID: deprecated, unused
        :param conList: A list of connections. Each connection being a dict
                        with keys depending on the type
        :type conList: list

        :returns: a list of statuses status will be 0 if disconnection was
                  successful
        :rtype: dict
        """
        vars.task.setDefaultException(
            se.StorageServerDisconnectionError(
                "domType=%s, spUUID=%s, conList=%s" %
                (domType, spUUID, conList)))

        results = storageServer.disconnect(domType, conList)

        # Disconnecting a device may change the visible storage domain list
        # so invalidate the caches
        sdCache.refreshStorage(resize=False)
        status_list = [{"id": con.id, "status": status}
                       for con, status in results]
        return dict(statuslist=status_list)

    @public
    def getStoragePoolInfo(self, spUUID):
        """
        Gets info about a storage pool.

        :param spUUID: The UUID of the storage pool you want to get info on.
        :type spUUID: UUID

        :returns: getPool(spUUID).getInfo
        """
        vars.task.setDefaultException(
            se.StoragePoolActionError("spUUID=%s" % spUUID))
        vars.task.getSharedLock(STORAGE, spUUID)
        pool = self.getPool(spUUID)
        poolInfo = pool.getInfo()
        doms = pool.getDomains()
        domInfo = self._getDomsStats(pool.domainMonitor, doms)
        for sdUUID in doms:
            if domInfo[sdUUID]['isoprefix']:
                poolInfo['isoprefix'] = domInfo[sdUUID]['isoprefix']
                break
        else:
            poolInfo['isoprefix'] = ''  # No ISO domain found

        return dict(info=poolInfo, dominfo=domInfo)

    @public
    def createStorageDomain(self, storageType, sdUUID, domainName,
                            typeSpecificArg, domClass,
                            domVersion=sc.SUPPORTED_DOMAIN_VERSIONS[0],
                            block_size=sc.BLOCK_SIZE_512,
                            max_hosts=sc.HOSTS_4K_1M):
        """
        Creates a new storage domain.

        Arguments:

            storageType (int): The storage type of the new storage
                domain (eg. NFS) as defined in sd.py.
            sdUUID (UUID): The UUID of the new storage domain.
            domainName (str): The human readable name of
                the new storage domain.
            typeSpecificArg (dict): Arguments that are specific to the
                storage type.
            domClass (int): The class of the new storage domain,
                as defined in sd.py (eg. iso, data)
            block_size (int): Underlying storage block size. Valid values are
                512 and 4096 (only for file based storage domains).
            max_hosts (int): Number of hosts, supported by that storage domain
                Valid values are 1 to 2000, bigger number of hosts requires
                bigger lockspaces with 4096 block.
        """
        msg = ("storageType=%s, sdUUID=%s, domainName=%s, "
               "domClass=%s, typeSpecificArg=%s domVersion=%s"
               "block_size=%s, max_hosts=%s" %
               (storageType, sdUUID, domainName, domClass,
                typeSpecificArg, domVersion, block_size, max_hosts))
        domVersion = int(domVersion)
        vars.task.setDefaultException(se.StorageDomainCreationError(msg))
        misc.validateUUID(sdUUID, 'sdUUID')
        self.validateNonDomain(sdUUID)

        if domClass not in sd.DOMAIN_CLASSES:
            raise se.StorageDomainClassError()

        # getSharedLock(connectionsResource...)
        # getExclusiveLock(sdUUID...)
        if storageType in sd.BLOCK_DOMAIN_TYPES:
            create = blockSD.BlockStorageDomain.create
        elif storageType in (sd.NFS_DOMAIN, sd.POSIXFS_DOMAIN):
            create = nfsSD.NfsStorageDomain.create
        elif storageType == sd.GLUSTERFS_DOMAIN:
            create = glusterSD.GlusterStorageDomain.create
        elif storageType == sd.LOCALFS_DOMAIN:
            create = localFsSD.LocalFsStorageDomain.create
        else:
            raise se.StorageDomainTypeError(storageType)

        newSD = create(
            sdUUID,
            domainName,
            domClass,
            typeSpecificArg,
            storageType,
            domVersion,
            block_size=block_size,
            max_hosts=max_hosts)

        findMethod = self._getSDTypeFindMethod(storageType)
        sdCache.knownSDs[sdUUID] = findMethod
        self.log.debug("knownSDs: {%s}", ", ".join("%s: %s.%s" %
                       (k, v.__module__, v.__name__)
                       for k, v in six.iteritems(sdCache.knownSDs)))

        sdCache.manuallyAddDomain(newSD)

    @public
    def validateStorageDomain(self, sdUUID):
        """
        Validates that the storage domain is accessible.

        :param sdUUID: The UUID of the storage domain you want to validate.
        :type sdUUID: UUID

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
    def formatStorageDomain(self, sdUUID, autoDetach=False):
        """
        Formats a detached storage domain.

        .. warning::
            This removes all data from the storage domain.

        :param sdUUID: The UUID for the storage domain you want to format.
        :param autoDetach: DEPRECATED
        :type sdUUID: UUID

        :returns: Nothing
        """
        multipath.rescan()
        vars.task.setDefaultException(
            se.StorageDomainActionError("sdUUID=%s" % sdUUID))
        # getSharedLock(connectionsResource...)

        vars.task.getExclusiveLock(STORAGE, sdUUID)
        # Avoid format if domain part of connected pool
        try:
            domDict = self._pool.getDomains()
        except se.StoragePoolNotConnected:
            pass
        else:
            if sdUUID in domDict:
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
                se.MiscBlockReadIncomplete) as e:
            self.log.warn("Domain %s has problem with metadata. Continue "
                          "formatting... (%s)", sdUUID, e)

        self._recycle(sd)

    @public
    def setStorageDomainDescription(self, sdUUID, description):
        """
        Sets a storage domain's description.

        :param sdUUID: The UUID of the storage domain you want to modify.
        :type sdUUID: UUID
        :param description: The new description.
        :type description: str
        """
        if len(description) > sd.MAX_DOMAIN_DESCRIPTION_SIZE:
            raise se.StorageDomainDescriptionTooLongError()

        vars.task.setDefaultException(
            se.StorageDomainActionError(
                "sdUUID=%s, description=%s" % (sdUUID, description)))
        dom = sdCache.produce(sdUUID=sdUUID)
        vars.task.getSharedLock(STORAGE, sdUUID)

        pool = self.getPool(dom.getPools()[0])
        pool.setSDDescription(dom, description)

    @public
    def getStorageDomainInfo(self, sdUUID):
        """
        Gets the info of a storage domain.

        :param sdUUID: The UUID of the storage domain you want to get
                       info about.
        :type sdUUID: UUID

        :returns: a dict containing the information about the domain.
        :rtype: dict
        """
        vars.task.setDefaultException(
            se.StorageDomainActionError("sdUUID=%s" % sdUUID))
        dom = self.validateSdUUID(sdUUID)
        # getSharedLock(connectionsResource...)

        vars.task.getSharedLock(STORAGE, sdUUID)
        return dict(info=dom.getInfo())

    @public
    def getStorageDomainStats(self, sdUUID):
        """
        Gets a storage domain's statistics.

        :param sdUUID: The UUID of the storage domain that you want to get
                       it's statistics.
        :type sdUUID: UUID

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
            remotePath=None):
        """
        Returns a List of all or pool specific storage domains.
        If remotePath is specified, storageType is required.

        :param spUUID: The UUID of the storage pool you want to list.
                       If spUUID equals to :attr:`~volume.BLANK_UUID` all
                       pools will be listed.
        :type spUUID: UUID

        :returns: a dict containing list of storage domains.
        :rtype: dict
        """
        vars.task.setDefaultException(
            se.StorageDomainActionError("spUUID: %s" % spUUID))
        sdCache.refreshStorage()
        if remotePath:
            remotePath = fileUtils.normalize_path(remotePath)
            local_path = fileUtils.transformPath(remotePath)
        if spUUID and spUUID != sc.BLANK_UUID:
            domains = list(self.getPool(spUUID).getDomains())
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
                if remotePath and local_path != dom.getRemotePath():
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
        info["pe_count"] = str(pv.pe_count)
        info["pe_alloc_count"] = str(pv.pe_alloc_count)
        info["GUID"] = str(pv.guid)
        info["discard_max_bytes"] = devInfo["discard_max_bytes"]
        # For backward compatibility with old engines.
        info["discard_zeroes_data"] = 0
        return info

    @deprecated
    @public
    def getVGList(self, storageType=None):
        """
        Returns a list all VGs.

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
            devNames.extend(map(getGuid, lvm.listPVNames(vg.name)))
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
    def getVGInfo(self, vgUUID):
        """
        Gets the info of a VG.

        :param vgUUID: The UUID of the VG.
        :type vgUUID: UUID

        :returns: a dict containing the info about the VG.
        :rtype: dict

        :raises: :exc:`storage.exception.VolumeGroupDoesNotExist`
                 if no VG with the specified UUID is found
        """
        vars.task.setDefaultException(se.VolumeGroupActionError("%s" % vgUUID))
        # As we have no synchronization between the host getting the
        # information and the SPM/other hosts we invalidate the vg
        # pvs in order to try and get the updated information.
        vg = lvm.getVGbyUUID(vgUUID)
        lvm.invalidateVG(vg.name, invalidateLVs=False, invalidatePVs=True)
        # getSharedLock(connectionsResource...)
        return dict(info=self.__getVGsInfo([vgUUID])[0])

    @public
    def discoverSendTargets(self, con):
        """
        Discovers iSCSI targets.

        :param con: A dict containing connection information like host FQDN or
            IP address, login credentials etc.
        :type con: dict

        :returns: a dict containing the send targets that were discovered.
        :rtype: dict
        """
        ip = con['connection']
        port = int(con['port'])
        ipv6_enabled = con['ipv6_enabled']
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
            if ipv6_enabled or not target.portal.is_ipv6():
                fullTargets.append(str(target))
                partialTargets.append(target.iqn)

        return dict(targets=partialTargets, fullTargets=fullTargets)

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
    def add_image_ticket(self, ticket):
        imagetickets.add_ticket(ticket)

    @public
    def get_image_ticket(self, uuid):
        return dict(result=imagetickets.get_ticket(uuid))

    @public
    def remove_image_ticket(self, uuid):
        imagetickets.remove_ticket(uuid)

    @public
    def extend_image_ticket(self, uuid, timeout):
        imagetickets.extend_ticket(uuid, timeout)

    @public
    def getVolumeSize(self, sdUUID, spUUID, imgUUID, volUUID):
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

        :returns: a dict with the size of the volume.
        :rtype: dict
        """
        # Return string because xmlrpc's "int" is very limited
        dom = sdCache.produce(sdUUID=sdUUID)
        size = dom.getVolumeSize(imgUUID, volUUID)
        return dict(
            apparentsize=str(size.apparentsize),
            truesize=str(size.truesize))

    @public
    def setVolumeSize(self, sdUUID, spUUID, imgUUID, volUUID, capacity):
        capacity = int(capacity)
        vol = sdCache.produce(sdUUID).produceVolume(imgUUID, volUUID)
        capacity = utils.round(capacity, sc.BLOCK_SIZE_4K)
        vol.setCapacity(capacity)

    @public
    def getVolumeInfo(self, sdUUID, spUUID, imgUUID, volUUID):
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

        :returns: a dict with the info of the volume.
        :rtype: dict
        """
        info = self._produce_volume(sdUUID, imgUUID, volUUID).getInfo()
        return dict(info=info)

    @public
    def getQemuImageInfo(self, sdUUID, spUUID, imgUUID, volUUID):
        """
        Gets a volume's qemuimg info.
        This command should work only if the volume was already prepared.

        :param sdUUID: The UUID of the storage domain that owns the volume.
        :type sdUUID: UUID
        :param spUUID: The UUID of the storage pool that owns the volume.
        :type spUUID: UUID
        :param imgUUID: The UUID of the image contained on the volume.
        :type imgUUID: UUID
        :param volUUID: The UUID of the volume you want to get the info on.
        :type volUUID: UUID

        :returns: The volume information returned by qemu-img info command.
        :rtype: dict
        """
        vars.task.getSharedLock(STORAGE, sdUUID)
        sd = sdCache.produce(sdUUID)
        vol = sd.produceVolume(imgUUID, volUUID)
        info = vol.getQemuImageInfo()
        return dict(info=info)

    @public
    def measure(self, sdUUID, imgUUID, volUUID, dest_format, backing=True):
        """
        Measure the size of a volume using qemu-img

        Arguments:
            sdUUID (str): The UUID of the storage domain that owns the volume.
            imgUUID (str): The UUID of the image contained on the volume.
            volUUID (str): The UUID of the volume you want to get the info on.
            dest_format (str): The output format we want to measure for
            backing (bool): True if we want to measure the volume with its
                        backing chain, False otherwise. (Default: True)

        Returns:
            dict containing the required size of the volume
        """
        vol = self._produce_volume(sdUUID, imgUUID, volUUID)
        result = qemuimg.measure(
            vol.getVolumePath(),
            format=sc.fmt2str(vol.getFormat()),
            output_format=sc.fmt2str(dest_format),
            backing=backing,
            is_block=vol.is_block()
        )

        return dict(result=result)

    @public
    def appropriateDevice(self, guid, thiefId, deviceType):
        """
        Change ownership of the guid device to vdsm:qemu

        Warning: Internal use only.
        """
        if deviceType == 'mpath':
            devPath = os.path.join(devicemapper.DMPATH_PREFIX, guid)
            supervdsm.getProxy().appropriateDevice(guid, thiefId, deviceType)
            supervdsm.getProxy().udevTrigger(guid, deviceType)
            size = str(multipath.getDeviceSize(devicemapper.getDmId(guid)))
            device = dict(truesize=size, apparentsize=size, path=devPath)
        elif deviceType == 'rbd':
            # In case the device is rbd, the entire path will be passed
            devPath = guid
            supervdsm.getProxy().appropriateDevice(guid, thiefId, deviceType)
            supervdsm.getProxy().udevTrigger(devPath, deviceType)
            device = dict(path=devPath)
        else:
            raise RuntimeError("Unsupported device type %r" % deviceType)

        function.retry(partial(fileUtils.validateQemuReadable, devPath),
                       expectedException=OSError,
                       timeout=QEMU_READABLE_TIMEOUT)

        # Get the size of the logical unit volume.
        # Casting to string for keeping consistency with public methods
        # that use it to overcome xmlrpc integer size limitation issues.

        return device

    @public
    def inappropriateDevices(self, thiefId):
        """
        Warning: Internal use only.
        """
        fails = supervdsm.getProxy().rmAppropriateMultipathRules(thiefId)
        if fails:
            self.log.error("Failed to remove the following rules: %s", fails)

    @public
    def prepareImage(self, sdUUID, spUUID, imgUUID, leafUUID,
                     allowIllegal=False):
        """
        Prepare an image, activating the needed volumes.
        Return the path to the leaf and an unsorted list of the image volumes.

        :param sdUUID: The UUID of the storage domain that owns the volume.
        :type sdUUID: UUID
        :param spUUID: The UUID of the storage pool that owns the volume.
        :type spUUID: UUID
        :param imgUUID: The UUID of the image contained on the volume.
        :type imgUUID: UUID
        """
        # If the pool is not blank we should make sure that we are connected
        # to the pool.
        if spUUID != sd.BLANK_UUID:
            self.getPool(spUUID)

        vars.task.getSharedLock(STORAGE, sdUUID)

        imgVolumesInfo = []
        dom = sdCache.produce(sdUUID)
        allVols = dom.getAllVolumes()
        # Filter volumes related to this image
        imgVolumes = list(sd.getVolsOfImage(allVols, imgUUID))

        if leafUUID not in imgVolumes:
            raise se.VolumeDoesNotExist(leafUUID)

        for volUUID in imgVolumes:
            legality = dom.produceVolume(imgUUID, volUUID).getLegality()
            if legality == sc.ILLEGAL_VOL:
                if allowIllegal:
                    self.log.info("Preparing illegal volume %s", leafUUID)
                else:
                    raise se.prepareIllegalVolumeError(volUUID)

        imgPath = dom.activateVolumes(imgUUID, imgVolumes)
        try:
            for volUUID in imgVolumes:
                dom.produceVolume(imgUUID, volUUID).updateInvalidatedSize()

            if spUUID and spUUID != sd.BLANK_UUID:
                runImgPath = dom.linkBCImage(imgPath, imgUUID)
            else:
                runImgPath = imgPath

            leafInfo = dom.produceVolume(imgUUID, leafUUID).getVmVolumeInfo()

            leafPath = os.path.join(runImgPath, leafUUID)
            for volUUID in imgVolumes:
                path = os.path.join(dom.domaindir, sd.DOMAIN_IMAGES, imgUUID,
                                    volUUID)
                volInfo = {'domainID': sdUUID, 'imageID': imgUUID,
                           'volumeID': volUUID, 'path': path}

                lease = dom.getVolumeLease(imgUUID, volUUID)

                if lease.path and isinstance(lease.offset, numbers.Integral):
                    volInfo.update({
                        'leasePath': lease.path,
                        'leaseOffset': lease.offset,
                    })

                imgVolumesInfo.append(volInfo)
        except Exception:
            # Tear down everyting on failure.
            try:
                dom.unlinkBCImage(imgUUID)
                dom.deactivateImage(imgUUID)
            except Exception:
                self.log.exception("Error tearing down image")
            raise

        return {'path': leafPath, 'info': leafInfo,
                'imgVolumesInfo': imgVolumesInfo}

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

        dom = sdCache.produce(sdUUID)
        dom.unlinkBCImage(imgUUID)
        dom.deactivateImage(imgUUID)

    @public
    def teardownVolume(self, sdUUID, imgUUID, volUUID):
        """
        Teardown image volume.

        Arguments:
            sdUUID (str): The UUID of the storage domain that owns the volume.
            imgUUID (str): The UUID of the image contained on the volume.
            volUUID (str): The UUID of the volume to be torn down.
        """
        vars.task.getSharedLock(STORAGE, sdUUID)

        dom = sdCache.produce_manifest(sdUUID)
        dom.teardownVolume(imgUUID, volUUID)

    @public
    def getVolumesList(self, sdUUID, spUUID, imgUUID=sc.BLANK_UUID):
        """
        Gets a list of all volumes.

        :param spUUID: Unused.
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
        vols = dom.getAllVolumes()
        if imgUUID == sc.BLANK_UUID:
            volUUIDs = list(vols)
        else:
            volUUIDs = [k for k, v in six.iteritems(vols) if imgUUID in v.imgs]
        return dict(uuidlist=volUUIDs)

    @public
    def dumpStorageDomain(self, sdUUID, full=False):
        """
        Gets a dictionary of storage domain raw metadata.

        :param sdUUID: The UUID of the storage domain you want to query.
        :type  sdUUID: UUID.
        :param full: If set to True, will also dump leases, lockspace and
                     xleases information along with SD metadata and
                     volumes info. Using the default setting would save
                     time and bandwidth.
        :type full: boolean.

        :returns: Storage domain dumped metadata and volumes along with its
                  leases, lockspace and xleases information if full is True.
        :rtype: dict.
        """
        vars.task.getSharedLock(STORAGE, sdUUID)
        dom = sdCache.produce(sdUUID)
        # Make sure we are not reading stale metadata.
        dom.invalidateMetadata()
        return dict(result=dom.dump(full=full))

    @public
    def getImagesList(self, sdUUID):
        """
        Gets a list of all the images of specific domain.

        :param sdUUID: The UUID of the storage domain you want to query.
        :type sdUUID: UUID.

        :returns: a dict with a list of the images belonging to the specified
                  domain.
        :rtype: dict
        """
        vars.task.getSharedLock(STORAGE, sdUUID)
        dom = sdCache.produce(sdUUID=sdUUID)
        images = dom.getAllImages()
        return dict(imageslist=list(images))

    @deprecated
    @public
    def getImageDomainsList(self, spUUID, imgUUID):
        """
        Gets a list of all data domains in the pool that contains imgUUID.

        :param spUUID: The UUID of the storage pool you want to query.
        :type spUUID: UUID
        :param imgUUID: The UUID of the image you want to filter by.
        :type spUUID: UUID

        :returns: a dict containing the list of domains found.
        :rtype: dict
        """
        vars.task.setDefaultException(
            se.GetStorageDomainListError("spUUID=%s imgUUID=%s" %
                                         (spUUID, imgUUID)))
        vars.task.getSharedLock(STORAGE, spUUID)
        pool = self.getPool(spUUID)
        # Find out domain list from the pool metadata
        activeDoms = sorted(pool.getDomains(activeOnly=True))
        imgDomains = []
        for sdUUID in activeDoms:
            dom = sdCache.produce(sdUUID=sdUUID)
            if dom.isData():
                with rm.acquireResource(STORAGE, sdUUID, rm.SHARED):
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
    def prepareForShutdown(self):
        """
        Prepares to shutdown host.
        Stops all tasks.

        .. note::
            shutdown cannot be cancelled, must stop all actions.
        """
        # TODO: Implement!!!! TBD: required functionality (stop hsm tasks,
        #                          stop spm tasks if spm etc.)
        try:
            sp.StoragePool.cleanupMasterMount()
            self.__releaseLocks()

            try:
                if self._pool.spmMailer:
                    self._pool.spmMailer.stop()
                    self._pool.spmMailer.tp.joinAll()

                if self._pool.hsmMailer:
                    self._pool.hsmMailer.stop()
            except se.StoragePoolNotConnected:
                pass

            # Stop repoStat threads
            try:
                self.domainMonitor.shutdown()
            except Exception:
                self.log.warning("Failed to stop RepoStats thread",
                                 exc_info=True)

            self.taskMng.prepareForShutdown()
            oop.stop()
            self.mpathhealth_monitor.stop()
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
    def upgradeStoragePool(self, spUUID, targetDomVersion):
        targetDomVersion = int(targetDomVersion)
        # This lock has to be mutual with the pool metadata operations (like
        # activateSD/deactivateSD) as the operation uses the pool metadata.
        vars.task.getExclusiveLock(STORAGE, spUUID)
        pool = self.getPool(spUUID)
        pool._upgradePool(targetDomVersion, lockTimeout=0)
        return {"upgradeStatus": "started"}

    def _getDomsStats(self, domainMonitor, doms):
        domInfo = {}
        repoStats = self._getRepoStats(domainMonitor)

        for sdUUID, sdStatus in six.iteritems(doms):
            # Return statistics for active domains only
            domInfo[sdUUID] = {'status': sdStatus, 'alerts': [],
                               'isoprefix': ''}

            if sdStatus != sd.DOM_ACTIVE_STATUS or sdUUID not in repoStats:
                continue

            domInfo[sdUUID]['version'] = repoStats[sdUUID]['result']['version']

            # For unreachable domains repoStats will return disktotal and
            # diskfree as None.
            if (repoStats[sdUUID]['disktotal'] is not None and
                    repoStats[sdUUID]['diskfree'] is not None):
                domInfo[sdUUID]['disktotal'] = repoStats[sdUUID]['disktotal']
                domInfo[sdUUID]['diskfree'] = repoStats[sdUUID]['diskfree']

            if not repoStats[sdUUID]['mdavalid']:
                domInfo[sdUUID]['alerts'].append({
                    'code': se.SmallVgMetadata.code,
                    'message': se.SmallVgMetadata.msg,
                })
                self.log.warning("VG %s's metadata size too small %s",
                                 sdUUID, repoStats[sdUUID]['mdasize'])

            if not repoStats[sdUUID]['mdathreshold']:
                domInfo[sdUUID]['alerts'].append({
                    'code': se.VgMetadataCriticallyFull.code,
                    'message': se.VgMetadataCriticallyFull.msg,
                })
                self.log.warning("VG %s's metadata size exceeded critical "
                                 "size: mdasize=%s mdafree=%s", sdUUID,
                                 repoStats[sdUUID]['mdasize'],
                                 repoStats[sdUUID]['mdafree'])

            if repoStats[sdUUID]['isoprefix'] is not None:
                domInfo[sdUUID]['isoprefix'] = repoStats[sdUUID]['isoprefix']

        return domInfo

    def _getRepoStats(self, domainMonitor, domains=()):
        domains = frozenset(domains)
        repoStats = {}
        statsGenTime = time.time()

        for sdUUID, domStatus in domainMonitor.getDomainsStatus():
            if domains and sdUUID not in domains:
                continue
            if domStatus.error is None:
                code = 0
            elif isinstance(domStatus.error, se.StorageException):
                code = domStatus.error.code
            else:
                code = se.StorageException.code

            disktotal, diskfree = domStatus.diskUtilization
            vgmdtotal, vgmdfree = domStatus.vgMdUtilization
            lastcheck = '%.1f' % (statsGenTime - domStatus.checkTime)

            repoStats[sdUUID] = {
                'finish': domStatus.checkTime,

                'result': {
                    'code': code,
                    'lastCheck': lastcheck,
                    'delay': str(domStatus.readDelay),
                    'valid': (domStatus.error is None),
                    'version': domStatus.version,
                    # domStatus.hasHostId can also be None
                    'acquired': domStatus.hasHostId is True,
                    'actual': domStatus.actual
                },

                'disktotal': disktotal,
                'diskfree': diskfree,

                'mdavalid': domStatus.vgMdHasEnoughFreeSpace,
                'mdathreshold': domStatus.vgMdFreeBelowThreashold,
                'mdasize': vgmdtotal,
                'mdafree': vgmdfree,

                'masterValidate': {
                    'mount': domStatus.masterMounted,
                    'valid': domStatus.masterValid
                },

                'isoprefix': domStatus.isoPrefix,
            }

        return repoStats

    @public
    def repoStats(self, domains=()):
        """
        Collects a storage repository's information and stats.

        :returns: result
        """
        result = {}

        repo_stats = self._getRepoStats(self.domainMonitor, domains=domains)

        for d in repo_stats:
            result[d] = repo_stats[d]['result']

        return result

    @public
    def multipath_health(self):
        return self.mpathhealth_monitor.status()

    @deprecated
    @public
    def startMonitoringDomain(self, sdUUID, hostID):
        with rm.acquireResource(STORAGE, HSM_DOM_MON_LOCK, rm.EXCLUSIVE):
            # Note: We cannot raise here StorageDomainIsMemberOfPool, as it
            # will break old hosted engine agent.
            self.domainMonitor.startMonitoring(sdUUID, int(hostID), False)

    @deprecated
    @public
    def stopMonitoringDomain(self, sdUUID):
        with rm.acquireResource(STORAGE, HSM_DOM_MON_LOCK, rm.EXCLUSIVE):
            if sdUUID in self.domainMonitor.poolDomains:
                raise se.StorageDomainIsMemberOfPool(sdUUID)
            self.domainMonitor.stopMonitoring([sdUUID])

    @public
    def getHostLeaseStatus(self, domains):
        """
        Returns host lease status for specified domains.

        Warning: Internal use only.

        :param domains:     mapping of host id indexed by domain uuid.
        :returns:           mapping of host lease status indexed by domain
                            uuid.  See clusterlock.py for possible values and
                            their meaning.
        """
        return {'domains': self.domainMonitor.getHostStatus(domains)}

    @public
    def prepareMerge(self, spUUID, subchainInfo):
        msg = "spUUID=%s, subchainInfo=%s" % (spUUID, subchainInfo)
        vars.task.setDefaultException(se.StorageException(msg))
        subchain = merge.SubchainInfo(subchainInfo, self._pool.id)
        pool = self.getPool(spUUID)
        sdCache.produce(subchain.sd_id)
        vars.task.getSharedLock(STORAGE, subchain.sd_id)
        self._spmSchedule(spUUID, "prepareMerge", pool.prepareMerge,
                          subchain)

    @public
    def finalizeMerge(self, spUUID, subchainInfo):
        msg = "spUUID=%s, subchainInfo=%s" % (spUUID, subchainInfo)
        vars.task.setDefaultException(se.StorageException(msg))
        subchain = merge.SubchainInfo(subchainInfo, self._pool.id)
        pool = self.getPool(spUUID)
        sdCache.produce(subchain.sd_id)
        vars.task.getSharedLock(STORAGE, subchain.sd_id)
        self._spmSchedule(spUUID, "finalizeMerge", pool.finalizeMerge,
                          subchain)

    def sdm_schedule(self, job):
        """
        SDM jobs currently run using the old TaskManager thread pool but none
        of the other old Task features (ie. rollbacks, persistence) are
        supported.  SDM tasks are managed using the Host Jobs API in jobs.py.
        """
        jobs.add(job)
        self.taskMng.scheduleJob("sdm", None, vars.task,
                                 job.description, job.run)

    @public
    def sdm_copy_data(self, job_id, source, destination, copy_bitmaps=False):
        job = copy_data.Job(job_id, self._pool.id, source, destination,
                            copy_bitmaps=copy_bitmaps)
        self.sdm_schedule(job)

    @public
    def sdm_sparsify_volume(self, job_id, vol_info):
        """
        Reduce sparse image size by converting free space on image to free
        space on storage domain using virt-sparsify --inplace (without using
        a temporary volume).
        """
        job = sparsify_volume.Job(job_id, self._pool.id, vol_info)
        self.sdm_schedule(job)

    @public
    def sdm_amend_volume(self, job_id, vol_info, qcow2_attr):
        job = amend_volume.Job(job_id, self._pool.id, vol_info, qcow2_attr)
        self.sdm_schedule(job)

    @public
    def sdm_update_volume(self, job_id, vol_info, vol_attr):
        job = update_volume.Job(job_id, self._pool.id, vol_info, vol_attr)
        self.sdm_schedule(job)

    @public
    def sdm_merge(self, job_id, subchain_info, merge_bitmaps=False):
        subchain = merge.SubchainInfo(subchain_info, self._pool.id)
        job = api_merge.Job(job_id, subchain, merge_bitmaps=merge_bitmaps)
        self.sdm_schedule(job)

    @public
    def sdm_move_domain_device(self, job_id, move_params):
        """
        Moves the data stored on a PV to other PVs that are part of the Storage
        Domain.

        :param job_id: The UUID of the job.
        :type job_id: UUID
        :param move_params: The move operation params
        :type move_params:
            `vdsm.storage.sdm.api.move_device.StorageDomainMoveDeviceParams`
        """
        job = move_device.Job(job_id, DISCONNECTED_HOST_ID, move_params)
        self.sdm_schedule(job)

    @public
    def sdm_reduce_domain(self, job_id, reduce_params):
        """
        Reduces a device from a block-based Storage Domain.

        :param job_id: The UUID of the job.
        :type job_id: UUID
        :param reduce_params: The reduce operation parameters
        :type reduce_params:
            'vdsm.storage.sdm.api.reduce_domain.StorageDomainReduceParams'
        """
        job = reduce_domain.Job(job_id, DISCONNECTED_HOST_ID, reduce_params)
        self.sdm_schedule(job)

    @public
    def sdm_add_bitmap(self, job_id, vol_info, bitmap):
        """
        Add a bitmap to the given volume.

        Arguments:
            job_id (str): The UUID of the storage domain that owns the volume.
            vol_info (dict): Dictionary that contains all the needed info
                on the volume.
            bitmap (str): The name of the bitmap to add.
        """
        job = add_bitmap.Job(job_id, self._pool.id, vol_info, bitmap)
        self.sdm_schedule(job)

    @public
    def sdm_remove_bitmap(self, job_id, vol_info, bitmap):
        """
        Remove the given bitmap from the given volume.

        Arguments:
            job_id (str): The UUID of the storage domain that owns the volume.
            vol_info (dict): Dictionary that contains all the needed info
                on the volume.
            bitmap (str): The name of the bitmap to remove.
        """
        job = remove_bitmap.Job(job_id, self._pool.id, vol_info, bitmap)
        self.sdm_schedule(job)

    @public
    def sdm_clear_bitmaps(self, job_id, vol_info):
        """
        Clear all the bitmaps from the given volume.

        Arguments:
            job_id (str):  The UUID of the job.
            vol_info (dict): Dictionary that contains all the needed info
                on the volume.
        """
        job = clear_bitmaps.Job(job_id, self._pool.id, vol_info)
        self.sdm_schedule(job)

    # Lease operations

    @public
    def create_lease(self, lease, metadata=None):
        lease = validators.Lease(lease)
        self._check_pool_connected()
        # TODO: can we move lock into the pool?
        vars.task.getSharedLock(STORAGE, lease.sd_id)
        self._spmSchedule(self._pool.spUUID, "create_lease",
                          self._pool.create_lease, lease, metadata)

    @public
    def delete_lease(self, lease):
        lease = validators.Lease(lease)
        self._check_pool_connected()
        # TODO: can we move lock into the pool?
        vars.task.getSharedLock(STORAGE, lease.sd_id)
        self._spmSchedule(self._pool.spUUID, "delete_lease",
                          self._pool.delete_lease, lease)

    @public
    def lease_info(self, lease):
        lease = validators.Lease(lease)
        self._check_pool_connected()
        with rm.acquireResource(STORAGE, lease.sd_id, rm.SHARED):
            dom = sdCache.produce_manifest(lease.sd_id)
            info = dom.lease_info(lease.lease_id)
        lease_info = dict(sd_id=info.lockspace,
                          lease_id=info.resource,
                          path=info.path,
                          offset=info.offset)
        return dict(result=lease_info)

    @public
    def rebuild_leases(self, sd_id):
        self._check_pool_connected()
        # TODO: can we move lock into the pool?
        vars.task.getSharedLock(STORAGE, sd_id)
        self._spmSchedule(self._pool.spUUID, "rebuild_leases",
                          self._pool.rebuild_leases, sd_id)

    @public
    def lease_status(self, lease):
        lease = validators.Lease(lease)
        self._check_pool_connected()
        with rm.acquireResource(STORAGE, lease.sd_id, rm.SHARED):
            dom = sdCache.produce_manifest(lease.sd_id)
            return dict(result=dom.lease_status(lease.lease_id, self._pool.id))

    @public
    def fence_lease(self, lease, metadata):
        lease = validators.Lease(lease)
        metadata = validators.JobMetadata(metadata)
        self._check_pool_connected()
        with rm.acquireResource(STORAGE, lease.sd_id, rm.SHARED):
            dom = sdCache.produce_manifest(lease.sd_id)
            dom.fence_lease(
                lease.lease_id,
                self._pool.id,
                metadata)

    # NBD

    @public
    def start_nbd_server(self, server_id, config):
        self._check_pool_connected()
        url = nbd.start_server(server_id, config)
        return dict(result=url)

    @public
    def stop_nbd_server(self, server_id):
        nbd.stop_server(server_id)

    # Transient disk

    @public
    def create_transient_disk(self, owner_name, disk_name, size):
        disk_info = transientdisk.create_disk(
            owner_name, disk_name, size)
        return dict(result=disk_info)

    @public
    def remove_transient_disk(self, owner_name, disk_name):
        transientdisk.remove_disk(owner_name, disk_name)

    @public
    def list_transient_disks(self, owner_name):
        disks = transientdisk.list_disks(owner_name)
        return dict(result=disks)

    # Helpers

    def _produce_volume(self, sdUUID, imgUUID, volUUID):
        vars.task.getSharedLock(STORAGE, sdUUID)
        dom = sdCache.produce_manifest(sdUUID=sdUUID)
        try:
            return dom.produceVolume(imgUUID=imgUUID, volUUID=volUUID)
        except se.VolumeDoesNotExist as e:
            raise exception.expected(e)

    # Validations

    def _check_pool_connected(self):
        if not self._pool.is_connected():
            # Calling when pool is not connected is client error.
            raise exception.expected(se.StoragePoolNotConnected())
