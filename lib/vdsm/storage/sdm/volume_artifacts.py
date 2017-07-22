#
# Copyright 2016 Red Hat, Inc.
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
volume_artifacts - construct and deconstruct volumes

In an SDM managed storage domain we will create and remove volumes using
a garbage collection approach rather than persistent tasks and rollback
operations.  Volumes consist of three separate parts: a data area, a
metadata area, and a lease area.  Once created on storage these objects
must be convertible into a Volume with a single atomic operation (ie.
rename a single file).  Conversely, a Volume can be destroyed by
reducing it to its artifacts with a single atomic operation.

VolumeArtifacts is an object to manage the creation and removal of the
three volume artifacts for both block and file based storage.  It also
has methods to manage the conversion of these artifacts to a Volume and
to deconstruct a Volume in order to remove the artifacts from storage.
The three artifacts on storage will not be detected as a volume by other
storage code until they are committed.

Proposed operations for VolumeArtifacts:
 - create: Create the artifacts on storage
 - commit: Convert the artifacts to a volume
 - dismantle: Convert a volume into artifacts
 - clean: Remove the artifacts from storage

Additional methods to identify and garbage collect artifacts will also
be required but the exact interface hasn't settled out yet.
"""

from __future__ import absolute_import

import errno
import logging
import os

from vdsm.storage import constants as sc
from vdsm.storage import exception as se
from vdsm.storage import lvm
from vdsm.storage import qemuimg
from vdsm.storage.volumemetadata import VolumeMetadata


class VolumeArtifacts(object):
    log = logging.getLogger('storage.VolumeArtifacts')

    def __init__(self, sd_manifest, img_id, vol_id):
        """
        Caller must hold the domain lock (paxos lease) and the image resource
        corresponding to self.img_id in exclusive mode.
        """
        self.sd_manifest = sd_manifest
        self.vol_class = self.sd_manifest.getVolumeClass()
        self.img_id = img_id
        self.vol_id = vol_id

    @property
    def volume_path(self):
        """
        Return a path which can be used to access the volume data area.
        """
        raise NotImplementedError()

    def create(self, size, vol_format, disk_type, desc, parent=None,
               initial_size=None):
        """
        Create a new image and volume artifacts or a new volume inside an
        existing image.  The result is considered as garbage until you invoke
        commit().
        """
        raise NotImplementedError()

    def commit(self):
        """
        Commit volume artifacts created in create(), creating a valid volume.
        On failure, the volume is considered as garbage and can be collected by
        the garbage collector.
        """
        raise NotImplementedError()

    def is_image(self):
        """
        Return True if the image already exists.  We assume that at least one
        volume exists in the image.
        """
        raise NotImplementedError()

    def is_garbage(self):
        """
        Return True if storage contains garbage. This can be a volume that was
        interrupted during creation, a dismantled volume, or volume that was
        interrupted during cleanup.
        """
        raise NotImplementedError()

    def _validate_create_params(self, vol_format, parent, prealloc):
        # XXX: Remove these when support is added:
        if parent:
            raise NotImplementedError("parent_vol_id not supported")

        if self.is_image() and not parent:
            self.log.debug("parent not provided when creating a volume in an"
                           "existing image.")
            raise se.InvalidParameterException("parent", parent)

        parent_vol_id = parent.vol_id if parent else sc.BLANK_UUID
        self.sd_manifest.validateCreateVolumeParams(
            vol_format, parent_vol_id, preallocate=prealloc)

    def _validate_size(self, size, initial_size, vol_format):
        if size % sc.BLOCK_SIZE != 0:
            self.log.debug("size %s not a multiple of the block size", size)
            raise se.InvalidParameterException("size", size)

        if initial_size is not None and vol_format != sc.COW_FORMAT:
            self.log.debug("initial_size is supported only for COW volumes")
            raise se.InvalidParameterException("initial_size", initial_size)

        if initial_size and initial_size % sc.BLOCK_SIZE != 0:
            self.log.debug("initial_size %s not a multiple of the block size",
                           initial_size)
            raise se.InvalidParameterException("initial_size", initial_size)

    def _initialize_volume(self, vol_format, size):
        if vol_format == sc.COW_FORMAT:
            qemuimg.create(self.volume_path,
                           size=size,
                           format=sc.fmt2str(vol_format))


class FileVolumeArtifacts(VolumeArtifacts):
    """
    A file based volume can be in one of these states:

    MISSING

    - States:
        - no image or volatile directories
    - Operations:
        - is_garbage -> false
        - is_image -> false
        - create artifacts -> change state GARBAGE

    GARBAGE

    - States:
        - volatile image directory
        - image directory containing a volatile metadata file
    - Operations:
        - is_garbage -> true
        - is_image -> true or false
        - clean -> change state to MISSING or VOLUME, GARBAGE if failed
        - commit -> change state to VOLUME, GARBAGE if failed

    VOLUME

    - States:
       - image directory with volume files
    - Operations:
        - is_garbage -> false
        - is_image -> true
        - create new volume -> change state to GARBAGE
        - destroy this volume -> change state to GARBAGE
    """
    log = logging.getLogger('storage.FileVolumeArtifacts')

    def __init__(self, sd_manifest, img_id, vol_id):
        super(FileVolumeArtifacts, self).__init__(sd_manifest, img_id,
                                                  vol_id)
        self._image_dir = self.sd_manifest.getImagePath(img_id)

    def is_garbage(self):
        volatile_img_dir = self.sd_manifest.getDeletedImagePath(self.img_id)
        if self._oop.fileUtils.pathExists(volatile_img_dir):
            return True

        return self._oop.fileUtils.pathExists(self.meta_volatile_path)

    def is_image(self):
        return self._oop.fileUtils.pathExists(self._image_dir)

    @property
    def _oop(self):
        return self.sd_manifest.oop

    @property
    def artifacts_dir(self):
        # If the artifacts are being added to an existing image we can create
        # them in that image directory.  If the artifacts represent the first
        # volume in a new image then use a new temporary image directory.
        if self.is_image():
            return self._image_dir
        else:
            return self.sd_manifest.getDeletedImagePath(self.img_id)

    @property
    def meta_volatile_path(self):
        return self.meta_path + sc.TEMP_VOL_FILEEXT

    @property
    def meta_path(self):
        vol_path = os.path.join(self.artifacts_dir, self.vol_id)
        return self.vol_class.metaVolumePath(vol_path)

    @property
    def lease_path(self):
        return self.vol_class.leaseVolumePath(self.volume_path)

    @property
    def volume_path(self):
        return os.path.join(self.artifacts_dir, self.vol_id)

    def create(self, size, vol_format, disk_type, desc, parent=None,
               initial_size=None):
        """
        Create metadata file artifact, lease file, and volume file on storage.
        """
        prealloc = self._get_volume_preallocation(vol_format)
        self._validate_create_params(vol_format, parent, prealloc)
        if initial_size is not None:
            self.log.debug("initial_size is not supported for file volumes")
            raise se.InvalidParameterException("initial_size", initial_size)
        self._validate_size(size, initial_size, vol_format)

        if not self.is_image():
            self._create_image_artifact()

        self._create_metadata_artifact(size, vol_format, prealloc, disk_type,
                                       desc, parent)
        self._create_lease_file()
        self._create_volume_file(vol_format, size)
        self._initialize_volume(vol_format, size)

    def commit(self):
        try:
            self._oop.os.rename(self.meta_volatile_path, self.meta_path)
        except OSError as e:
            if e.errno == errno.EEXIST:
                raise se.VolumeAlreadyExists("Path %r exists", self.meta_path)
            raise

        # If we created a new image directory, rename it to the correct name
        if not self.is_image():
            self._oop.os.rename(self.artifacts_dir, self._image_dir)

    def _get_volume_preallocation(self, vol_format):
        # File volumes are always sparse regardless of format
        return sc.SPARSE_VOL

    def _create_metadata_artifact(self, size, vol_format, prealloc, disk_type,
                                  desc, parent):
        if self._oop.fileUtils.pathExists(self.meta_path):
            raise se.VolumeAlreadyExists("metadata exists: %r" %
                                         self.meta_path)

        if self._oop.fileUtils.pathExists(self.meta_volatile_path):
            raise se.DomainHasGarbage("metadata artifact exists: %r" %
                                      self.meta_volatile_path)

        parent_vol_id = parent.vol_id if parent else sc.BLANK_UUID
        # Create the metadata artifact.  The metadata file is created with a
        # special extension to prevent these artifacts from being recognized as
        # a volume until FileVolumeArtifacts.commit() is called.
        meta = VolumeMetadata(
            self.sd_manifest.sdUUID,
            self.img_id,
            parent_vol_id,
            size / sc.BLOCK_SIZE,  # Size is stored as number of blocks
            sc.type2name(vol_format),
            sc.type2name(prealloc),
            sc.type2name(sc.LEAF_VOL),
            disk_type,
            desc,
            sc.LEGAL_VOL)
        self._oop.writeFile(self.meta_volatile_path, meta.storage_format())

    def _create_lease_file(self):
        if self.sd_manifest.hasVolumeLeases():
            meta_id = (self.volume_path,)
            self.vol_class.newVolumeLease(meta_id, self.sd_manifest.sdUUID,
                                          self.vol_id)

    def _create_volume_file(self, vol_format, size):
        trunc_size = size if vol_format == sc.RAW_FORMAT else 0
        self._oop.truncateFile(
            self.volume_path, trunc_size,
            mode=sc.FILE_VOLUME_PERMISSIONS, creatExcl=True)

    def _create_image_artifact(self):
        self.log.debug("Creating image artifact directory: %r",
                       self.artifacts_dir)
        try:
            self._oop.os.mkdir(self.artifacts_dir)
        except OSError as e:
            if e.errno != errno.EEXIST:
                raise

            # We have existing artifacts in the way.  Time to run
            # garbage collection
            raise se.DomainHasGarbage("artifacts directory exists: %r" %
                                      self.artifacts_dir)


class BlockVolumeArtifacts(VolumeArtifacts):
    """
    A block based volume can be in one of these states:

    MISSING

    - States:
        - No logical volume exists
    - Operations:
        - is_garbage -> false
        - is_image -> false
        - create artifacts -> change state GARBAGE

    GARBAGE

    - States:
        - A logical volume with the TEMP_VOL_LVTAG tag exists
    - Operations:
        - is_garbage -> true
        - is_image -> true or false
        - clean -> change state to MISSING
        - commit -> change state to VOLUME

    VOLUME

    - States:
        - A logical volume without the TEMP_VOL_LVTAG tag exists
    - Operations:
        - is_garbage -> false
        - is_image -> true
        - create new volume -> change state to GARBAGE
        - destroy this volume -> change state to GARBAGE
    """
    log = logging.getLogger('storage.BlockVolumeArtifacts')

    def __init__(self, sd_manifest, img_id, vol_id):
        self.vol_class = sd_manifest.getVolumeClass()
        super(BlockVolumeArtifacts, self).__init__(sd_manifest, img_id,
                                                   vol_id)

    def is_image(self):
        # This queries the LVM cache and builds a fairly elaborate data
        # structure.  It shouldn't be too expensive and it is not called often.
        # If performance problems arise, we could write some custom code.  For
        # now, we prefer to reuse an existing interface.
        return self.img_id in self.sd_manifest.getAllImages()

    def is_garbage(self):
        try:
            lv = lvm.getLV(self.sd_manifest.sdUUID, self.vol_id)
        except se.LogicalVolumeDoesNotExistError:
            return False
        return sc.TEMP_VOL_LVTAG in lv.tags

    @property
    def volume_path(self):
        return os.path.join(self._get_image_path(), self.vol_id)

    def create(self, size, vol_format, disk_type, desc, parent=None,
               initial_size=None):
        prealloc = self.get_volume_preallocation(vol_format)
        self._validate_create_params(vol_format, parent, prealloc)
        self._validate_size(size, initial_size, vol_format)

        lv_size = self._calculate_volume_alloc_size(prealloc, size,
                                                    initial_size)
        self._create_lv_artifact(parent, lv_size)
        self._create_image_path()
        meta_slot = self._acquire_metadata_slot()
        self._create_metadata(meta_slot, size, vol_format, prealloc, disk_type,
                              desc, parent)
        self._initialize_volume(vol_format, size)
        self._create_lease(meta_slot)

    def commit(self):
        lv = lvm.getLV(self.sd_manifest.sdUUID, self.vol_id)
        if sc.TEMP_VOL_LVTAG not in lv.tags:
            raise se.VolumeAlreadyExists("LV %r has already been committed" %
                                         self.vol_id)
        lvm.changeLVTags(self.sd_manifest.sdUUID, self.vol_id,
                         delTags=(sc.TEMP_VOL_LVTAG,))

    def get_volume_preallocation(self, vol_format):
        if vol_format == sc.RAW_FORMAT:
            return sc.PREALLOCATED_VOL
        else:
            return sc.SPARSE_VOL

    def _calculate_volume_alloc_size(self, prealloc, size, initial_size):
        size_blk = size / sc.BLOCK_SIZE
        initial_size_blk = (None if initial_size is None else
                            initial_size / sc.BLOCK_SIZE)
        return self.vol_class.calculate_volume_alloc_size(
            prealloc, size_blk, initial_size_blk)

    def _create_lv_artifact(self, parent, lv_size):
        try:
            lv = lvm.getLV(self.sd_manifest.sdUUID, self.vol_id)
        except se.LogicalVolumeDoesNotExistError:
            pass
        else:
            if sc.TEMP_VOL_LVTAG in lv.tags:
                raise se.DomainHasGarbage("Logical volume artifact %s exists" %
                                          self.vol_id)
            else:
                raise se.VolumeAlreadyExists("Logical volume %s exists" %
                                             self.vol_id)

        parent_vol_id = parent.vol_id if parent else sc.BLANK_UUID
        tags = (sc.TEMP_VOL_LVTAG,
                sc.TAG_PREFIX_PARENT + parent_vol_id,
                sc.TAG_PREFIX_IMAGE + self.img_id)
        lvm.createLV(self.sd_manifest.sdUUID, self.vol_id, lv_size,
                     activate=True, initialTags=tags)

    def _get_image_path(self):
        return self.sd_manifest.getImageDir(self.img_id)

    def _create_image_path(self):
        image_path = self._get_image_path()
        if not os.path.isdir(image_path):
            self.log.info("Create placeholder %s for image's volumes",
                          image_path)
            os.mkdir(image_path)
        os.symlink(lvm.lvPath(self.sd_manifest.sdUUID, self.vol_id),
                   self.volume_path)

    def _acquire_metadata_slot(self):
        sd_id = self.sd_manifest.sdUUID
        with self.sd_manifest.acquireVolumeMetadataSlot(
                self.vol_id, sc.VOLUME_MDNUMBLKS) as slot:
            md_tag = sc.TAG_PREFIX_MD + str(slot)
            lvm.changeLVTags(sd_id, self.vol_id, addTags=[md_tag])
            return slot

    def _create_metadata(self, meta_slot, size, vol_format, prealloc,
                         disk_type, desc, parent):
        # When the volume format is RAW the real volume capacity is the device
        # size.  The device size may have been rounded up if 'size' is not
        # divisible by the domain's extent size.
        if vol_format == sc.RAW_FORMAT:
            size = int(self.sd_manifest.getVSize(self.img_id, self.vol_id))

        # We use the BlockVolumeManifest API here because we create the
        # metadata in the standard way.  We cannot do this for file volumes
        # because the metadata needs to be written to a specially named file.
        meta_id = (self.sd_manifest.sdUUID, meta_slot)
        parent_vol_id = parent.vol_id if parent else sc.BLANK_UUID
        size_blk = size / sc.BLOCK_SIZE
        self.vol_class.newMetadata(
            meta_id,
            self.sd_manifest.sdUUID,
            self.img_id,
            parent_vol_id,
            size_blk,
            sc.type2name(vol_format),
            sc.type2name(prealloc),
            sc.type2name(sc.LEAF_VOL),
            disk_type,
            desc,
            sc.LEGAL_VOL)

    def _create_lease(self, meta_slot):
        meta_id = (self.sd_manifest.sdUUID, meta_slot)
        self.vol_class.newVolumeLease(
            meta_id, self.sd_manifest.sdUUID, self.vol_id)
