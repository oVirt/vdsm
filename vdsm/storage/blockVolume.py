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
import logging
import sanlock

from vdsm import qemuimg
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

QCOW_OVERHEAD_FACTOR = 1.1

# Reserved leases for special purposes:
#  - 0       SPM (Backward comapatibility with V0 and V2)
#  - 1       SDM (SANLock V3)
#  - 2..100  (Unassigned)
RESERVED_LEASES = 100

log = logging.getLogger('Storage.Volume')
rmanager = rm.ResourceManager.getInstance()


class BlockVolume(volume.Volume):
    """ Actually represents a single volume (i.e. part of virtual disk).
    """

    def __init__(self, repoPath, sdUUID, imgUUID, volUUID):
        self.metaoff = None
        volume.Volume.__init__(self, repoPath, sdUUID, imgUUID, volUUID)
        self.lvmActivationNamespace = sd.getNamespace(self.sdUUID,
                                                      LVM_ACTIVATION_NAMESPACE)

    def validate(self):
        try:
            lvm.getLV(self.sdUUID, self.volUUID)
        except se.LogicalVolumeDoesNotExistError:
            raise se.VolumeDoesNotExist(self.volUUID)
        volume.Volume.validate(self)

    def refreshVolume(self):
        lvm.refreshLVs(self.sdUUID, (self.volUUID,))

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
                    cls.log.debug("Unlinking half baked volume: %s", volPath)
                    os.unlink(volPath)

    @classmethod
    def createVolumeMetadataRollback(cls, taskObj, sdUUID, offs):
        cls.log.info("Metadata rollback for sdUUID=%s offs=%s", sdUUID, offs)
        cls.__putMetadata((sdUUID, int(offs)),
                          {"NONE": "#" * (sd.METASIZE - 10)})

    @classmethod
    def _create(cls, dom, imgUUID, volUUID, size, volFormat, preallocate,
                volParent, srcImgUUID, srcVolUUID, volPath, initialSize=None):
        """
        Class specific implementation of volumeCreate. All the exceptions are
        properly handled and logged in volume.create()
        """

        lvSize = cls._calculate_volume_alloc_size(preallocate,
                                                  size, initialSize)

        lvm.createLV(dom.sdUUID, volUUID, "%s" % lvSize, activate=True,
                     initialTag=TAG_VOL_UNINIT)

        utils.rmFile(volPath)
        os.symlink(lvm.lvPath(dom.sdUUID, volUUID), volPath)

        if not volParent:
            cls.log.info("Request to create %s volume %s with size = %s "
                         "sectors", volume.type2name(volFormat), volPath,
                         size)
            if volFormat == volume.COW_FORMAT:
                qemuimg.create(
                    volPath, size * BLOCK_SIZE, volume.fmt2str(volFormat))
        else:
            # Create hardlink to template and its meta file
            cls.log.info("Request to create snapshot %s/%s of volume %s/%s",
                         imgUUID, volUUID, srcImgUUID, srcVolUUID)
            volParent.clone(volPath, volFormat)

        with dom.acquireVolumeMetadataSlot(volUUID, VOLUME_MDNUMBLKS) as slot:
            mdTags = ["%s%s" % (TAG_PREFIX_MD, slot),
                      "%s%s" % (TAG_PREFIX_PARENT, srcVolUUID),
                      "%s%s" % (TAG_PREFIX_IMAGE, imgUUID)]
            lvm.changeLVTags(dom.sdUUID, volUUID, delTags=[TAG_VOL_UNINIT],
                             addTags=mdTags)

        try:
            lvm.deactivateLVs(dom.sdUUID, volUUID)
        except se.CannotDeactivateLogicalVolume:
            cls.log.warn("Cannot deactivate new created volume %s/%s",
                         dom.sdUUID, volUUID, exc_info=True)

        return (dom.sdUUID, slot)

    @classmethod
    def _calculate_volume_alloc_size(cls, preallocate, capacity, initial_size):
        """ Calculate the allocation size in mb of the volume
        'preallocate' - Sparse or Preallocated
        'capacity' - the volume size in sectors
        'initial_size' - optional, if provided the initial allocated
                         size in sectors for sparse volumes
         """
        if initial_size and initial_size > capacity:
            log.error("The volume size %s is smaller "
                      "than the requested initial size %s",
                      capacity, initial_size)
            raise se.InvalidParameterException("initial size",
                                               initial_size)

        if initial_size and preallocate == volume.PREALLOCATED_VOL:
            log.error("Initial size is not supported for preallocated volumes")
            raise se.InvalidParameterException("initial size",
                                               initial_size)

        if preallocate == volume.SPARSE_VOL:
            if initial_size:
                initial_size = int(initial_size * QCOW_OVERHEAD_FACTOR)
                alloc_size = ((initial_size + SECTORS_TO_MB - 1)
                              / SECTORS_TO_MB)
            else:
                alloc_size = config.getint("irs",
                                           "volume_utilization_chunk_mb")
        else:
            alloc_size = (capacity + SECTORS_TO_MB - 1) / SECTORS_TO_MB

        return alloc_size

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

        # On block storage domains we store a volume's parent UUID in two
        # places: 1) in the domain's metadata LV, and 2) in a LV tag attached
        # to the volume LV itself.  The LV tag is more efficient to access
        # than the domain metadata but it may only be updated by the SPM.
        #
        # This means that after a live merge completes the domain metadata LV
        # will be updated but the LV tag will not.  We can detect this case
        # here and fix the LV tag since this is an SPM verb.
        #
        # File domains do not have this complexity because the metadata is
        # stored in only one place and that metadata is updated by the HSM
        # host when the live merge finishes.
        sync = False
        for childID in self.getChildren():
            child = BlockVolume(self.repoPath, self.sdUUID, self.imgUUID,
                                childID)
            metaParent = child.getParentMeta()
            tagParent = child.getParentTag()
            if metaParent != tagParent:
                self.log.debug("Updating stale PUUID LV tag from %s to %s for "
                               "volume %s", tagParent, metaParent,
                               child.volUUID)
                child.setParentTag(metaParent)
                sync = True
        if sync:
            self.recheckIfLeaf()

        if not force:
            self.validateDelete()

        # Mark volume as illegal before deleting
        self.setLegality(volume.ILLEGAL_VOL)

        if postZero:
            self.prepare(justme=True, rw=True, chainrw=force, setrw=True,
                         force=True)
            try:
                misc.ddWatchCopy(
                    "/dev/zero", vol_path, vars.task.aborting, int(size))
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
            self.log.debug("Unlinking %s", vol_path)
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
            self.prepare()
            try:
                check = qemuimg.check(self.getVolumePath(),
                                      qemuimg.FORMAT.QCOW2)
            finally:
                self.teardown(self.sdUUID, self.volUUID)
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
            vars.task.pushRecovery(task.Recovery(
                name, "blockVolume", "BlockVolume", "renameVolumeRollback",
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

        # Image directory may be a symlink to /run/vdsm/storage/sd/image
        # created when preparing an image before starting a vm.
        if os.path.islink(imageDir) and not os.path.exists(imageDir):
            self.log.warning("Removing stale image directory link %r",
                             imageDir)
            os.unlink(imageDir)

        if not os.path.isdir(imageDir):
            try:
                os.mkdir(imageDir, 0o755)
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
            srcPath = lvm.lvPath(self.sdUUID, self.volUUID)
            self.log.debug("Creating symlink from %s to %s", srcPath, volPath)
            os.symlink(srcPath, volPath)
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

    def getParentMeta(self):
        return self.getMetaParam(volume.PUUID)

    def getParentTag(self):
        return self.getVolumeTag(TAG_PREFIX_PARENT)

    def getParent(self):
        """
        Return parent volume UUID
        """
        return self.getParentTag()

    def getImage(self):
        """
        Return image UUID
        """
        return self.getVolumeTag(TAG_PREFIX_IMAGE)

    def setParentMeta(self, puuid):
        """
        Set parent volume UUID in Volume metadata.  This operation can be done
        by an HSM while it is using the volume and by an SPM when no one is
        using the volume.
        """
        self.setMetaParam(volume.PUUID, puuid)

    def setParentTag(self, puuid):
        """
        Set parent volume UUID in Volume tags.  Since this operation modifies
        LV metadata it may only be performed by an SPM.
        """
        self.changeVolumeTag(TAG_PREFIX_PARENT, puuid)

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

    def getChildren(self):
        """ Return children volume UUIDs.

        Children can be found in any image of the volume SD.
        """
        lvs = lvm.lvsByTag(self.sdUUID,
                           "%s%s" % (TAG_PREFIX_PARENT, self.volUUID))
        return tuple(lv.name for lv in lvs)

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
                raise se.MetadataOverflowError(data)

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
                key, value = l.split("=", 1)
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
        # Just call the SD method getVSize() - apparently it does what
        # we need. We consider incurred overhead of producing the SD object
        # to be a small price for code de-duplication.
        sdobj = sdCache.produce(sdUUID=self.sdUUID)
        return int(sdobj.getVSize(self.imgUUID, self.volUUID) / bs)

    getVolumeTrueSize = getVolumeSize

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
