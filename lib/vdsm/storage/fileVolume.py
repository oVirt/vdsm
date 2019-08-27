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

import errno
import os

from vdsm import constants
from vdsm import utils
from vdsm.common import exception
from vdsm.common.commands import grepCmd
from vdsm.common.compat import glob_escape
from vdsm.common.marks import deprecated
from vdsm.common.threadlocal import vars
from vdsm.storage import constants as sc
from vdsm.storage import exception as se
from vdsm.storage import fallocate
from vdsm.storage import outOfProcess as oop
from vdsm.storage import qemuimg
from vdsm.storage import task
from vdsm.storage import volume
from vdsm.storage.compat import sanlock
from vdsm.storage.sdc import sdCache
from vdsm.storage.volumemetadata import VolumeMetadata


META_FILEEXT = ".meta"
LEASE_FILEOFFSET = 0


def getDomUuidFromVolumePath(volPath):
    # fileVolume path has pattern:
    # */sdUUID/images/imgUUID/volUUID
    sdPath = os.path.normpath(volPath).rsplit('/images/', 1)[0]
    target, sdUUID = os.path.split(sdPath)
    return sdUUID


class FileVolumeManifest(volume.VolumeManifest):

    # How this volume is presented to a vm.
    DISK_TYPE = "file"

    # Raw volumes should be aligned to block size, which is 512 or 4096
    # (not supported yet). qcow2 images should be aligned to cluster size
    # (64K default). To simplify, we use 1M alignment.
    align_size = constants.MEGAB

    def __init__(self, repoPath, sdUUID, imgUUID, volUUID):
        volume.VolumeManifest.__init__(self, repoPath, sdUUID, imgUUID,
                                       volUUID)

    @classmethod
    def is_block(cls):
        return False

    @property
    def oop(self):
        return oop.getProcessPool(self.sdUUID)

    def validateImagePath(self):
        """
        Validate that the image dir exists and valid.
        In the file volume repositories,
        the image dir must exists after creation its first volume.
        """
        manifest = sdCache.produce_manifest(self.sdUUID)
        imageDir = manifest.getImageDir(self.imgUUID)

        if not self.oop.os.path.isdir(imageDir):
            raise se.ImagePathError(imageDir)
        if not self.oop.os.access(imageDir, os.R_OK | os.W_OK | os.X_OK):
            raise se.ImagePathError(imageDir)
        self._imagePath = imageDir

    @classmethod
    def metaVolumePath(cls, volPath):
        if volPath:
            return volPath + META_FILEEXT
        else:
            return None

    def getMetaVolumePath(self, vol_path=None):
        """
        Get the volume metadata file/link path
        """
        if not vol_path:
            vol_path = self.getVolumePath()
        return self.metaVolumePath(vol_path)

    def validateMetaVolumePath(self):
        """
        In file volume repositories,
        the volume metadata must exists after the image/volume is created.
        """
        metaVolumePath = self.getMetaVolumePath()
        if not self.oop.fileUtils.pathExists(metaVolumePath):
            raise se.VolumeDoesNotExist(self.volUUID)

    def validateVolumePath(self):
        """
        In file volume repositories,
        the volume file and the volume md must exists after
        the image/volume is created.
        """
        self.log.debug("validate path for %s" % self.volUUID)
        if not self.imagePath:
            self.validateImagePath()
        volPath = os.path.join(self.imagePath, self.volUUID)
        if not self.oop.fileUtils.pathExists(volPath):
            raise se.VolumeDoesNotExist(self.volUUID)

        self._volumePath = volPath
        sd = sdCache.produce_manifest(self.sdUUID)
        if not sd.isISO():
            self.validateMetaVolumePath()

    def getMetadataId(self):
        """
        Get the metadata Id
        """
        return (self.getVolumePath(),)

    def getMetadata(self, metaId=None):
        """
        Get Meta data array of key,values lines
        """
        if not metaId:
            metaId = self.getMetadataId()

        volPath, = metaId
        metaPath = self.getMetaVolumePath(volPath)

        try:
            data = self.oop.readFile(metaPath, direct=True)
        except Exception as e:
            self.log.error(e, exc_info=True)
            raise se.VolumeMetadataReadError("%s: %s" % (metaId, e))

        data = data.rstrip(b"\0")
        lines = data.splitlines()
        md = VolumeMetadata.from_lines(lines)
        return md

    def getParent(self):
        """
        Return parent volume UUID
        """
        return self.getMetaParam(sc.PUUID)

    def getChildren(self):
        """ Return children volume UUIDs.

        This API is not suitable for use with a template's base volume.
        """
        imgDir, _ = os.path.split(self.volumePath)
        metaPattern = os.path.join(glob_escape(imgDir), "*.meta")
        metaPaths = oop.getProcessPool(self.sdUUID).glob.glob(metaPattern)
        pattern = "%s.*%s" % (sc.PUUID, self.volUUID)
        matches = grepCmd(pattern, metaPaths)
        if matches:
            children = []
            for line in matches:
                volMeta = os.path.basename(line.rsplit(':', 1)[0])
                children.append(os.path.splitext(volMeta)[0])  # volUUID
        else:
            children = tuple()

        return tuple(children)

    def getImage(self):
        """
        Return image UUID
        """
        return self.getMetaParam(sc.IMAGE)

    def getDevPath(self):
        """
        Return the underlying device (for sharing)
        """
        return self.getVolumePath()

    def getVolumeSize(self):
        """
        Return the volume size in bytes.
        """
        volPath = self.getVolumePath()
        return self.oop.os.stat(volPath).st_size

    def getVolumeTrueSize(self):
        """
        Return the in bytes size of the storage allocated for this volume
        on underlying storage
        """
        volPath = self.getVolumePath()
        return self.oop.os.stat(volPath).st_blocks * sc.STAT_BYTES_PER_BLOCK

    def setMetadata(self, meta, metaId=None, **overrides):
        """
        Set the meta data hash as the new meta data of the Volume
        """
        if not metaId:
            metaId = self.getMetadataId()

        try:
            self._putMetadata(metaId, meta, **overrides)
        except Exception as e:
            self.log.error(e, exc_info=True)
            raise se.VolumeMetadataWriteError(str(metaId) + str(e))

    @classmethod
    def file_setrw(cls, volPath, rw):
        sdUUID = getDomUuidFromVolumePath(volPath)
        mode = 0o440
        if rw:
            mode |= 0o220
        if oop.getProcessPool(sdUUID).os.path.isdir(volPath):
            mode |= 0o110
        oop.getProcessPool(sdUUID).os.chmod(volPath, mode)

    @deprecated  # valid only for domain version < 3, see volume.setrw
    def _setrw(self, rw):
        """
        Set the read/write permission on the volume (deprecated)
        """
        self.file_setrw(self.getVolumePath(), rw=rw)

    @classmethod
    def _putMetadata(cls, metaId, meta, **overrides):
        volPath, = metaId
        metaPath = cls.metaVolumePath(volPath)

        sd = sdCache.produce_manifest(meta.domain)

        data = meta.storage_format(sd.getVersion(), **overrides)

        with open(metaPath + ".new", "wb") as f:
            f.write(data)

        oop.getProcessPool(meta.domain).os.rename(metaPath + ".new", metaPath)

    def setImage(self, imgUUID):
        """
        Set image UUID
        """
        self.setMetaParam(sc.IMAGE, imgUUID)

    def removeMetadata(self, metaId=None):
        """
        Remove the meta file
        """
        metaPath = self.getMetaVolumePath()
        if self.oop.os.path.lexists(metaPath):
            self.log.info("Removing: %s", metaPath)
            self.oop.os.unlink(metaPath)

    @classmethod
    def leaseVolumePath(cls, vol_path):
        if vol_path:
            return vol_path + sc.LEASE_FILEEXT
        else:
            return None

    def getLeaseVolumePath(self, vol_path=None):
        if not vol_path:
            vol_path = self.getVolumePath()
        return self.leaseVolumePath(vol_path)

    @classmethod
    def newVolumeLease(cls, metaId, sdUUID, volUUID):
        cls.log.debug("Initializing volume lease volUUID=%s sdUUID=%s, "
                      "metaId=%s", volUUID, sdUUID, metaId)
        volPath = metaId[0]
        leasePath = cls.leaseVolumePath(volPath)
        oop.getProcessPool(sdUUID).truncateFile(leasePath, LEASE_FILEOFFSET)
        cls.file_setrw(leasePath, rw=True)

        manifest = sdCache.produce_manifest(sdUUID)
        sanlock.write_resource(
            sdUUID.encode("utf-8"),
            volUUID.encode("utf-8"),
            [(leasePath, LEASE_FILEOFFSET)],
            align=manifest.alignment,
            sector=manifest.block_size)

    def _shareLease(self, dstImgPath):
        """
        Internal utility method used to share the template volume lease file
        with the images based on such template.
        """
        self.log.debug("Share volume lease of %s to %s", self.volUUID,
                       dstImgPath)
        dstLeasePath = self.getLeaseVolumePath(
            os.path.join(dstImgPath, self.volUUID))
        self.oop.utils.forceLink(self.getLeaseVolumePath(), dstLeasePath)

    def _share(self, dstImgPath):
        """
        Share this volume to dstImgPath, including the metadata and the lease
        """
        dstVolPath = os.path.join(dstImgPath, self.volUUID)
        dstMetaPath = self.getMetaVolumePath(dstVolPath)

        self.log.debug("Share volume %s to %s", self.volUUID, dstImgPath)
        self.oop.utils.forceLink(self.getVolumePath(), dstVolPath)

        self.log.debug("Share volume metadata of %s to %s", self.volUUID,
                       dstImgPath)
        self.oop.utils.forceLink(self.getMetaVolumePath(), dstMetaPath)

        # Link the lease file if the domain uses sanlock
        if sdCache.produce(self.sdUUID).hasVolumeLeases():
            self._shareLease(dstImgPath)

    @classmethod
    def getImageVolumes(cls, sdUUID, imgUUID):
        """
        Fetch the list of the Volumes UUIDs,
        not including the shared base (template)
        """
        sd = sdCache.produce_manifest(sdUUID)
        img_dir = sd.getImageDir(imgUUID)
        pattern = os.path.join(glob_escape(img_dir), "*.meta")
        files = oop.getProcessPool(sdUUID).glob.glob(pattern)
        volList = []
        for i in files:
            volid = os.path.splitext(os.path.basename(i))[0]
            if (sd.produceVolume(imgUUID, volid).getImage() == imgUUID):
                volList.append(volid)
        return volList

    def llPrepare(self, rw=False, setrw=False):
        """
        Make volume accessible as readonly (internal) or readwrite (leaf)
        """
        volPath = self.getVolumePath()

        # Volumes leaves created in 2.2 did not have group writeable bit
        # set. We have to set it here if we want qemu-kvm to write to old
        # NFS volumes.
        self.oop.fileUtils.copyUserModeToGroup(volPath)

        if setrw:
            self.setrw(rw=rw)
        if rw:
            if not self.oop.os.access(volPath, os.R_OK | os.W_OK):
                raise se.VolumeAccessError(volPath)
        else:
            if not self.oop.os.access(volPath, os.R_OK):
                raise se.VolumeAccessError(volPath)

    def optimal_size(self):
        """
        Return the optimal size of the volume.

        Returns:
            virtual size if format is RAW and current (apparent) size if
            format is COW.

        Note:
            the volume must be prepared when calling this helper.
        """
        if self.getFormat() == sc.RAW_FORMAT:
            return self.getCapacity()
        else:
            return self.getVolumeSize()


class FileVolume(volume.Volume):
    """ Actually represents a single volume (i.e. part of virtual disk).
    """
    manifestClass = FileVolumeManifest

    @property
    def oop(self):
        return self._manifest.oop

    # Must be class method for redirection tests.
    @classmethod
    def file_setrw(cls, volPath, rw):
        cls.manifestClass.file_setrw(volPath, rw)

    @classmethod
    def halfbakedVolumeRollback(cls, taskObj, *args):
        if len(args) == 1:  # Backward compatibility
            volPath, = args
            sdUUID = getDomUuidFromVolumePath(volPath)
        elif len(args) == 3:
            (sdUUID, volUUID, volPath) = args
        else:
            raise TypeError("halfbakedVolumeRollback takes 1 or 3 "
                            "arguments (%d given)" % len(args))

        metaVolPath = cls.manifestClass.metaVolumePath(volPath)
        cls.log.info("Halfbaked volume rollback for volPath=%s", volPath)

        if oop.getProcessPool(sdUUID).fileUtils.pathExists(volPath) and not \
                oop.getProcessPool(sdUUID).fileUtils.pathExists(metaVolPath):
            oop.getProcessPool(sdUUID).os.unlink(volPath)

    @classmethod
    def createVolumeMetadataRollback(cls, taskObj, volPath):
        cls.log.info("createVolumeMetadataRollback: volPath=%s" % (volPath))
        metaPath = cls.manifestClass.metaVolumePath(volPath)
        sdUUID = getDomUuidFromVolumePath(volPath)
        if oop.getProcessPool(sdUUID).os.path.lexists(metaPath):
            cls.log.info("Unlinking metadata volume %r", metaPath)
            oop.getProcessPool(sdUUID).os.unlink(metaPath)

    @classmethod
    def _create(cls, dom, imgUUID, volUUID, capacity, volFormat, preallocate,
                volParent, srcImgUUID, srcVolUUID, volPath, initial_size=None):
        """
        Class specific implementation of volumeCreate.
        """
        if volFormat == sc.RAW_FORMAT:
            return cls._create_raw_volume(
                dom, volUUID, capacity, volPath, initial_size, preallocate)
        else:
            return cls._create_cow_volume(
                dom, volUUID, capacity, volPath, initial_size, volParent,
                imgUUID, srcImgUUID, srcVolUUID)

    @classmethod
    def _create_raw_volume(
            cls, dom, vol_id, capacity, vol_path, initial_size, preallocate):
        """
        Specific implementation of _create() for RAW volumes.
        All the exceptions are properly handled and logged in volume.create()
        """
        if initial_size is None:
            alloc_size = capacity
        else:
            if preallocate == sc.SPARSE_VOL:
                cls.log.error("initial size is not supported for file-based "
                              "sparse volumes")
                raise se.InvalidParameterException(
                    "initial size", initial_size)

            if initial_size > capacity:
                cls.log.error("initial_size %d out of range 0-%s",
                              initial_size, capacity)
                raise se.InvalidParameterException(
                    "initial size", initial_size)

            # Always allocate at least 4k, so qemu-img can allocated the first
            # block of the image, helping qemu to probe the alignment later.
            alloc_size = max(initial_size, sc.BLOCK_SIZE_4K)

        cls._truncate_volume(vol_path, 0, vol_id, dom)

        cls._allocate_volume(vol_path, alloc_size, preallocate=preallocate)

        if alloc_size < capacity:
            qemuimg.resize(vol_path, capacity, format=qemuimg.FORMAT.RAW)

        cls.log.info("Request to create RAW volume %s with capacity = %s",
                     vol_path, capacity)

        # Forcing volume permissions in case qemu-img changed the permissions.
        cls._set_permissions(vol_path, dom)

        return (vol_path,)

    @classmethod
    def _create_cow_volume(
            cls, dom, vol_id, capacity, vol_path, initial_size, vol_parent,
            img_id, src_img_id, src_vol_id):
        """
        specific implementation of _create() for COW volumes.
        All the exceptions are properly handled and logged in volume.create()
        """
        if initial_size:
            cls.log.error("initial size is not supported "
                          "for file-based volumes")
            raise se.InvalidParameterException("initial size", initial_size)

        cls._truncate_volume(vol_path, 0, vol_id, dom)

        if not vol_parent:
            cls.log.info("Request to create COW volume %s with capacity = %s",
                         vol_path, capacity)

            operation = qemuimg.create(vol_path,
                                       size=capacity,
                                       format=sc.fmt2str(sc.COW_FORMAT),
                                       qcow2Compat=dom.qcow2_compat())
            operation.run()
        else:
            # Create hardlink to template and its meta file
            cls.log.info("Request to create snapshot %s/%s of volume %s/%s "
                         "with capacity %s",
                         img_id, vol_id, src_img_id, src_vol_id, capacity)
            vol_parent.clone(vol_path, sc.COW_FORMAT, capacity)

        # Forcing the volume permissions in case one of the tools we use
        # (dd, qemu-img, etc.) will mistakenly change the file permissions.
        cls._set_permissions(vol_path, dom)

        return (vol_path,)

    @classmethod
    def _truncate_volume(cls, vol_path, capacity, vol_id, dom):
        try:
            oop.getProcessPool(dom.sdUUID).truncateFile(
                vol_path, capacity, mode=sc.FILE_VOLUME_PERMISSIONS,
                creatExcl=True)
        except OSError as e:
            if e.errno == errno.EEXIST:
                raise se.VolumeAlreadyExists(vol_id)
            raise

    @classmethod
    def _allocate_volume(cls, vol_path, size, preallocate):
        if preallocate == sc.PREALLOCATED_VOL:
            preallocation = qemuimg.PREALLOCATION.FALLOC
        else:
            preallocation = qemuimg.PREALLOCATION.OFF

        try:
            operation = qemuimg.create(
                vol_path,
                size=size,
                format=qemuimg.FORMAT.RAW,
                preallocation=preallocation)

            with vars.task.abort_callback(operation.abort):
                with utils.stopwatch("Preallocating volume %s" % vol_path):
                    operation.run()
        except exception.ActionStopped:
            raise
        except Exception:
            cls.log.error("Unexpected error", exc_info=True)
            raise se.VolumesZeroingError(vol_path)

    @classmethod
    def _set_permissions(cls, vol_path, dom):
        cls.log.info("Changing volume %r permission to %04o",
                     vol_path, sc.FILE_VOLUME_PERMISSIONS)
        dom.oop.os.chmod(vol_path, sc.FILE_VOLUME_PERMISSIONS)

    def removeMetadata(self, metaId=None):
        self._manifest.removeMetadata()

    def delete(self, postZero, force, discard):
        """
        Delete volume.
            'postZero' - zeroing file before deletion
            'force' - required to remove shared and internal volumes
            'discard' - discard volume before deletion
        """
        self.log.info("Request to delete volume %s", self.volUUID)

        if discard:
            raise se.DiscardIsNotSupported(self.sdUUID, "file storage domain")
        vol_path = self.getVolumePath()
        lease_path = self._manifest.leaseVolumePath(vol_path)

        if not force:
            self.validateDelete()

        # Mark volume as illegal before deleting
        self.setLegality(sc.ILLEGAL_VOL)

        # try to cleanup as much as possible
        eFound = se.CannotDeleteVolume(self.volUUID)
        puuid = None
        try:
            # We need to blank parent record in our metadata
            # for parent to become leaf successfully.
            puuid = self.getParent()
            self.setParent(sc.BLANK_UUID)
            if puuid and puuid != sc.BLANK_UUID:
                pvol = FileVolume(self.repoPath, self.sdUUID,
                                  self.imgUUID, puuid)
                pvol.recheckIfLeaf()
        except Exception as e:
            eFound = e
            self.log.warning("cannot finalize parent volume %s",
                             puuid, exc_info=True)

        try:
            self.oop.utils.rmFile(vol_path)
            self.oop.utils.rmFile(lease_path)
        except Exception as e:
            eFound = e
            self.log.error("cannot delete volume %s at path: %s", self.volUUID,
                           vol_path, exc_info=True)

        try:
            self.removeMetadata()
            return True
        except Exception as e:
            eFound = e
            self.log.error("cannot remove volume's %s metadata",
                           self.volUUID, exc_info=True)

        raise eFound

    @classmethod
    def shareVolumeRollback(cls, taskObj, volPath):
        cls.log.info("Volume rollback for volPath=%s", volPath)
        procPool = oop.getProcessPool(getDomUuidFromVolumePath(volPath))
        procPool.utils.rmFile(volPath)
        procPool.utils.rmFile(cls.manifestClass.metaVolumePath(volPath))
        procPool.utils.rmFile(cls.manifestClass.leaseVolumePath(volPath))

    def setParentMeta(self, puuid):
        """
        Set parent volume UUID in Volume metadata.  This operation can be done
        by an HSM while it is using the volume and by an SPM when no one is
        using the volume.
        """
        self.setMetaParam(sc.PUUID, puuid)

    def setParentTag(self, puuid):
        """
        For file volumes we do not use any LV tags
        """
        pass

    @classmethod
    def renameVolumeRollback(cls, taskObj, oldPath, newPath):
        try:
            cls.log.info("oldPath=%s newPath=%s", oldPath, newPath)
            sdUUID = getDomUuidFromVolumePath(oldPath)
            oop.getProcessPool(sdUUID).os.rename(oldPath, newPath)
        except Exception:
            cls.log.error("Could not rollback "
                          "volume rename (oldPath=%s newPath=%s)",
                          oldPath, newPath, exc_info=True)

    def rename(self, newUUID, recovery=True):
        """
        Rename volume
        """
        self.log.info("Rename volume %s as %s ", self.volUUID, newUUID)
        if not self.imagePath:
            self._manifest.validateImagePath()
        volPath = os.path.join(self.imagePath, newUUID)
        metaPath = self.getMetaVolumePath(volPath)
        prevMetaPath = self.getMetaVolumePath()
        leasePath = self.getLeaseVolumePath(volPath)
        prevLeasePath = self.getLeaseVolumePath()

        if recovery:
            name = "Rename volume rollback: " + volPath
            vars.task.pushRecovery(task.Recovery(name, "fileVolume",
                                                 "FileVolume",
                                                 "renameVolumeRollback",
                                                 [volPath, self.volumePath]))
        self.log.info("Renaming %s to %s", self.volumePath, volPath)
        self.oop.os.rename(self.volumePath, volPath)
        if recovery:
            name = "Rename meta-volume rollback: " + metaPath
            vars.task.pushRecovery(task.Recovery(name, "fileVolume",
                                                 "FileVolume",
                                                 "renameVolumeRollback",
                                                 [metaPath, prevMetaPath]))
        self.log.info("Renaming %s to %s", prevMetaPath, metaPath)
        self.oop.os.rename(prevMetaPath, metaPath)
        if recovery:
            name = "Rename lease-volume rollback: " + leasePath
            vars.task.pushRecovery(task.Recovery(name, "fileVolume",
                                                 "FileVolume",
                                                 "renameVolumeRollback",
                                                 [leasePath, prevLeasePath]))
        self.log.info("Renaming %s to %s", prevLeasePath, leasePath)
        try:
            self.oop.os.rename(prevLeasePath, leasePath)
        except OSError as e:
            if e.errno != errno.ENOENT:
                raise

        self.renameLease((volPath, LEASE_FILEOFFSET), newUUID,
                         recovery=recovery)

        self._manifest.volUUID = newUUID
        self._manifest.volumePath = volPath

    def getMetaVolumePath(self, vol_path=None):
        # pylint: disable=no-member
        return self._manifest.getMetaVolumePath(vol_path)

    def getLeaseVolumePath(self, vol_path=None):
        # pylint: disable=no-member
        return self._manifest.getLeaseVolumePath(vol_path)

    def _extendSizeRaw(self, new_capacity):
        volPath = self.getVolumePath()
        cur_capacity = self.oop.os.stat(volPath).st_size

        # No real sanity checks here, they should be included in the calling
        # function/method. We just validate the sizes to be consistent since
        # they're computed and used in the pre-allocated case.
        if new_capacity == cur_capacity:
            return  # Nothing to do
        elif cur_capacity <= 0:
            raise se.StorageException(
                "Volume capacity is impossible: %s" % cur_capacity)
        elif new_capacity < cur_capacity:
            raise se.VolumeResizeValueError(new_capacity)

        if self.getType() == sc.PREALLOCATED_VOL:
            self.log.info("Preallocating volume %s to %s",
                          volPath, new_capacity)
            operation = fallocate.allocate(volPath,
                                           new_capacity - cur_capacity,
                                           cur_capacity)
            with vars.task.abort_callback(operation.abort):
                with utils.stopwatch("Preallocating volume %s" % volPath):
                    operation.run()
        else:
            # for sparse files we can just truncate to the correct size
            # also good fallback for failed preallocation
            self.log.info("Truncating volume %s to %s",
                          volPath, new_capacity)
            self.oop.truncateFile(volPath, new_capacity)
