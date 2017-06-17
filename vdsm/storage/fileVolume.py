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

import errno
import os

from vdsm import constants
from vdsm import qemuimg
from vdsm import utils
from vdsm.commands import grepCmd
from vdsm.common import exception
from vdsm.common.threadlocal import vars
from vdsm.storage import constants as sc
from vdsm.storage import exception as se
from vdsm.storage import outOfProcess as oop
from vdsm.storage import task
from vdsm.storage import fallocate
from vdsm.storage.compat import sanlock
from vdsm.storage.misc import deprecated
from vdsm.storage.volumemetadata import VolumeMetadata

from sdc import sdCache
import volume
import sd
import fileSD

META_FILEEXT = ".meta"
LEASE_FILEOFFSET = 0

BLOCK_SIZE = sc.BLOCK_SIZE


def getDomUuidFromVolumePath(volPath):
    # fileVolume path has pattern:
    # */sdUUID/images/imgUUID/volUUID
    sdPath = os.path.normpath(volPath).split('/images')[0]
    target, sdUUID = os.path.split(sdPath)
    return sdUUID


class FileVolumeManifest(volume.VolumeManifest):

    # Raw volumes should be aligned to sector size, which is 512 or 4096
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

    def _getMetaVolumePath(self, vol_path=None):
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
        metaVolumePath = self._getMetaVolumePath()
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
        domainPath = os.path.join(self.repoPath, self.sdUUID)
        if not fileSD.FileStorageDomainManifest(domainPath).isISO():
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
        metaPath = self._getMetaVolumePath(volPath)

        try:
            lines = self.oop.directReadLines(metaPath)
        except Exception as e:
            self.log.error(e, exc_info=True)
            raise se.VolumeMetadataReadError("%s: %s" % (metaId, e))

        md = VolumeMetadata.from_lines(lines)
        return md.legacy_info()

    def getParent(self):
        """
        Return parent volume UUID
        """
        return self.getMetaParam(sc.PUUID)

    def getChildren(self):
        """ Return children volume UUIDs.

        This API is not suitable for use with a template's base volume.
        """
        domPath = self.imagePath.split('images')[0]
        metaPattern = os.path.join(domPath, 'images', self.imgUUID, '*.meta')
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

    def getVolumeSize(self, bs=BLOCK_SIZE):
        """
        Return the volume size in blocks
        """
        volPath = self.getVolumePath()
        return int(int(self.oop.os.stat(volPath).st_size) / bs)

    def getVolumeTrueSize(self, bs=BLOCK_SIZE):
        """
        Return the size of the storage allocated for this volume
        on underlying storage
        """
        volPath = self.getVolumePath()
        return int(int(self.oop.os.stat(volPath).st_blocks) * BLOCK_SIZE / bs)

    def setMetadata(self, meta, metaId=None):
        """
        Set the meta data hash as the new meta data of the Volume
        """
        if not metaId:
            metaId = self.getMetadataId()

        try:
            self._putMetadata(metaId, meta)
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
    def _putMetadata(cls, metaId, meta):
        volPath, = metaId
        metaPath = cls.metaVolumePath(volPath)

        data = cls.formatMetadata(meta)

        with open(metaPath + ".new", "w") as f:
            f.write(data)

        sdUUID = getDomUuidFromVolumePath(volPath)
        oop.getProcessPool(sdUUID).os.rename(metaPath + ".new", metaPath)

    def setImage(self, imgUUID):
        """
        Set image UUID
        """
        self.setMetaParam(sc.IMAGE, imgUUID)

    def removeMetadata(self, metaId=None):
        """
        Remove the meta file
        """
        metaPath = self._getMetaVolumePath()
        if self.oop.os.path.lexists(metaPath):
            self.log.debug("Removing: %s", metaPath)
            self.oop.os.unlink(metaPath)

    @classmethod
    def leaseVolumePath(cls, vol_path):
        if vol_path:
            return vol_path + sc.LEASE_FILEEXT
        else:
            return None

    def _getLeaseVolumePath(self, vol_path=None):
        if not vol_path:
            vol_path = self.getVolumePath()
        return self.leaseVolumePath(vol_path)

    @classmethod
    def newVolumeLease(cls, metaId, sdUUID, volUUID):
        cls.log.debug("Initializing volume lease volUUID=%s sdUUID=%s, "
                      "metaId=%s", volUUID, sdUUID, metaId)
        volPath, = metaId
        leasePath = cls.leaseVolumePath(volPath)
        oop.getProcessPool(sdUUID).truncateFile(leasePath, LEASE_FILEOFFSET)
        cls.file_setrw(leasePath, rw=True)
        sanlock.init_resource(sdUUID, volUUID, [(leasePath,
                                                 LEASE_FILEOFFSET)])

    def _shareLease(self, dstImgPath):
        """
        Internal utility method used to share the template volume lease file
        with the images based on such template.
        """
        self.log.debug("Share volume lease of %s to %s", self.volUUID,
                       dstImgPath)
        dstLeasePath = self._getLeaseVolumePath(
            os.path.join(dstImgPath, self.volUUID))
        self.oop.utils.forceLink(self._getLeaseVolumePath(), dstLeasePath)

    def _share(self, dstImgPath):
        """
        Share this volume to dstImgPath, including the metadata and the lease
        """
        dstVolPath = os.path.join(dstImgPath, self.volUUID)
        dstMetaPath = self._getMetaVolumePath(dstVolPath)

        self.log.debug("Share volume %s to %s", self.volUUID, dstImgPath)
        self.oop.utils.forceLink(self.getVolumePath(), dstVolPath)

        self.log.debug("Share volume metadata of %s to %s", self.volUUID,
                       dstImgPath)
        self.oop.utils.forceLink(self._getMetaVolumePath(), dstMetaPath)

        # Link the lease file if the domain uses sanlock
        if sdCache.produce(self.sdUUID).hasVolumeLeases():
            self._shareLease(dstImgPath)

    @classmethod
    def getImageVolumes(cls, repoPath, sdUUID, imgUUID):
        """
        Fetch the list of the Volumes UUIDs,
        not including the shared base (template)
        """
        # Get Volumes of an image
        pattern = os.path.join(repoPath, sdUUID, sd.DOMAIN_IMAGES,
                               imgUUID, "*.meta")
        files = oop.getProcessPool(sdUUID).glob.glob(pattern)
        volList = []
        for i in files:
            volid = os.path.splitext(os.path.basename(i))[0]
            if (sdCache.produce(sdUUID).
                    produceVolume(imgUUID, volid).
                    getImage() == imgUUID):
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
            the volume must be prepared must be prepared when calling this
            helper.
        """
        if self.getFormat() == sc.RAW_FORMAT:
            return self.getSize() * sc.BLOCK_SIZE
        else:
            return self.getVolumeSize() * sc.BLOCK_SIZE


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
            oop.getProcessPool(sdUUID).os.unlink(metaPath)

    @classmethod
    def _create(cls, dom, imgUUID, volUUID, size, volFormat, preallocate,
                volParent, srcImgUUID, srcVolUUID, volPath,
                initialSize=None):
        """
        Class specific implementation of volumeCreate. All the exceptions are
        properly handled and logged in volume.create()
        """
        if initialSize:
            cls.log.error("initialSize is not supported for file-based "
                          "volumes")
            raise se.InvalidParameterException("initial size",
                                               initialSize)

        sizeBytes = size * BLOCK_SIZE
        truncSize = sizeBytes if volFormat == sc.RAW_FORMAT else 0

        try:
            oop.getProcessPool(dom.sdUUID).truncateFile(
                volPath, truncSize, mode=sc.FILE_VOLUME_PERMISSIONS,
                creatExcl=True)
        except OSError as e:
            if e.errno == errno.EEXIST:
                raise se.VolumeAlreadyExists(volUUID)
            raise

        if preallocate == sc.PREALLOCATED_VOL:
            try:
                operation = fallocate.allocate(volPath,
                                               sizeBytes)
                with vars.task.abort_callback(operation.abort):
                    with utils.stopwatch("Preallocating volume %s" % volPath):
                        operation.run()
            except exception.ActionStopped:
                raise
            except Exception:
                cls.log.error("Unexpected error", exc_info=True)
                raise se.VolumesZeroingError(volPath)

        if not volParent:
            cls.log.info("Request to create %s volume %s with size = %s "
                         "sectors", sc.type2name(volFormat), volPath,
                         size)
            if volFormat == sc.COW_FORMAT:
                qemuimg.create(volPath,
                               size=sizeBytes,
                               format=sc.fmt2str(volFormat),
                               qcow2Compat=dom.qcow2_compat())
        else:
            # Create hardlink to template and its meta file
            cls.log.info("Request to create snapshot %s/%s of volume %s/%s",
                         imgUUID, volUUID, srcImgUUID, srcVolUUID)
            volParent.clone(volPath, volFormat)

        # Forcing the volume permissions in case one of the tools we use
        # (dd, qemu-img, etc.) will mistakenly change the file permissiosn.
        dom.oop.os.chmod(volPath, sc.FILE_VOLUME_PERMISSIONS)

        return (volPath,)

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
        metaPath = self._getMetaVolumePath(volPath)
        prevMetaPath = self._getMetaVolumePath()
        leasePath = self._getLeaseVolumePath(volPath)
        prevLeasePath = self._getLeaseVolumePath()

        if recovery:
            name = "Rename volume rollback: " + volPath
            vars.task.pushRecovery(task.Recovery(name, "fileVolume",
                                                 "FileVolume",
                                                 "renameVolumeRollback",
                                                 [volPath, self.volumePath]))
        self.log.debug("Renaming %s to %s", self.volumePath, volPath)
        self.oop.os.rename(self.volumePath, volPath)
        if recovery:
            name = "Rename meta-volume rollback: " + metaPath
            vars.task.pushRecovery(task.Recovery(name, "fileVolume",
                                                 "FileVolume",
                                                 "renameVolumeRollback",
                                                 [metaPath, prevMetaPath]))
        self.log.debug("Renaming %s to %s", prevMetaPath, metaPath)
        self.oop.os.rename(prevMetaPath, metaPath)
        if recovery:
            name = "Rename lease-volume rollback: " + leasePath
            vars.task.pushRecovery(task.Recovery(name, "fileVolume",
                                                 "FileVolume",
                                                 "renameVolumeRollback",
                                                 [leasePath, prevLeasePath]))
        self.log.debug("Renaming %s to %s", prevLeasePath, leasePath)
        try:
            self.oop.os.rename(prevLeasePath, leasePath)
        except OSError as e:
            if e.errno != os.errno.ENOENT:
                raise
        self._manifest.volUUID = newUUID
        self._manifest.volumePath = volPath

    def _getMetaVolumePath(self, vol_path=None):
        return self._manifest._getMetaVolumePath(vol_path)

    def _getLeaseVolumePath(self, vol_path=None):
        return self._manifest._getLeaseVolumePath(vol_path)

    def _extendSizeRaw(self, newSize):
        volPath = self.getVolumePath()
        curSizeBytes = self.oop.os.stat(volPath).st_size
        newSizeBytes = newSize * BLOCK_SIZE

        # No real sanity checks here, they should be included in the calling
        # function/method. We just validate the sizes to be consistent since
        # they're computed and used in the pre-allocated case.
        if newSizeBytes == curSizeBytes:
            return  # Nothing to do
        elif curSizeBytes <= 0:
            raise se.StorageException(
                "Volume size is impossible: %s" % curSizeBytes)
        elif newSizeBytes < curSizeBytes:
            raise se.VolumeResizeValueError(newSize)

        if self.getType() == sc.PREALLOCATED_VOL:
            self.log.info("Preallocating volume %s to %s bytes",
                          volPath, newSizeBytes)
            operation = fallocate.allocate(volPath,
                                           newSizeBytes - curSizeBytes,
                                           curSizeBytes)
            with vars.task.abort_callback(operation.abort):
                with utils.stopwatch("Preallocating volume %s" % volPath):
                    operation.run()
        else:
            # for sparse files we can just truncate to the correct size
            # also good fallback for failed preallocation
            self.log.info("Truncating volume %s to %s bytes",
                          volPath, newSizeBytes)
            self.oop.truncateFile(volPath, newSizeBytes)
