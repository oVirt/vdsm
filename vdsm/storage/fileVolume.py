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

from os.path import normpath
import os
import uuid
import sanlock

import storage_exception as se
from config import config
from sdc import sdCache
import outOfProcess as oop
import volume
import image
import sd
import misc
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
    sdUUID = len(normpath(config.get('irs', 'repository')).split('/')) + sdUUIDPos
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
    def halfbakedVolumeRollback(cls, taskObj, volPath):
        cls.log.info("halfbakedVolumeRollback: volPath=%s" % (volPath))
        sdUUID = getDomUuidFromVolumePath(volPath)
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
        volume.Volume.validateCreateVolumeParams(volFormat, preallocate, srcVolUUID)

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
    def create(cls, repoPath, sdUUID, imgUUID, size, volFormat, preallocate, diskType, volUUID, desc, srcImgUUID, srcVolUUID):
        """
        Create a new volume with given size or snapshot
            'size' - in sectors
            'volFormat' - volume format COW / RAW
            'preallocate' - Prealocate / Sparse
            'diskType' - string that describes disk type System|Data|Shared|Swap|Temp
            'srcImgUUID' - source image UUID
            'srcVolUUID' - source volume UUID
        """
        if not volUUID:
            volUUID = str(uuid.uuid4())
        if volUUID == volume.BLANK_UUID:
            raise se.InvalidParameterException("volUUID", volUUID)

        # Validate volume parameters should be checked here for all
        # internal flows using volume creation.
        cls.validateCreateVolumeParams(volFormat, preallocate, srcVolUUID)

        imageDir = image.Image(repoPath).create(sdUUID, imgUUID)
        vol_path = os.path.join(imageDir, volUUID)
        voltype = "LEAF"
        pvol = None
        # Check if volume already exists
        if oop.getProcessPool(sdUUID).fileUtils.pathExists(vol_path):
            raise se.VolumeAlreadyExists(vol_path)
        # Check if snapshot creation required
        if srcVolUUID != volume.BLANK_UUID:
            if srcImgUUID == volume.BLANK_UUID:
                srcImgUUID = imgUUID
            pvol = FileVolume(repoPath, sdUUID, srcImgUUID, srcVolUUID)
            # Cannot create snapshot for ILLEGAL volume
            if not pvol.isLegal():
                raise se.createIllegalVolumeSnapshotError(pvol.volUUID)

        # Rollback sentinel, just to mark the start of the task
        vars.task.pushRecovery(task.Recovery(task.ROLLBACK_SENTINEL, "fileVolume", "FileVolume", "startCreateVolumeRollback",
                                             [sdUUID, imgUUID, volUUID]))
        # create volume rollback
        vars.task.pushRecovery(task.Recovery("halfbaked volume rollback", "fileVolume", "FileVolume", "halfbakedVolumeRollback",
                                             [vol_path]))
        if preallocate == volume.PREALLOCATED_VOL:
            try:
                # ddWatchCopy expects size to be in bytes
                misc.ddWatchCopy("/dev/zero", vol_path, vars.task.aborting, (int(size) * 512))
            except se.ActionStopped, e:
                raise e
            except Exception, e:
                cls.log.error("Unexpected error", exc_info=True)
                raise se.VolumesZeroingError(vol_path)
        else:
            # Sparse = Normal file
            oop.getProcessPool(sdUUID).createSparseFile(vol_path, 0)

        cls.log.info("fileVolume: create: volUUID %s srcImg %s srvVol %s" % (volUUID, srcImgUUID, srcVolUUID))
        if not pvol:
            cls.log.info("Request to create %s volume %s with size = %s sectors",
                     volume.type2name(volFormat), vol_path, size)
            # Create preallocate/raw volume via qemu-img actually redundant
            if preallocate == volume.SPARSE_VOL or volFormat == volume.COW_FORMAT:
                volume.createVolume(None, None, vol_path, size, volFormat, preallocate)
        else:
            # Create hardlink to template and its meta file
            if imgUUID != srcImgUUID:
                pvol.share(imageDir, hard=True)
                # Make clone to link the new volume against the local shared volume
                pvol = FileVolume(repoPath, sdUUID, imgUUID, srcVolUUID)
            pvol.clone(imageDir, volUUID, volFormat, preallocate)
            size = pvol.getMetaParam(volume.SIZE)

        try:
            vars.task.pushRecovery(task.Recovery("create file volume metadata rollback", "fileVolume", "FileVolume", "createVolumeMetadataRollback",
                                                 [vol_path]))
            # By definition volume is now a leaf
            cls.file_setrw(vol_path, rw=True)
            # Set metadata and mark volume as legal
            cls.newMetadata(vol_path, sdUUID, imgUUID, srcVolUUID, size, volume.type2name(volFormat),
                            volume.type2name(preallocate), voltype, diskType, desc, volume.LEGAL_VOL)
            cls.newVolumeLease(sdUUID, volUUID, vol_path)
        except Exception, e:
            cls.log.error("Unexpected error", exc_info=True)
            raise se.VolumeMetadataWriteError(vol_path + ":" + str(e))

        # Remove all previous rollbacks for 'halfbaked' volume and add rollback for 'real' volume creation
        vars.task.replaceRecoveries(task.Recovery("create file volume rollback", "fileVolume", "FileVolume", "createVolumeRollback",
                                             [repoPath, sdUUID, imgUUID, volUUID, imageDir]))
        return volUUID


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
                pvol = FileVolume(self.repoPath, self.sdUUID, self.imgUUID, puuid)
                pvol.recheckIfLeaf()
        except Exception, e:
            eFound = e
            self.log.warning("cannot finalize parent volume %s", puuid, exc_info=True)

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
            self.log.error("cannot remove volume's %s metadata", self.volUUID, exc_info=True)

        raise eFound

    def getDevPath(self):
        """
        Return the underlying device (for sharing)
        """
        return self.getVolumePath()

    def share(self, dst_image_dir, hard=True):
        """
        Share this volume to dst_image_dir, including the meta file
        """
        volume.Volume.share(self, dst_image_dir, hard=hard)

        self.log.debug("share  meta of %s to %s hard %s" % (self.volUUID, dst_image_dir, hard))
        src = self._getMetaVolumePath()
        dst = self._getMetaVolumePath(os.path.join(dst_image_dir, self.volUUID))
        if self.oop.fileUtils.pathExists(dst):
            self.oop.os.unlink(dst)
        if hard:
            self.oop.os.link(src, dst)
        else:
            self.oop.os.symlink(src, dst)

    def setrw(self, rw):
        """
        Set the read/write permission on the volume
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
            grpmode =  mode & 0070
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


    def getMetadata(self, vol_path = None, nocache=False):
        """
        Get Meta data array of key,values lines
        """
        if nocache:
            out = self.metaCache()
            if out:
                return out
        meta = self._getMetaVolumePath(vol_path)
        try:
            f = self.oop.directReadLines(meta)
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
            raise se.VolumeMetadataReadError(meta + str(e))
        self.putMetaCache(out)
        return out

    @classmethod
    def __putMetadata(cls, metaarr, vol_path):
        meta = cls.__metaVolumePath(vol_path)
        f = None
        try:
            f = open(meta + ".new", "w")
            for key, value in metaarr.iteritems():
                f.write("%s=%s\n" % (key.strip(), str(value).strip()))
            f.write("EOF\n")
        finally:
            if f:
                f.close()

        sdUUID = getDomUuidFromVolumePath(vol_path)
        oop.getProcessPool(sdUUID).os.rename(meta + ".new", meta)


    @classmethod
    def createMetadata(cls, metaarr, vol_path):
        cls.__putMetadata(metaarr, vol_path)

    def setMetadata(self, metaarr, vol_path = None, nocache=False):
        """
        Set the meta data hash as the new meta data of the Volume
        """
        if not vol_path:
            vol_path = self.getVolumePath()
        try:
            self.__putMetadata(metaarr, vol_path)
            if not nocache:
                self.putMetaCache(metaarr)
        except Exception, e:
            self.log.error(e, exc_info=True)
            raise se.VolumeMetadataWriteError(vol_path + ":" + str(e))

    @classmethod
    def getImageVolumes(cls, repoPath, sdUUID, imgUUID):
        """
        Fetch the list of the Volumes UUIDs, not including the shared base (template)
        """
        # Get Volumes of an image
        pattern = os.path.join(repoPath, sdUUID, sd.DOMAIN_IMAGES, imgUUID, "*.meta")
        files = oop.getProcessPool(sdUUID).glob.glob(pattern)
        volList = []
        for i in files:
            volid = os.path.splitext(os.path.basename(i))[0]
            if sdCache.produce(sdUUID).produceVolume(imgUUID, volid).getImage() == imgUUID:
                volList.append(volid)
        return volList

    @classmethod
    def newVolumeLease(cls, sdUUID, volUUID, volPath):
        dom = sdCache.produce(sdUUID)
        procPool = oop.getProcessPool(sdUUID)

        if dom.hasVolumeLeases():
            leasePath = cls.__leaseVolumePath(volPath)
            procPool.createSparseFile(leasePath, LEASE_FILEOFFSET)
            cls.file_setrw(leasePath, rw=True)
            sanlock.init_resource(sdUUID, volUUID,
                                  [(leasePath, LEASE_FILEOFFSET)])

    @classmethod
    def getAllChildrenList(cls, repoPath, sdUUID, imgUUID, pvolUUID):
        """
        Fetch the list of children volumes (across the all images in domain)
        """
        volList = []
        # FIXME!!! We cannot check hardlinks in 'backup' domain, because of possibility of overwriting
        #  'fake' volumes that have hardlinks with 'legal' volumes with same uuid and without hardlinks
        # First, check number of hardlinks
     ## volPath = os.path.join(cls.storage_repository, spUUID, sdUUID, sd.DOMAIN_IMAGES, imgUUID, pvolUUID)
     ## if os.path.exists(volPath):
     ##     if os.stat(volPath).st_nlink == 1:
     ##         return volList
     ## else:
     ##     cls.log.info("Volume %s does not exist", volPath)
     ##     return volList
        # scan whole domain
        pattern = os.path.join(repoPath, sdUUID, sd.DOMAIN_IMAGES, "*", "*.meta")
        files = oop.getProcessPool(sdUUID).glob.glob(pattern)
        sdDom = sdCache.produce(sdUUID)
        for i in files:
            volid = os.path.splitext(os.path.basename(i))[0]
            imgUUID = os.path.basename(os.path.dirname(i))
            if sdDom.produceVolume(imgUUID, volid).getParent() == pvolUUID:
                volList.append({'imgUUID':imgUUID, 'volUUID':volid})

        return volList

    def findImagesByVolume(self, legal=False):
        """
        Find the image(s) UUID by one of its volume UUID.
        Templated and shared disks volumes may result more then one image.
        """
        try:
            pattern = os.path.join(self.repoPath, self.sdUUID, sd.DOMAIN_IMAGES, "*", self.volUUID)
            vollist = self.oop.glob.glob(pattern)
            for vol in vollist[:]:
                img = os.path.basename(os.path.dirname(vol))
                if img.startswith(image.REMOVED_IMAGE_PREFIX):
                    vollist.remove(vol)
        except Exception, e:
            self.log.info("Volume %s does not exists." % (self.volUUID))
            raise se.VolumeDoesNotExist("%s: %s:" % (self.volUUID, e))

        imglist = [ os.path.basename(os.path.dirname(vol)) for vol in vollist ]

        # Check image legallity, if needed
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
        return sdobj.produceVolume(imgUUID, volUUID).getVolumeSize(bs)

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
            cls.log.error("Could not rollback volume rename (oldPath=%s newPath=%s)", oldPath, newPath, exc_info=True)

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
            vars.task.pushRecovery(task.Recovery(name, "fileVolume", "FileVolume", "renameVolumeRollback",
                                                 [volPath, self.volumePath]))
        self.oop.os.rename(self.volumePath, volPath)
        if recovery:
            name = "Rename meta-volume rollback: " + metaPath
            vars.task.pushRecovery(task.Recovery(name, "fileVolume", "FileVolume", "renameVolumeRollback",
                                                 [metaPath, prevMetaPath]))
        self.oop.os.rename(prevMetaPath, metaPath)
        self.volUUID = newUUID
        self.volumePath = volPath

    def validateImagePath(self):
        """
        Validate that the image dir exists and valid. In the file volume repositories,
        the image dir must exists after creation its first volume.
        """
        imageDir = image.Image(self.repoPath).getImageDir(self.sdUUID, self.imgUUID)
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
        Get/Set the path of the metadata volume file/link
        """
        if not vol_path:
            vol_path = self.getVolumePath()
        return self.__metaVolumePath(vol_path)

    def validateVolumePath(self):
        """
        In file volume repositories,
        the volume file and the volume md must exists after the image/volume is created.
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


