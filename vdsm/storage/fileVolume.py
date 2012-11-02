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
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA
#
# Refer to the README and COPYING files for full details of the license
#

from os.path import normpath
import os
import sanlock

import storage_exception as se
from vdsm.config import config
from sdc import sdCache
import outOfProcess as oop
import volume
import image
import sd
import misc
from misc import deprecated
import task
from threadLocal import vars

LEASE_FILEEXT = ".lease"
LEASE_FILEOFFSET = 0


def getDomUuidFromVolumePath(volPath):
    # Volume path has pattern:
    #  /rhev/data-center/spUUID/sdUUID/images/imgUUID/volUUID

    # sdUUID position after data-center
    sdUUIDPos = 3

    volList = volPath.split('/')
    sdUUID = len(normpath(config.get('irs', 'repository')).split('/')) + \
             sdUUIDPos
    return volList[sdUUID]


def deleteMultipleVolumes(sdUUID, volumes, postZero):
    #Posix asserts that the blocks will be zeroed before reuse
    volPaths = []
    for vol in volumes:
        vol.setLegality(volume.ILLEGAL_VOL)
        volPaths.append(vol.getVolumePath())
    try:
        oop.getGlobalProcPool().fileUtils.cleanupfiles(volPaths)
    except OSError:
        volume.log.error("cannot delete some volumes at paths: %s",
                            volPaths, exc_info=True)


class FileVolume(volume.Volume):
    """ Actually represents a single volume (i.e. part of virtual disk).
    """
    def __init__(self, repoPath, sdUUID, imgUUID, volUUID):
        volume.Volume.__init__(self, repoPath, sdUUID, imgUUID, volUUID)

    @property
    def oop(self):
        return oop.getProcessPool(self.sdUUID)

    @staticmethod
    def file_setrw(volPath, rw):
        sdUUID = getDomUuidFromVolumePath(volPath)
        mode = 0440
        if rw:
            mode |= 0220
        if oop.getProcessPool(sdUUID).os.path.isdir(volPath):
            mode |= 0110
        oop.getProcessPool(sdUUID).os.chmod(volPath, mode)

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

        cls.log.info("Halfbaked volume rollback for volPath=%s", volPath)

        if oop.getProcessPool(sdUUID).fileUtils.pathExists(volPath):
            oop.getProcessPool(sdUUID).os.unlink(volPath)

    @classmethod
    def validateCreateVolumeParams(cls, volFormat, preallocate, srcVolUUID):
        """
        Validate create volume parameters.
        'srcVolUUID' - backing volume UUID
        'volFormat' - volume format RAW/QCOW2
        'preallocate' - sparse/preallocate
        """
        volume.Volume.validateCreateVolumeParams(volFormat, preallocate,
                                                 srcVolUUID)

        # Snapshot should be COW volume
        if srcVolUUID != volume.BLANK_UUID and volFormat != volume.COW_FORMAT:
            raise se.IncorrectFormat(srcVolUUID)

    @classmethod
    def createVolumeMetadataRollback(cls, taskObj, volPath):
        cls.log.info("createVolumeMetadataRollback: volPath=%s" % (volPath))
        metaPath = cls.__metaVolumePath(volPath)
        sdUUID = getDomUuidFromVolumePath(volPath)
        if oop.getProcessPool(sdUUID).os.path.lexists(metaPath):
            oop.getProcessPool(sdUUID).os.unlink(metaPath)

    @classmethod
    def _create(cls, dom, imgUUID, volUUID, size, volFormat, preallocate,
                volParent, srcImgUUID, srcVolUUID, imgPath, volPath):
        """
        Class specific implementation of volumeCreate. All the exceptions are
        properly handled and logged in volume.create()
        """

        sizeBytes = int(size) * 512

        if preallocate == volume.SPARSE_VOL:
            # Sparse = regular file
            oop.getProcessPool(dom.sdUUID).createSparseFile(volPath, sizeBytes)
        else:
            try:
                # ddWatchCopy expects size to be in bytes
                misc.ddWatchCopy("/dev/zero", volPath,
                                 vars.task.aborting, sizeBytes)
            except se.ActionStopped, e:
                raise e
            except Exception, e:
                cls.log.error("Unexpected error", exc_info=True)
                raise se.VolumesZeroingError(volPath)

        if not volParent:
            cls.log.info("Request to create %s volume %s with size = %s "
                         "sectors", volume.type2name(volFormat), volPath,
                         size)

            if volFormat == volume.COW_FORMAT:
                volume.createVolume(None, None, volPath, size, volFormat,
                                    preallocate)
        else:
            # Create hardlink to template and its meta file
            cls.log.info("Request to create snapshot %s/%s of volume %s/%s",
                         imgUUID, volUUID, srcImgUUID, srcVolUUID)
            volParent.clone(imgPath, volUUID, volFormat, preallocate)

        # By definition the volume is a leaf
        cls.file_setrw(volPath, rw=True)

        return (volPath,)

    def delete(self, postZero, force):
        """
        Delete volume.
            'postZero' - zeroing file before deletion
            'force' - required to remove shared and internal volumes
        """
        self.log.info("Request to delete volume %s", self.volUUID)

        vol_path = self.getVolumePath()
        lease_path = self.__leaseVolumePath(vol_path)

        if not force:
            self.validateDelete()

        # Mark volume as illegal before deleting
        self.setLegality(volume.ILLEGAL_VOL)

        # try to cleanup as much as possible
        eFound = se.CannotDeleteVolume(self.volUUID)
        puuid = None
        try:
            # We need to blank parent record in our metadata
            # for parent to become leaf successfully.
            puuid = self.getParent()
            self.setParent(volume.BLANK_UUID)
            if puuid and puuid != volume.BLANK_UUID:
                pvol = FileVolume(self.repoPath, self.sdUUID,
                                  self.imgUUID, puuid)
                pvol.recheckIfLeaf()
        except Exception, e:
            eFound = e
            self.log.warning("cannot finalize parent volume %s",
                             puuid, exc_info=True)

        try:
            self.oop.fileUtils.cleanupfiles([vol_path, lease_path])
        except Exception, e:
            eFound = e
            self.log.error("cannot delete volume %s at path: %s", self.volUUID,
                            vol_path, exc_info=True)

        try:
            self.removeMetadata()
            return True
        except Exception, e:
            eFound = e
            self.log.error("cannot remove volume's %s metadata",
                           self.volUUID, exc_info=True)

        raise eFound

    def getDevPath(self):
        """
        Return the underlying device (for sharing)
        """
        return self.getVolumePath()

    def _shareLease(self, dstImgPath):
        """
        Internal utility method used to share the template volume lease file
        with the images based on such template.
        """
        self.log.debug("Share volume lease of %s to %s", self.volUUID,
                       dstImgPath)
        dstLeasePath = self._getLeaseVolumePath(
                                os.path.join(dstImgPath, self.volUUID))
        self.oop.fileUtils.safeUnlink(dstLeasePath)
        self.oop.os.link(self._getLeaseVolumePath(), dstLeasePath)

    def _share(self, dstImgPath):
        """
        Share this volume to dstImgPath, including the metadata and the lease
        """
        dstVolPath = os.path.join(dstImgPath, self.volUUID)
        dstMetaPath = self._getMetaVolumePath(dstVolPath)

        self.log.debug("Share volume %s to %s", self.volUUID, dstImgPath)

        self.oop.fileUtils.safeUnlink(dstVolPath)
        self.oop.os.link(self.getVolumePath(), dstVolPath)

        self.log.debug("Share volume metadata of %s to %s", self.volUUID,
                       dstImgPath)

        self.oop.fileUtils.safeUnlink(dstMetaPath)
        self.oop.os.link(self._getMetaVolumePath(), dstMetaPath)

        # Link the lease file if the domain uses sanlock
        if sdCache.produce(self.sdUUID).hasVolumeLeases():
            self._shareLease(dstImgPath)

    @classmethod
    def shareVolumeRollback(cls, taskObj, volPath):
        cls.log.info("Volume rollback for volPath=%s", volPath)

        try:
            procPool = oop.getProcessPool(getDomUuidFromVolumePath(volPath))
            procPool.fileUtils.safeUnlink(volPath)
            procPool.fileUtils.safeUnlink(cls.__metaVolumePath(volPath))
            procPool.fileUtils.safeUnlink(cls.__leaseVolumePath(volPath))

        except Exception:
            cls.log.error("Unexpected error", exc_info=True)

    @deprecated  # valid only for domain version < 3, see volume.setrw
    def _setrw(self, rw):
        """
        Set the read/write permission on the volume (deprecated)
        """
        self.file_setrw(self.getVolumePath(), rw=rw)

    def llPrepare(self, rw=False, setrw=False):
        """
        Make volume accessible as readonly (internal) or readwrite (leaf)
        """
        def copyUserModeToGroup(path):
            # Volumes leaves created in 2.2 did not have group writeable bit
            # set. We have to set it here if we want qemu-kvm to write to old
            # NFS volumes.
            mode = self.oop.os.stat(path).st_mode
            usrmode = (mode & 0700) >> 3
            grpmode = mode & 0070
            if usrmode & grpmode != usrmode:
                mode |= usrmode
                self.oop.os.chmod(path, mode)

        volPath = self.getVolumePath()

        copyUserModeToGroup(volPath)

        if setrw:
            self.setrw(rw=rw)
        if rw:
            if not self.oop.os.access(volPath, os.R_OK | os.W_OK):
                raise se.VolumeAccessError(volPath)
        else:
            if not self.oop.os.access(volPath, os.R_OK):
                raise se.VolumeAccessError(volPath)

    def removeMetadata(self):
        """
        Remove the meta file
        """
        metaPath = self._getMetaVolumePath()
        if self.oop.os.path.lexists(metaPath):
            self.oop.os.unlink(metaPath)

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
            f = self.oop.directReadLines(metaPath)
            out = {}
            for l in f:
                if l.startswith("EOF"):
                    return out
                if l.find("=") < 0:
                    continue
                key, value = l.split("=")
                out[key.strip()] = value.strip()

        except Exception, e:
            self.log.error(e, exc_info=True)
            raise se.VolumeMetadataReadError("%s: %s" % (metaId, e))

        return out

    @classmethod
    def __putMetadata(cls, metaId, meta):
        volPath, = metaId
        metaPath = cls.__metaVolumePath(volPath)

        with open(metaPath + ".new", "w") as f:
            for key, value in meta.iteritems():
                f.write("%s=%s\n" % (key.strip(), str(value).strip()))
            f.write("EOF\n")

        sdUUID = getDomUuidFromVolumePath(volPath)
        oop.getProcessPool(sdUUID).os.rename(metaPath + ".new", metaPath)

    @classmethod
    def createMetadata(cls, metaId, meta):
        cls.__putMetadata(metaId, meta)

    def setMetadata(self, meta, metaId=None):
        """
        Set the meta data hash as the new meta data of the Volume
        """
        if not metaId:
            metaId = self.getMetadataId()

        try:
            self.__putMetadata(metaId, meta)
        except Exception, e:
            self.log.error(e, exc_info=True)
            raise se.VolumeMetadataWriteError(str(metaId) + str(e))

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

    @classmethod
    def newVolumeLease(cls, metaId, sdUUID, volUUID):
        cls.log.debug("Initializing volume lease volUUID=%s sdUUID=%s, "
                      "metaId=%s", volUUID, sdUUID, metaId)
        volPath, = metaId
        leasePath = cls.__leaseVolumePath(volPath)
        oop.getProcessPool(sdUUID).createSparseFile(leasePath,
                                                    LEASE_FILEOFFSET)
        cls.file_setrw(leasePath, rw=True)
        sanlock.init_resource(sdUUID, volUUID, [(leasePath,
                                                 LEASE_FILEOFFSET)])

    def findImagesByVolume(self, legal=False):
        """
        Find the image(s) UUID by one of its volume UUID.
        Templated and shared disks volumes may result more then one image.
        """
        try:
            pattern = os.path.join(self.repoPath, self.sdUUID,
                                   sd.DOMAIN_IMAGES, "*", self.volUUID)
            vollist = self.oop.glob.glob(pattern)
            for vol in vollist[:]:
                img = os.path.basename(os.path.dirname(vol))
                if img.startswith(image.REMOVED_IMAGE_PREFIX):
                    vollist.remove(vol)
        except Exception, e:
            self.log.info("Volume %s does not exists." % (self.volUUID))
            raise se.VolumeDoesNotExist("%s: %s:" % (self.volUUID, e))

        imglist = [os.path.basename(os.path.dirname(vol)) for vol in vollist]

        # Check image legality, if needed
        if legal:
            for img in imglist[:]:
                if not image.Image(self.repoPath).isLegal(self.sdUUID, img):
                    imglist.remove(img)

        return imglist

    def getParent(self):
        """
        Return parent volume UUID
        """
        return self.getMetaParam(volume.PUUID)

    def getImage(self):
        """
        Return image UUID
        """
        return self.getMetaParam(volume.IMAGE)

    def setParent(self, puuid):
        """
        Set parent volume UUID
        """
        self.setMetaParam(volume.PUUID, puuid)

    def setImage(self, imgUUID):
        """
        Set image UUID
        """
        self.setMetaParam(volume.IMAGE, imgUUID)

    @classmethod
    def getVSize(cls, sdobj, imgUUID, volUUID, bs=512):
        imagePath = image.Image(sdobj._getRepoPath()).getImageDir(
                                                    sdobj.sdUUID, imgUUID)
        volPath = os.path.join(imagePath, volUUID)
        return int(sdobj.oop.os.stat(volPath).st_size / bs)

    @classmethod
    def getVTrueSize(cls, sdobj, imgUUID, volUUID, bs=512):
        return sdobj.produceVolume(imgUUID, volUUID).getVolumeTrueSize(bs)

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
            self.validateImagePath()
        volPath = os.path.join(self.imagePath, newUUID)
        metaPath = self._getMetaVolumePath(volPath)
        prevMetaPath = self._getMetaVolumePath()

        if recovery:
            name = "Rename volume rollback: " + volPath
            vars.task.pushRecovery(task.Recovery(name, "fileVolume",
                                                 "FileVolume",
                                                 "renameVolumeRollback",
                                                 [volPath, self.volumePath]))
        self.oop.os.rename(self.volumePath, volPath)
        if recovery:
            name = "Rename meta-volume rollback: " + metaPath
            vars.task.pushRecovery(task.Recovery(name, "fileVolume",
                                                 "FileVolume",
                                                 "renameVolumeRollback",
                                                 [metaPath, prevMetaPath]))
        self.oop.os.rename(prevMetaPath, metaPath)
        self.volUUID = newUUID
        self.volumePath = volPath

    def validateImagePath(self):
        """
        Validate that the image dir exists and valid.
        In the file volume repositories,
        the image dir must exists after creation its first volume.
        """
        imageDir = image.Image(self.repoPath).getImageDir(self.sdUUID,
                                                          self.imgUUID)
        if not self.oop.os.path.isdir(imageDir):
            raise se.ImagePathError(imageDir)
        if not self.oop.os.access(imageDir, os.R_OK | os.W_OK | os.X_OK):
            raise se.ImagePathError(imageDir)
        self.imagePath = imageDir

    @classmethod
    def __metaVolumePath(cls, vol_path):
        if vol_path:
            return vol_path + '.meta'
        else:
            return None

    @classmethod
    def __leaseVolumePath(cls, vol_path):
        if vol_path:
            return vol_path + LEASE_FILEEXT
        else:
            return None

    def _getMetaVolumePath(self, vol_path=None):
        """
        Get the volume metadata file/link path
        """
        if not vol_path:
            vol_path = self.getVolumePath()
        return type(self).__metaVolumePath(vol_path)

    def _getLeaseVolumePath(self, vol_path=None):
        """
        Get the volume lease file/link path
        """
        if not vol_path:
            vol_path = self.getVolumePath()
        return type(self).__leaseVolumePath(vol_path)

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

        self.volumePath = volPath
        if not sdCache.produce(self.sdUUID).isISO():
            self.validateMetaVolumePath()

    def validateMetaVolumePath(self):
        """
        In file volume repositories,
        the volume metadata must exists after the image/volume is created.
        """
        metaVolumePath = self._getMetaVolumePath()
        if not self.oop.fileUtils.pathExists(metaVolumePath):
            raise se.VolumeDoesNotExist(self.volUUID)

    def getVolumeSize(self, bs=512):
        """
        Return the volume size in blocks
        """
        volPath = self.getVolumePath()
        return int(int(self.oop.os.stat(volPath).st_size) / bs)

    def getVolumeTrueSize(self, bs=512):
        """
        Return the size of the storage allocated for this volume
        on underlying storage
        """
        volPath = self.getVolumePath()
        return int(int(self.oop.os.stat(volPath).st_blocks) * 512 / bs)

    def getVolumeMtime(self):
        """
        Return the volume mtime in msec epoch
        """
        volPath = self.getVolumePath()
        try:
            return self.getMetaParam(volume.MTIME)
        except se.MetaDataKeyNotFoundError:
            return self.oop.os.stat(volPath).st_mtime
