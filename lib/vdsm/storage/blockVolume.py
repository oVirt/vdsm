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
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA  02110-1301 USA
#
# Refer to the README and COPYING files for full details of the license
#

from __future__ import absolute_import

import os
import logging

from vdsm import utils
from vdsm.common import fileutils
from vdsm.common.marks import deprecated
from vdsm.common.threadlocal import vars
from vdsm.common.units import MiB
from vdsm.config import config
from vdsm.storage import blockdev
from vdsm.storage import constants as sc
from vdsm.storage import exception as se
from vdsm.storage import lvm
from vdsm.storage import qemuimg
from vdsm.storage import resourceManager as rm
from vdsm.storage import task
from vdsm.storage import volume
from vdsm.storage.sdc import sdCache
from vdsm.storage.volumemetadata import VolumeMetadata


QCOW_OVERHEAD_FACTOR = 1.1

# Minimal padding to be added to internal volume optimal size.
MIN_PADDING = MiB

log = logging.getLogger('storage.volume')


class BlockVolumeManifest(volume.VolumeManifest):

    # How this volume is presented to a vm.
    DISK_TYPE = "block"

    # On block storage volume are composed of lvm extents, 128 MiB by default.
    align_size = sc.VG_EXTENT_SIZE

    def __init__(self, repoPath, sdUUID, imgUUID, volUUID):
        volume.VolumeManifest.__init__(self, repoPath, sdUUID, imgUUID,
                                       volUUID)
        self.lvmActivationNamespace = rm.getNamespace(
            sc.LVM_ACTIVATION_NAMESPACE, self.sdUUID)

    @classmethod
    def is_block(cls):
        return True

    def chunked(self):
        return self.getFormat() == sc.COW_FORMAT

    def getMetadataId(self):
        """
        Get the metadata Id
        """
        return (self.sdUUID, self.getMetaSlot())

    def getMetaSlot(self):
        try:
            md = getVolumeTag(self.sdUUID, self.volUUID, sc.TAG_PREFIX_MD)
        except se.MissingTagOnLogicalVolume:
            self.log.error("missing offset tag on volume %s/%s",
                           self.sdUUID, self.volUUID, exc_info=True)
            raise se.VolumeMetadataReadError(
                "missing offset tag on volume %s/%s" %
                (self.sdUUID, self.volUUID))
        else:
            return int(md)

    def getMetadata(self, metaId=None):
        """
        Get Meta data array of key,values lines
        """
        if not metaId:
            metaId = self.getMetadataId()

        _, slot = metaId
        sd = sdCache.produce_manifest(self.sdUUID)
        try:
            lines = sd.read_metadata_block(slot).splitlines()
        except Exception as e:
            self.log.error(e, exc_info=True)
            raise se.VolumeMetadataReadError("%s: %s" % (metaId, e))

        md = VolumeMetadata.from_lines(lines)
        return md

    def validateImagePath(self):
        """
        Block SD supports lazy image dir creation
        """
        manifest = sdCache.produce_manifest(self.sdUUID)
        imageDir = manifest.getImageDir(self.imgUUID)

        # Image directory may be a symlink to /run/vdsm/storage/sd/image
        # created when preparing an image before starting a vm.
        if os.path.islink(imageDir) and not os.path.exists(imageDir):
            self.log.warning("Removing stale image directory link %r",
                             imageDir)
            os.unlink(imageDir)

        if not os.path.isdir(imageDir):
            self.log.info("Creating image directory %r", imageDir)
            try:
                os.mkdir(imageDir, 0o755)
            except Exception:
                self.log.exception("Unexpected error")
                raise se.ImagePathError(imageDir)
        self._imagePath = imageDir

    def validateVolumePath(self):
        """
        Block SD supports lazy volume link creation. Note that the volume can
        be still inactive.
        An explicit prepare is required to validate that the volume is active.
        """
        if not self._imagePath:
            self.validateImagePath()
        volPath = os.path.join(self._imagePath, self.volUUID)
        if not os.path.lexists(volPath):
            srcPath = lvm.lvPath(self.sdUUID, self.volUUID)
            self.log.info("Creating symlink from %s to %s", srcPath, volPath)
            os.symlink(srcPath, volPath)
        self._volumePath = volPath

    def validate(self):
        try:
            lvm.getLV(self.sdUUID, self.volUUID)
        except se.LogicalVolumeDoesNotExistError:
            raise se.VolumeDoesNotExist(self.volUUID)

        volume.VolumeManifest.validate(self)

    def getVolumeTag(self, tagPrefix):
        return getVolumeTag(self.sdUUID, self.volUUID, tagPrefix)

    def getParentTag(self):
        return self.getVolumeTag(sc.TAG_PREFIX_PARENT)

    def getParentMeta(self):
        return self.getMetaParam(sc.PUUID)

    def getParent(self):
        """
        Return parent volume UUID
        """
        return self.getParentTag()

    def getChildren(self):
        """ Return children volume UUIDs.

        Children can be found in any image of the volume SD.
        """
        lvs = lvm.lvsByTag(self.sdUUID,
                           "%s%s" % (sc.TAG_PREFIX_PARENT, self.volUUID))
        return tuple(lv.name for lv in lvs)

    def getImage(self):
        """
        Return image UUID
        """
        return self.getVolumeTag(sc.TAG_PREFIX_IMAGE)

    def getDevPath(self):
        """
        Return the underlying device (for sharing)
        """
        return lvm.lvPath(self.sdUUID, self.volUUID)

    def getVolumeSize(self):
        """
        Return the volume size in bytes.
        """
        # Just call the SD Manifest method getVSize() - apparently it does what
        # we need. We consider incurred overhead of producing the object
        # to be a small price for code de-duplication.
        manifest = sdCache.produce_manifest(self.sdUUID)
        return manifest.getVSize(self.imgUUID, self.volUUID)

    def getVolumeTrueSize(self):
        """
        Return the true volume size in bytes
        """
        return self.getVolumeSize()

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
            raise se.VolumeMetadataWriteError("%s: %s" % (metaId, e))

    @deprecated  # valid only for domain version < 3, see volume.setrw
    def _setrw(self, rw):
        """
        Set the read/write permission on the volume (deprecated)
        """
        lvm.setrwLV(self.sdUUID, self.volUUID, rw)

    @classmethod
    def _putMetadata(cls, metaId, meta, **overrides):
        sd = sdCache.produce_manifest(meta.domain)

        _, slot = metaId

        data = meta.storage_format(sd.getVersion(), **overrides)
        data = data.ljust(sc.METADATA_SIZE, b"\0")
        sd.write_metadata_block(slot, data)

    def changeVolumeTag(self, tagPrefix, uuid):

        if tagPrefix not in sc.VOLUME_TAGS:
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
            lvm.changeLVsTags(
                self.sdUUID,
                (self.volUUID,),
                delTags=(oldTag,),
                addTags=(newTag,))

    def setParentMeta(self, puuid):
        """
        Set parent volume UUID in Volume metadata.  This operation can be done
        by an HSM while it is using the volume and by an SPM when no one is
        using the volume.
        """
        self.setMetaParam(sc.PUUID, puuid)

    def setParentTag(self, puuid):
        """
        Set parent volume UUID in Volume tags.  Since this operation modifies
        LV metadata it may only be performed by an SPM.
        """
        self.changeVolumeTag(sc.TAG_PREFIX_PARENT, puuid)

    def setImage(self, imgUUID):
        """
        Set image UUID
        """
        self.changeVolumeTag(sc.TAG_PREFIX_IMAGE, imgUUID)
        # FIXME In next version we should remove imgUUID, as it is saved on lvm
        # tags
        self.setMetaParam(sc.IMAGE, imgUUID)

    def removeMetadata(self, metaId):
        """
        Just wipe meta.
        """
        _, slot = metaId
        sdCache.produce_manifest(self.sdUUID).clear_metadata_block(slot)

    @classmethod
    def newVolumeLease(cls, metaId, sdUUID, volUUID):
        cls.log.debug("Initializing volume lease volUUID=%s sdUUID=%s, "
                      "metaId=%s", volUUID, sdUUID, metaId)
        _, slot = metaId
        sd = sdCache.produce_manifest(sdUUID)
        sd.create_volume_lease(slot, volUUID)

    def refreshVolume(self):
        lvm.refreshLVs(self.sdUUID, (self.volUUID,))

    def _share(self, dstImgPath):
        """
        Share this volume to dstImgPath
        """
        dstPath = os.path.join(dstImgPath, self.volUUID)

        self.log.info("Share volume %s to %s", self.volUUID, dstImgPath)
        os.symlink(self.getDevPath(), dstPath)

    @classmethod
    def getImageVolumes(cls, sdUUID, imgUUID):
        """
        Fetch the list of the Volumes UUIDs, not including the shared base
        (template)
        """
        lvs = lvm.lvsByTag(sdUUID, "%s%s" % (sc.TAG_PREFIX_IMAGE, imgUUID))
        return [lv.name for lv in lvs]

    @classmethod
    def calculate_volume_alloc_size(
            cls, preallocate, vol_format, capacity, initial_size):
        """ Calculate the allocation size in bytes of the volume
        'preallocate' - Sparse or Preallocated
        'vol_format' - sc.COW_FORMAT or sc.RAW_FORMAT
        'capacity' - the volume size in bytes
        'initial_size' - optional, if provided the initial allocated
                         size in bytes for sparse volumes
         """
        if initial_size and vol_format == sc.RAW_FORMAT:
            log.error("Initial size is not supported for raw preallocated "
                      "volumes")
            raise se.InvalidParameterException("initial size",
                                               initial_size)

        if initial_size:
            max_size = cls.max_size(capacity, sc.COW_FORMAT)
            if initial_size > max_size:
                log.error("The requested initial %s is bigger "
                          "than the max size %s", initial_size, max_size)
                raise se.InvalidParameterException("initial_size",
                                                   initial_size)

        if preallocate == sc.SPARSE_VOL:
            # Sparse qcow2
            if initial_size:
                # TODO: if initial_size == max_size, we exceed the max_size
                # here. This should be fixed, but first we must check that
                # engine is not assuming that vdsm will increase initial size
                # like this.
                alloc_size = int(initial_size * QCOW_OVERHEAD_FACTOR)
            else:
                chunk_size_mb = config.getint("irs",
                                              "volume_utilization_chunk_mb")
                alloc_size = chunk_size_mb * MiB
        else:
            # Preallocated qcow2
            alloc_size = initial_size if initial_size else capacity

        return alloc_size

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
        access = rm.EXCLUSIVE if rw else rm.SHARED
        activation = rm.acquireResource(self.lvmActivationNamespace,
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
        lvmActivationNamespace = rm.getNamespace(sc.LVM_ACTIVATION_NAMESPACE,
                                                 sdUUID)
        rm.releaseResource(lvmActivationNamespace, volUUID)
        if not justme:
            try:
                pvolUUID = getVolumeTag(sdUUID, volUUID, sc.TAG_PREFIX_PARENT)
            except Exception as e:
                # If storage not accessible or lvm error occurred
                # we will failure to get the parent volume.
                # We can live with it and still succeed in volume's teardown.
                pvolUUID = sc.BLANK_UUID
                cls.log.warn("Failure to get parent of volume %s/%s (%s)"
                             % (sdUUID, volUUID, e))

            if pvolUUID != sc.BLANK_UUID:
                cls.teardown(sdUUID=sdUUID, volUUID=pvolUUID, justme=False)

    def optimal_size(self):
        """
        Return the optimal size of the volume.

        Returns:
            optimal size is the minimum of the volume maximum size and the
            volume actual size plus padding. For leaf volumes, the padding
            is one chunk, and for internal volumes the padding is
            `MIN_PADDING`.
            Size is returned in bytes.

        Note:
            the volume must be prepared when calling this helper.
        """
        if self.getFormat() == sc.RAW_FORMAT:
            virtual_size = self.getCapacity()
            self.log.debug("RAW format, using virtual size: %s", virtual_size)
            return virtual_size

        # Read actual size.
        check = qemuimg.check(self.getVolumePath(), qemuimg.FORMAT.QCOW2)
        actual_size = check['offset']

        # Add padding.
        if self.isLeaf():
            # For leaf volumes, the padding is one chunk.
            chnuk_size_mb = int(config.get("irs",
                                           "volume_utilization_chunk_mb"))
            padding = chnuk_size_mb * MiB
            self.log.debug("Leaf volume, using padding: %s", padding)

            potential_optimal_size = actual_size + padding

        else:
            # For internal volumes, using minimal padding.
            padding = MIN_PADDING
            self.log.debug("Internal volume, using padding: %s", padding)

            potential_optimal_size = actual_size + padding

            # Limit optimal size to the minimal volume size.
            potential_optimal_size = max(sc.MIN_CHUNK, potential_optimal_size)

        # Limit optimal size by maximum size.
        max_size = self.max_size(self.getCapacity(), self.getFormat())
        optimal_size = min(potential_optimal_size, max_size)
        self.log.debug("COW format, actual_size: %s, max_size: %s, "
                       "optimal_size: %s",
                       actual_size, max_size, optimal_size)
        return optimal_size


class BlockVolume(volume.Volume):
    """ Actually represents a single volume (i.e. part of virtual disk).
    """
    manifestClass = BlockVolumeManifest

    def refreshVolume(self):
        self._manifest.refreshVolume()

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
            if sc.TAG_VOL_UNINIT in tags:
                try:
                    lvm.removeLVs(sdUUID, (volUUID,))
                except se.CannotRemoveLogicalVolume as e:
                    cls.log.warning("Remove logical volume failed %s/%s %s",
                                    sdUUID, volUUID, str(e))

                if os.path.lexists(volPath):
                    cls.log.info("Unlinking half baked volume: %s", volPath)
                    os.unlink(volPath)

    @classmethod
    def createVolumeMetadataRollback(cls, taskObj, sdUUID, slot_str):
        """
        This function is called only from tasks framework, with strings values.
        """
        cls.log.info("Metadata rollback for sdUUID=%s slot=%s", sdUUID,
                     slot_str)
        sd = sdCache.produce_manifest(sdUUID)
        sd.clear_metadata_block(int(slot_str))

    @classmethod
    def _create(cls, dom, imgUUID, volUUID, capacity, volFormat, preallocate,
                volParent, srcImgUUID, srcVolUUID, volPath, initial_size=None,
                add_bitmaps=False):
        """
        Class specific implementation of volumeCreate. All the exceptions are
        properly handled and logged in volume.create()
        """

        lv_size = cls.calculate_volume_alloc_size(
            preallocate, volFormat, capacity, initial_size)
        lv_size_mb = utils.round(lv_size, MiB) // MiB

        lvm.createLV(dom.sdUUID, volUUID, lv_size_mb, activate=True,
                     initialTags=(sc.TAG_VOL_UNINIT,))

        fileutils.rm_file(volPath)
        lvPath = lvm.lvPath(dom.sdUUID, volUUID)
        cls.log.info("Creating volume symlink from %r to %r", lvPath, volPath)
        os.symlink(lvPath, volPath)

        if not volParent:
            cls.log.info("Request to create %s volume %s with capacity = %s",
                         sc.type2name(volFormat), volPath, capacity)
            if volFormat == sc.COW_FORMAT:
                operation = qemuimg.create(volPath,
                                           size=capacity,
                                           format=sc.fmt2str(volFormat),
                                           qcow2Compat=dom.qcow2_compat())
                operation.run()
        else:
            # Create hardlink to template and its meta file
            cls.log.info("Request to create snapshot %s/%s of volume %s/%s "
                         "with capacity %s",
                         imgUUID, volUUID, srcImgUUID, srcVolUUID, capacity)
            volParent.clone(
                volPath, volFormat, capacity, add_bitmaps=add_bitmaps)

        with dom.acquireVolumeMetadataSlot(volUUID) as slot:
            mdTags = ["%s%s" % (sc.TAG_PREFIX_MD, slot),
                      "%s%s" % (sc.TAG_PREFIX_PARENT, srcVolUUID),
                      "%s%s" % (sc.TAG_PREFIX_IMAGE, imgUUID)]
            lvm.changeLVsTags(
                dom.sdUUID,
                (volUUID,),
                delTags=[sc.TAG_VOL_UNINIT],
                addTags=mdTags)

        try:
            lvm.deactivateLVs(dom.sdUUID, [volUUID])
        except se.CannotDeactivateLogicalVolume:
            cls.log.warn("Cannot deactivate new created volume %s/%s",
                         dom.sdUUID, volUUID, exc_info=True)

        return (dom.sdUUID, slot)

    @classmethod
    def calculate_volume_alloc_size(cls, preallocate, vol_format, capacity,
                                    initial_size):
        return cls.manifestClass.calculate_volume_alloc_size(
            preallocate, vol_format, capacity, initial_size)

    def removeMetadata(self, metaId):
        self._manifest.removeMetadata(metaId)

    def delete(self, postZero, force, discard):
        """ Delete volume
            'postZero' - zeroing file before deletion
            'force' is required to remove shared and internal volumes
            'discard' - discard lv before deletion
        """
        self.log.info("Request to delete LV %s of image %s in VG %s ",
                      self.volUUID, self.imgUUID, self.sdUUID)

        vol_path = self.getVolumePath()

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
        self.setLegality(sc.ILLEGAL_VOL)

        if postZero or discard:
            self.prepare(justme=True, rw=True, chainrw=force, setrw=True,
                         force=True)
            try:
                if postZero:
                    blockdev.zero(vol_path, task=vars.task)

                if discard:
                    blockdev.discard(vol_path)
            finally:
                self.teardown(self.sdUUID, self.volUUID, justme=True)

        # try to cleanup as much as possible
        eFound = se.CannotDeleteVolume(self.volUUID)
        puuid = None
        try:
            # We need to blank parent record in our metadata
            # for parent to become leaf successfully.
            puuid = self.getParent()
            self.setParent(sc.BLANK_UUID)
            if puuid and puuid != sc.BLANK_UUID:
                pvol = BlockVolume(self.repoPath, self.sdUUID, self.imgUUID,
                                   puuid)
                pvol.recheckIfLeaf()
        except Exception as e:
            eFound = e
            self.log.warning("cannot finalize parent volume %s", puuid,
                             exc_info=True)

        # Basically, we want to mark the volume _remove_me at the beginning of
        # the delete; however, with the current delete logic, if marking the
        # volume fails, and the deleted volume is a leaf, we end up with a
        # chain with a valid leaf volume.
        # The ultimate solution of volume deletion requires changes in
        # image.syncVolumeChain to disconnect the volume from the chain,
        # and probably there mark it as _remove_me.
        manifest = sdCache.produce_manifest(self.sdUUID)
        manifest.markForDelVols(self.sdUUID, self.imgUUID, [self.volUUID],
                                sc.REMOVED_IMAGE_PREFIX)

        try:
            lvm.removeLVs(self.sdUUID, (self.volUUID,))
        except se.CannotRemoveLogicalVolume:
            self.log.exception("Failed to delete volume %s/%s. The "
                               "logical volume must be removed manually.",
                               self.sdUUID, self.volUUID)

        try:
            self.log.info("Unlinking %s", vol_path)
            os.unlink(vol_path)
            return True
        except Exception as e:
            eFound = e
            self.log.error("cannot delete volume's %s/%s link path: %s",
                           self.sdUUID, self.volUUID, vol_path, exc_info=True)

        raise eFound

    def extend(self, new_size):
        """Extend a logical volume
            'new_size' - new size in bytes
        """
        self.log.info("Request to extend LV %s of image %s in VG %s with "
                      "size = %s", self.volUUID, self.imgUUID,
                      self.sdUUID, new_size)
        # we should return: Success/Failure
        # Backend APIs:
        sizemb = utils.round(new_size, MiB) // MiB
        lvm.extendLV(self.sdUUID, self.volUUID, sizemb)

    def reduce(self, new_size, allowActive=False):
        """
        Reduce the size of the logical volume.

        Arguments:
            new_size (int) - new size in bytes
            allowActive (boolean) - indicates whether the LV is active
        """
        self.log.info("Request to reduce LV %s of image %s in VG %s with "
                      "size = %s allowActive = %s", self.volUUID,
                      self.imgUUID, self.sdUUID, new_size, allowActive)
        sizemb = utils.round(new_size, MiB) // MiB
        lvm.reduceLV(self.sdUUID, self.volUUID, sizemb, force=allowActive)

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
            self._manifest.validateImagePath()

        if os.path.lexists(self.getVolumePath()):
            self.log.info("Unlinking volume symlink %r", self.getVolumePath())
            os.unlink(self.getVolumePath())

        if recovery:
            name = "Rename volume rollback: " + newUUID
            vars.task.pushRecovery(task.Recovery(
                name, "blockVolume", "BlockVolume", "renameVolumeRollback",
                [self.sdUUID, newUUID, self.volUUID]))

        # Save the metadaId before renaming the LV, because getMetadataId()
        # uses the volume UUID and we need the metadataId before perform the
        # rename.
        metadataId = self.getMetadataId()

        lvm.renameLV(self.sdUUID, self.volUUID, newUUID)

        self.renameLease(metadataId, newUUID, recovery=recovery)

        self._manifest.volUUID = newUUID
        self._manifest.volumePath = os.path.join(self.imagePath, newUUID)

    def getDevPath(self):
        return self._manifest.getDevPath()

    @classmethod
    def shareVolumeRollback(cls, taskObj, volPath):
        cls.log.info("Volume rollback for volPath=%s", volPath)
        fileutils.rm_file(volPath)

    def getVolumeTag(self, tagPrefix):
        return self._manifest.getVolumeTag(tagPrefix)

    def changeVolumeTag(self, tagPrefix, uuid):
        return self._manifest.changeVolumeTag(tagPrefix, uuid)

    def getParentMeta(self):
        return self._manifest.getParentMeta()

    def getParentTag(self):
        return self._manifest.getParentTag()

    def setParentMeta(self, puuid):
        return self._manifest.setParentMeta(puuid)

    def setParentTag(self, puuid):
        return self._manifest.setParentTag(puuid)

    def getMetaSlot(self):
        return self._manifest.getMetaSlot()

    def _extendSizeRaw(self, new_capacity):
        # Since this method relies on lvm.extendLV (lvextend) when the
        # requested size is equal or smaller than the current size, the
        # request is siliently ignored.
        new_capacity_mb = utils.round(new_capacity, MiB) // MiB
        lvm.extendLV(self.sdUUID, self.volUUID, new_capacity_mb)


def getVolumeTag(sdUUID, volUUID, tagPrefix):
    tags = lvm.getLV(sdUUID, volUUID).tags
    if sc.TAG_VOL_UNINIT in tags:
        log.warning("Reloading uninitialized volume %s/%s", sdUUID, volUUID)
        lvm.invalidateVG(sdUUID)
        tags = lvm.getLV(sdUUID, volUUID).tags
        if sc.TAG_VOL_UNINIT in tags:
            log.error("Found uninitialized volume: %s/%s", sdUUID, volUUID)
            raise se.VolumeDoesNotExist("%s/%s" % (sdUUID, volUUID))

    for tag in tags:
        if tag.startswith(tagPrefix):
            return tag[len(tagPrefix):]
    else:
        log.error("Missing tag %s in volume: %s/%s. tags: %s",
                  tagPrefix, sdUUID, volUUID, tags)
        raise se.MissingTagOnLogicalVolume(volUUID, tagPrefix)
