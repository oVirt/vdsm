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
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA  02110-1301 USA
#
# Refer to the README and COPYING files for full details of the license
#

import os
import threading
import logging
import sanlock

from vdsm import qemuImg
from vdsm import constants
from vdsm.config import config
import vdsm.utils as utils
import storage_exception as se
import volume
import image
import sd
import misc
from misc import logskip
from misc import deprecated
import task
import lvm
import resourceManager as rm
from threadLocal import vars
from sdc import sdCache
from resourceFactories import LVM_ACTIVATION_NAMESPACE
import fileUtils

TAG_PREFIX_MD = "MD_"
TAG_PREFIX_MDNUMBLKS = "MS_"
TAG_PREFIX_IMAGE = "IU_"
TAG_PREFIX_PARENT = "PU_"
TAG_VOL_UNINIT = "OVIRT_VOL_INITIALIZING"
VOLUME_TAGS = [TAG_PREFIX_PARENT,
               TAG_PREFIX_IMAGE,
               TAG_PREFIX_MD,
               TAG_PREFIX_MDNUMBLKS]

BLOCK_SIZE = volume.BLOCK_SIZE

# volume meta data block size
VOLUME_METASIZE = BLOCK_SIZE
VOLUME_MDNUMBLKS = 1

SECTORS_TO_MB = 2048

# Reserved leases for special purposes:
#  - 0       SPM (Backward comapatibility with V0 and V2)
#  - 1       SDM (SANLock V3)
#  - 2..100  (Unassigned)
RESERVED_LEASES = 100

log = logging.getLogger('Storage.Volume')
rmanager = rm.ResourceManager.getInstance()


def _tellEnd(devPath):
    with open(devPath, "rb") as f:
        f.seek(0, os.SEEK_END)
        return f.tell()


class BlockVolume(volume.Volume):
    """ Actually represents a single volume (i.e. part of virtual disk).
    """
    _tagCreateLock = threading.Lock()

    def __init__(self, repoPath, sdUUID, imgUUID, volUUID):
        self.metaoff = None
        volume.Volume.__init__(self, repoPath, sdUUID, imgUUID, volUUID)
        self.lvmActivationNamespace = sd.getNamespace(self.sdUUID,
                                                      LVM_ACTIVATION_NAMESPACE)

    def validate(self):
        try:
            lvm.getLV(self.sdUUID, self.volUUID)
        except se.LogicalVolumeDoesNotExistError:
            raise se.VolumeDoesNotExist(self.volUUID)  # Fix me
        volume.Volume.validate(self)

    def refreshVolume(self):
        lvm.refreshLV(self.sdUUID, self.volUUID)

    @classmethod
    def getVSize(cls, sdobj, imgUUID, volUUID, bs=BLOCK_SIZE):
        """ Returns size in block units.

        Returns the largest integer value less than or equal to size [blocks].
        """
        try:
            size = _tellEnd(lvm.lvPath(sdobj.sdUUID, volUUID)) / bs
        except IOError as e:
            if e.errno == os.errno.ENOENT:
                # Inactive volume has no /dev entry. Fallback to lvm way.
                size = int(int(lvm.getLV(sdobj.sdUUID, volUUID).size) / bs)
            else:
                cls.log.warn("Could not get size for vol %s/%s",
                             sdobj.sdUUID, volUUID, exc_info=True)
                raise

        return size

    getVTrueSize = getVSize

    @classmethod
    def halfbakedVolumeRollback(cls, taskObj, sdUUID, volUUID, volPath):
        cls.log.info("sdUUID=%s volUUID=%s volPath=%s" %
                    (sdUUID, volUUID, volPath))
        try:
            # Fix me: assert resource lock.
            tags = lvm.getLV(sdUUID, volUUID).tags
        except se.LogicalVolumeDoesNotExistError:
            pass  # It's OK: inexistent LV, don't try to remove.
        else:
            if TAG_VOL_UNINIT in tags:
                try:
                    lvm.removeLVs(sdUUID, volUUID)
                except se.CannotRemoveLogicalVolume as e:
                    cls.log.warning("Remove logical volume failed %s/%s %s",
                                    sdUUID, volUUID, str(e))

                if os.path.lexists(volPath):
                    os.unlink(volPath)

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

        # Sparse-Raw not supported for block volumes
        if preallocate == volume.SPARSE_VOL and volFormat == volume.RAW_FORMAT:
            raise se.IncorrectFormat(srcVolUUID)

        # Snapshot should be COW volume
        if srcVolUUID != volume.BLANK_UUID and volFormat != volume.COW_FORMAT:
            raise se.IncorrectFormat(srcVolUUID)

    @classmethod
    def createVolumeMetadataRollback(cls, taskObj, sdUUID, offs):
        cls.log.info("Metadata rollback for sdUUID=%s offs=%s", sdUUID, offs)
        cls.__putMetadata((sdUUID, int(offs)),
                          {"NONE": "#" * (sd.METASIZE - 10)})

    @classmethod
    def _create(cls, dom, imgUUID, volUUID, size, volFormat, preallocate,
                volParent, srcImgUUID, srcVolUUID, imgPath, volPath):
        """
        Class specific implementation of volumeCreate. All the exceptions are
        properly handled and logged in volume.create()
        """

        if preallocate == volume.SPARSE_VOL:
            volSize = "%s" % config.get("irs", "volume_utilization_chunk_mb")
        else:
            volSize = "%s" % (size / 2 / 1024)

        lvm.createLV(dom.sdUUID, volUUID, volSize, activate=True,
                     initialTag=TAG_VOL_UNINIT)

        utils.rmFile(volPath)
        os.symlink(lvm.lvPath(dom.sdUUID, volUUID), volPath)

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

        with cls._tagCreateLock:
            mdSlot = dom.mapMetaOffset(volUUID, VOLUME_MDNUMBLKS)
            mdTags = ["%s%s" % (TAG_PREFIX_MD, mdSlot),
                      "%s%s" % (TAG_PREFIX_PARENT, srcVolUUID),
                      "%s%s" % (TAG_PREFIX_IMAGE, imgUUID)]
            lvm.changeLVTags(dom.sdUUID, volUUID, delTags=[TAG_VOL_UNINIT],
                             addTags=mdTags)

        try:
            lvm.deactivateLVs(dom.sdUUID, volUUID)
        except Exception:
            cls.log.warn("Cannot deactivate new created volume %s/%s",
                         dom.sdUUID, volUUID, exc_info=True)

        return (dom.sdUUID, mdSlot)

    def delete(self, postZero, force):
        """ Delete volume
            'postZero' - zeroing file before deletion
            'force' is required to remove shared and internal volumes
        """
        self.log.info("Request to delete LV %s of image %s in VG %s ",
                      self.volUUID, self.imgUUID, self.sdUUID)

        vol_path = self.getVolumePath()
        size = self.getVolumeSize(bs=1)
        offs = self.getMetaOffset()

        if not force:
            self.validateDelete()

        # Mark volume as illegal before deleting
        self.setLegality(volume.ILLEGAL_VOL)

        if postZero:
            self.prepare(justme=True, rw=True, chainrw=force, setrw=True,
                         force=True)
            try:
                misc.ddWatchCopy(
                    "/dev/zero", vol_path, vars.task.aborting, int(size),
                    recoveryCallback=volume.baseAsyncTasksRollback)
            except utils.ActionStopped:
                raise
            except Exception:
                self.log.error("Unexpected error", exc_info=True)
                raise se.VolumesZeroingError(vol_path)
            finally:
                self.teardown(self.sdUUID, self.volUUID, justme=True)

        # try to cleanup as much as possible
        eFound = se.CannotDeleteVolume(self.volUUID)
        puuid = None
        try:
            # We need to blank parent record in our metadata
            # for parent to become leaf successfully.
            puuid = self.getParent()
            self.setParent(volume.BLANK_UUID)
            if puuid and puuid != volume.BLANK_UUID:
                pvol = BlockVolume(self.repoPath, self.sdUUID, self.imgUUID,
                                   puuid)
                pvol.recheckIfLeaf()
        except Exception as e:
            eFound = e
            self.log.warning("cannot finalize parent volume %s", puuid,
                             exc_info=True)

        try:
            try:
                lvm.removeLVs(self.sdUUID, self.volUUID)
            except se.CannotRemoveLogicalVolume:
                # At this point LV is already marked as illegal, we will
                # try to cleanup whatever we can...
                pass

            self.removeMetadata([self.sdUUID, offs])
        except Exception as e:
            eFound = e
            self.log.error("cannot remove volume %s/%s", self.sdUUID,
                           self.volUUID, exc_info=True)

        try:
            os.unlink(vol_path)
            return True
        except Exception as e:
            eFound = e
            self.log.error("cannot delete volume's %s/%s link path: %s",
                           self.sdUUID, self.volUUID, vol_path, exc_info=True)

        raise eFound

    def extend(self, newSize):
        """Extend a logical volume
            'newSize' - new size in blocks
        """
        self.log.info("Request to extend LV %s of image %s in VG %s with "
                      "size = %s", self.volUUID, self.imgUUID, self.sdUUID,
                      newSize)
        # we should return: Success/Failure
        # Backend APIs:
        sizemb = (newSize + SECTORS_TO_MB - 1) / SECTORS_TO_MB
        lvm.extendLV(self.sdUUID, self.volUUID, sizemb)

    def reduce(self, newSize):
        """Reduce a logical volume
            'newSize' - new size in blocks
        """
        self.log.info("Request to reduce LV %s of image %s in VG %s with "
                      "size = %s", self.volUUID, self.imgUUID, self.sdUUID,
                      newSize)
        sizemb = (newSize + SECTORS_TO_MB - 1) / SECTORS_TO_MB
        lvm.reduceLV(self.sdUUID, self.volUUID, sizemb)

    def shrinkToOptimalSize(self):
        """
        Reduce a logical volume to the actual
        disk size, adding round up to next
        closest volume utilization chunk
        """
        volParams = self.getVolumeParams()
        if volParams['volFormat'] == volume.COW_FORMAT:
            self.prepare(justme=True)
            try:
                check = qemuImg.check(self.getVolumePath(),
                                      qemuImg.FORMAT.QCOW2)
            finally:
                self.teardown(self.sdUUID, self.volUUID, justme=True)
            volActualSize = check['offset']
            volExtendSizeMB = int(config.get(
                                  "irs", "volume_utilization_chunk_mb"))
            volExtendSize = volExtendSizeMB * constants.MEGAB
            volUtil = int(config.get("irs", "volume_utilization_percent"))
            finalSize = (volActualSize + volExtendSize * volUtil * 0.01)
            finalSize += volExtendSize - (finalSize % volExtendSize)
            self.log.debug('Shrink qcow volume: %s to : %s bytes',
                           self.volUUID, finalSize)
            self.reduce((finalSize + BLOCK_SIZE - 1) / BLOCK_SIZE)

    @classmethod
    def renameVolumeRollback(cls, taskObj, sdUUID, oldUUID, newUUID):
        try:
            cls.log.info("renameVolumeRollback: sdUUID=%s oldUUID=%s "
                         "newUUID=%s", sdUUID, oldUUID, newUUID)
            lvm.renameLV(sdUUID, oldUUID, newUUID)
        except Exception:
            cls.log.error("Failure in renameVolumeRollback: sdUUID=%s "
                          "oldUUID=%s newUUID=%s", sdUUID, oldUUID, newUUID,
                          exc_info=True)

    def rename(self, newUUID, recovery=True):
        """
        Rename volume
        """
        self.log.info("Rename volume %s as %s ", self.volUUID, newUUID)
        if not self.imagePath:
            self.validateImagePath()

        if os.path.lexists(self.getVolumePath()):
            os.unlink(self.getVolumePath())

        if recovery:
            name = "Rename volume rollback: " + newUUID
            vars.task.pushRecovery(task.Recovery(name, "blockVolume",
                                   "BlockVolume", "renameVolumeRollback",
                                   [self.sdUUID, newUUID, self.volUUID]))

        lvm.renameLV(self.sdUUID, self.volUUID, newUUID)
        self.volUUID = newUUID
        self.volumePath = os.path.join(self.imagePath, newUUID)

    def getDevPath(self):
        """
        Return the underlying device (for sharing)
        """
        return lvm.lvPath(self.sdUUID, self.volUUID)

    def _share(self, dstImgPath):
        """
        Share this volume to dstImgPath
        """
        dstPath = os.path.join(dstImgPath, self.volUUID)

        self.log.debug("Share volume %s to %s", self.volUUID, dstImgPath)
        os.symlink(self.getDevPath(), dstPath)

    @classmethod
    def shareVolumeRollback(cls, taskObj, volPath):
        cls.log.info("Volume rollback for volPath=%s", volPath)
        utils.rmFile(volPath)

    @deprecated  # valid only for domain version < 3, see volume.setrw
    def _setrw(self, rw):
        """
        Set the read/write permission on the volume (deprecated)
        """
        lvm.setrwLV(self.sdUUID, self.volUUID, rw)

    @logskip("ResourceManager")
    def llPrepare(self, rw=False, setrw=False):
        """
        Perform low level volume use preparation

        For the Block Volumes the actual LV activation is wrapped
        into lvmActivation resource. It is being initialized by the
        storage domain sitting on top of the encapsulating VG.
        We just use it here.
        """
        if setrw:
            self.setrw(rw=rw)
        access = rm.LockType.exclusive if rw else rm.LockType.shared
        activation = rmanager.acquireResource(self.lvmActivationNamespace,
                                              self.volUUID, access)
        activation.autoRelease = False

    @classmethod
    def teardown(cls, sdUUID, volUUID, justme=False):
        """
        Deactivate volume and release resources.
        Volume deactivation occurs as part of resource releasing.
        If justme is false, the entire COW chain should be torn down.
        """
        cls.log.info("Tearing down volume %s/%s justme %s"
                     % (sdUUID, volUUID, justme))
        lvmActivationNamespace = sd.getNamespace(sdUUID,
                                                 LVM_ACTIVATION_NAMESPACE)
        rmanager.releaseResource(lvmActivationNamespace, volUUID)
        if not justme:
            try:
                pvolUUID = _getVolumeTag(sdUUID, volUUID, TAG_PREFIX_PARENT)
            except Exception as e:
                # If storage not accessible or lvm error occurred
                # we will failure to get the parent volume.
                # We can live with it and still succeed in volume's teardown.
                pvolUUID = volume.BLANK_UUID
                cls.log.warn("Failure to get parent of volume %s/%s (%s)"
                             % (sdUUID, volUUID, e))

            if pvolUUID != volume.BLANK_UUID:
                cls.teardown(sdUUID=sdUUID, volUUID=pvolUUID, justme=False)

    def validateImagePath(self):
        """
        Block SD supports lazy image dir creation
        """
        imageDir = image.Image(self.repoPath).getImageDir(self.sdUUID,
                                                          self.imgUUID)
        if not os.path.isdir(imageDir):
            try:
                os.mkdir(imageDir, 0755)
            except Exception:
                self.log.error("Unexpected error", exc_info=True)
                raise se.ImagePathError(imageDir)
        self.imagePath = imageDir

    def validateVolumePath(self):
        """
        Block SD supports lazy volume link creation. Note that the volume can
        be still inactive.
        An explicit prepare is required to validate that the volume is active.
        """
        if not self.imagePath:
            self.validateImagePath()
        volPath = os.path.join(self.imagePath, self.volUUID)
        if not os.path.lexists(volPath):
            os.symlink(lvm.lvPath(self.sdUUID, self.volUUID), volPath)
        self.volumePath = volPath

    def getVolumeTag(self, tagPrefix):
        return _getVolumeTag(self.sdUUID, self.volUUID, tagPrefix)

    def changeVolumeTag(self, tagPrefix, uuid):

        if tagPrefix not in VOLUME_TAGS:
            raise se.LogicalVolumeWrongTagError(tagPrefix)

        oldTag = ""
        for tag in lvm.getLV(self.sdUUID, self.volUUID).tags:
            if tag.startswith(tagPrefix):
                oldTag = tag
                break

        if not oldTag:
            raise se.MissingTagOnLogicalVolume(self.volUUID, tagPrefix)

        newTag = tagPrefix + uuid
        if oldTag != newTag:
            lvm.replaceLVTag(self.sdUUID, self.volUUID, oldTag, newTag)

    def getParent(self):
        """
        Return parent volume UUID
        """
        return self.getVolumeTag(TAG_PREFIX_PARENT)

    def getImage(self):
        """
        Return image UUID
        """
        return self.getVolumeTag(TAG_PREFIX_IMAGE)

    def setParent(self, puuid):
        """
        Set parent volume UUID
        """
        self.changeVolumeTag(TAG_PREFIX_PARENT, puuid)
        # FIXME In next version we should remove PUUID, as it is saved on lvm
        # tags
        self.setMetaParam(volume.PUUID, puuid)

    def setImage(self, imgUUID):
        """
        Set image UUID
        """
        self.changeVolumeTag(TAG_PREFIX_IMAGE, imgUUID)
        # FIXME In next version we should remove imgUUID, as it is saved on lvm
        # tags
        self.setMetaParam(volume.IMAGE, imgUUID)

    @classmethod
    def getImageVolumes(cls, repoPath, sdUUID, imgUUID):
        """
        Fetch the list of the Volumes UUIDs, not including the shared base
        (template)
        """
        lvs = lvm.lvsByTag(sdUUID, "%s%s" % (TAG_PREFIX_IMAGE, imgUUID))
        return [lv.name for lv in lvs]

    def removeMetadata(self, metaId):
        """
        Just wipe meta.
        """
        try:
            self.__putMetadata(metaId, {"NONE": "#" * (sd.METASIZE - 10)})
        except Exception as e:
            self.log.error(e, exc_info=True)
            raise se.VolumeMetadataWriteError("%s: %s" % (metaId, e))

    @classmethod
    def __putMetadata(cls, metaId, meta):
        vgname, offs = metaId

        lines = ["%s=%s\n" % (key.strip(), str(value).strip())
                 for key, value in meta.iteritems()]
        lines.append("EOF\n")

        metavol = lvm.lvPath(vgname, sd.METADATA)
        with fileUtils.DirectFile(metavol, "r+d") as f:
            data = "".join(lines)
            if len(data) > VOLUME_METASIZE:
                cls.log.warn("Truncating volume metadata (%s)", data)
                data = data[:VOLUME_METASIZE]
            else:
                data += "\0" * (VOLUME_METASIZE - len(data))

            f.seek(offs * VOLUME_METASIZE)
            f.write(data)

    @classmethod
    def createMetadata(cls, metaId, meta):
        cls.__putMetadata(metaId, meta)

    def getMetaOffset(self):
        if self.metaoff:
            return self.metaoff
        try:
            md = _getVolumeTag(self.sdUUID, self.volUUID, TAG_PREFIX_MD)
        except se.MissingTagOnLogicalVolume:
            self.log.error("missing offset tag on volume %s/%s",
                           self.sdUUID, self.volUUID, exc_info=True)
            raise se.VolumeMetadataReadError(
                "missing offset tag on volume %s/%s" %
                (self.sdUUID, self.volUUID))
        else:
            return int(md)

    def getMetadataId(self):
        """
        Get the metadata Id
        """
        return (self.sdUUID, self.getMetaOffset())

    def getMetadata(self, metaId=None):
        """
        Get Meta data array of key,values lines
        """
        if not metaId:
            metaId = self.getMetadataId()

        vgname, offs = metaId

        try:
            meta = misc.readblock(lvm.lvPath(vgname, sd.METADATA),
                                  offs * VOLUME_METASIZE, VOLUME_METASIZE)
            out = {}
            for l in meta:
                if l.startswith("EOF"):
                    return out
                if l.find("=") < 0:
                    continue
                key, value = l.split("=")
                out[key.strip()] = value.strip()

        except Exception as e:
            self.log.error(e, exc_info=True)
            raise se.VolumeMetadataReadError("%s: %s" % (metaId, e))

        return out

    def setMetadata(self, meta, metaId=None):
        """
        Set the meta data hash as the new meta data of the Volume
        """
        if not metaId:
            metaId = self.getMetadataId()

        try:
            self.__putMetadata(metaId, meta)
        except Exception as e:
            self.log.error(e, exc_info=True)
            raise se.VolumeMetadataWriteError("%s: %s" % (metaId, e))

    @classmethod
    def newVolumeLease(cls, metaId, sdUUID, volUUID):
        cls.log.debug("Initializing volume lease volUUID=%s sdUUID=%s, "
                      "metaId=%s", volUUID, sdUUID, metaId)
        dom = sdCache.produce(sdUUID)
        metaSdUUID, mdSlot = metaId

        leasePath = dom.getLeasesFilePath()
        leaseOffset = ((mdSlot + RESERVED_LEASES)
                       * dom.logBlkSize * sd.LEASE_BLOCKS)

        sanlock.init_resource(sdUUID, volUUID, [(leasePath, leaseOffset)])

    def getVolumeSize(self, bs=BLOCK_SIZE):
        """
        Return the volume size in blocks
        """
        # Just call the class method getVSize() - apparently it does what
        # we need. We consider incurred overhead of producing the SD object
        # to be a small price for code de-duplication.
        sdobj = sdCache.produce(sdUUID=self.sdUUID)
        return self.getVSize(sdobj, self.imgUUID, self.volUUID, bs)

    getVolumeTrueSize = getVolumeSize

    def getVolumeMtime(self):
        """
        Return the volume mtime in msec epoch
        """
        try:
            mtime = self.getMetaParam(volume.MTIME)
        except se.MetaDataKeyNotFoundError:
            mtime = 0

        return mtime

    def _extendSizeRaw(self, newSize):
        # Since this method relies on lvm.extendLV (lvextend) when the
        # requested size is equal or smaller than the current size, the
        # request is siliently ignored.
        newSizeMb = (newSize + SECTORS_TO_MB - 1) / SECTORS_TO_MB
        lvm.extendLV(self.sdUUID, self.volUUID, newSizeMb)


def _getVolumeTag(sdUUID, volUUID, tagPrefix):
    tags = lvm.getLV(sdUUID, volUUID).tags
    if TAG_VOL_UNINIT in tags:
        log.warning("Reloading uninitialized volume %s/%s", sdUUID, volUUID)
        lvm.invalidateVG(sdUUID)
        tags = lvm.getLV(sdUUID, volUUID).tags
        if TAG_VOL_UNINIT in tags:
            log.error("Found uninitialized volume: %s/%s", sdUUID, volUUID)
            raise se.VolumeDoesNotExist("%s/%s" % (sdUUID, volUUID))

    for tag in tags:
        if tag.startswith(tagPrefix):
            return tag[len(tagPrefix):]
    else:
        log.error("Missing tag %s in volume: %s/%s. tags: %s",
                  tagPrefix, sdUUID, volUUID, tags)
        raise se.MissingTagOnLogicalVolume(volUUID, tagPrefix)
