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

import os.path
import logging
from contextlib import contextmanager

from vdsm import utils
from vdsm.common import exception
from vdsm.common.marks import deprecated
from vdsm.common.threadlocal import vars

from vdsm.storage import clusterlock
from vdsm.storage import constants as sc
from vdsm.storage import exception as se
from vdsm.storage import fileUtils
from vdsm.storage import guarded
from vdsm.storage import qemuimg
from vdsm.storage import resourceManager as rm
from vdsm.storage import task
from vdsm.storage.sdc import sdCache
from vdsm.storage.volumemetadata import VolumeMetadata

log = logging.getLogger('storage.Volume')


def getBackingVolumePath(imgUUID, volUUID):
    # We used to return a relative path ../<imgUUID>/<volUUID> but this caused
    # unnecessary growth in the backing chain paths with repeated live merge
    # operations (see https://bugzilla.redhat.com/show_bug.cgi?id=1333627).
    # Since all volumes of an image are in the same directory (including cloned
    # templates which have a hard link on file domains and a symlink on block
    # domains) we do not need to use the image dir reference anymore.
    return volUUID


def _next_generation(current_generation):
    # Increment a generation value and wrap to 0 after MAX_GENERATION
    return (current_generation + 1) % (sc.MAX_GENERATION + 1)


class VolumeManifest(object):
    log = logging.getLogger('storage.VolumeManifest')

    # How this volume is presented to a vm.  Must be overriden in derived
    # classes.
    DISK_TYPE = None

    # The miminal allocation unit; implemented in concrete classes
    align_size = None

    def __init__(self, repoPath, sdUUID, imgUUID, volUUID):
        self.repoPath = repoPath
        self.sdUUID = sdUUID
        self.imgUUID = imgUUID
        self.volUUID = volUUID
        self._volumePath = None
        self._imagePath = None
        self.voltype = None

        if not imgUUID or imgUUID == sc.BLANK_UUID:
            raise se.InvalidParameterException("imgUUID", imgUUID)
        if not volUUID or volUUID == sc.BLANK_UUID:
            raise se.InvalidParameterException("volUUID", volUUID)
        self.validate()

    @property
    def imagePath(self):
        if self._imagePath is None:
            self.validateImagePath()
        return self._imagePath

    @property
    def volumePath(self):
        if self._volumePath is None:
            self.validateVolumePath()
        return self._volumePath

    @volumePath.setter
    def volumePath(self, value):
        self._volumePath = value

    def chunked(self):
        return False

    def validate(self):
        """
        Validate that the volume can be accessed
        """
        self.validateImagePath()
        self.validateVolumePath()

    def getMetaParam(self, key):
        """
        Get a value of a specific key
        """
        meta = self.getMetadata()
        try:
            return meta[key]
        except KeyError:
            raise se.MetaDataKeyNotFoundError(str(meta) + ":" + str(key))

    def getVolumePath(self):
        """
        Get the path of the volume file/link
        """
        if not self._volumePath:
            raise se.VolumeAccessError(self.volUUID)
        return self._volumePath

    def getVolType(self):
        if not self.voltype:
            self.voltype = self.getMetaParam(sc.VOLTYPE)
        return self.voltype

    def isLeaf(self):
        return self.getVolType() == sc.type2name(sc.LEAF_VOL)

    def isShared(self):
        return self.getVolType() == sc.type2name(sc.SHARED_VOL)

    def getDescription(self):
        """
        Return volume description
        """
        return self.getMetaParam(sc.DESCRIPTION)

    def getLegality(self):
        """
        Return volume legality
        """
        try:
            legality = self.getMetaParam(sc.LEGALITY)
            return legality
        except se.MetaDataKeyNotFoundError:
            return sc.LEGAL_VOL

    def isLegal(self):
        try:
            legality = self.getMetaParam(sc.LEGALITY)
            return legality != sc.ILLEGAL_VOL
        except se.MetaDataKeyNotFoundError:
            return True

    def isFake(self):
        try:
            legality = self.getMetaParam(sc.LEGALITY)
            return legality == sc.FAKE_VOL
        except se.MetaDataKeyNotFoundError:
            return False

    def getCapacity(self):
        capacity = int(self.getMetaParam(sc.CAPACITY))
        if capacity < 1:  # Capacity stored in the metadata is not valid
            raise se.MetaDataValidationError()
        return capacity

    def getFormat(self):
        return sc.name2type(self.getMetaParam(sc.FORMAT))

    def getType(self):
        return sc.name2type(self.getMetaParam(sc.TYPE))

    def getDiskType(self):
        return self.getMetaParam(sc.DISKTYPE)

    def isInternal(self):
        return self.getVolType() == sc.type2name(sc.INTERNAL_VOL)

    def isSparse(self):
        return self.getType() == sc.SPARSE_VOL

    def getLeaseStatus(self):
        sd_manifest = sdCache.produce_manifest(self.sdUUID)
        if not sd_manifest.hasVolumeLeases():
            return None

        # Version is always None when a lease is not acquired, although sanlock
        # always report the version. The clusterlock should be fixed to match
        # the schema.
        try:
            version, host_id = sd_manifest.inquireVolumeLease(self.imgUUID,
                                                              self.volUUID)
        except clusterlock.InvalidLeaseName as e:
            self.log.warning("Cannot get lease status: %s", e)
            return None
        except clusterlock.TemporaryFailure as e:
            raise exception.expected(e)

        # TODO: Move this logic to clusterlock and fix callers to handle list
        # of owners instead of None.
        owners = [host_id] if host_id is not None else []
        return dict(owners=owners, version=version)

    def metadata2info(self, meta):
        return {
            "uuid": self.volUUID,
            "type": meta.get(sc.TYPE, ""),
            "format": meta.get(sc.FORMAT, ""),
            "disktype": meta.get(sc.DISKTYPE, ""),
            "voltype": meta.get(sc.VOLTYPE, ""),
            "capacity": meta.get(sc.CAPACITY, "0"),
            "parent": self.getParent(),
            "description": meta.get(sc.DESCRIPTION, ""),
            "pool": "",  # deprecated value
            "domain": meta.get(sc.DOMAIN, ""),
            "image": self.getImage(),
            "ctime": meta.get(sc.CTIME, ""),
            "mtime": "0",
            "legality": meta.get(sc.LEGALITY, ""),
            "generation": meta.get(sc.GENERATION, sc.DEFAULT_GENERATION)
        }

    def getInfo(self):
        """
        Get volume info
        """
        self.log.info("Info request: sdUUID=%s imgUUID=%s volUUID = %s ",
                      self.sdUUID, self.imgUUID, self.volUUID)
        info = {}
        try:
            meta = self.getMetadata()
            info = self.metadata2info(meta)
            # Get the image actual size on disk
            vsize = self.getVolumeSize()
            avsize = self.getVolumeTrueSize()
            info['apparentsize'] = str(vsize)
            info['truesize'] = str(avsize)
            info['status'] = sc.VOL_STATUS_OK
            # 'lease' is an optional property with null as the default value.
            # If volume doesn't have 'lease', we will not return it as part
            # of the volume info.
            sd_manifest = sdCache.produce_manifest(self.sdUUID)
            _, path, offset = sd_manifest.getVolumeLease(self.imgUUID,
                                                         self.volUUID)
            if path is not None:
                leaseinfo = {"path": path, "offset": offset}
                leasestatus = self.getLeaseStatus()
                # If the lease needs repair, its will not be available
                if leasestatus:
                    leaseinfo.update(leasestatus)
                info['lease'] = leaseinfo
        except se.StorageException as e:
            self.log.debug("Failed to get volume info: %s", e)
            info['apparentsize'] = "0"
            info['truesize'] = "0"
            info['status'] = sc.VOL_STATUS_INVALID

        # Both engine and dumpStorageTable don't use this option so
        # only keeping it to not break existing scripts that look for the key
        info['children'] = []

        # If image was set to illegal, mark the status same
        # (because of VDC constraints)
        if info.get('legality', None) == sc.ILLEGAL_VOL:
            info['status'] = sc.ILLEGAL_VOL
        self.log.info("%s/%s/%s info is %s",
                      self.sdUUID, self.imgUUID, self.volUUID, str(info))
        return info

    def getQemuImageInfo(self):
        """
        Returns volume information as returned by qemu-img info command
        """
        # As this helper may be called while the VM is running,
        # use unsafe=True when calling qemuimg.info()
        return qemuimg.info(self.getVolumePath(),
                            sc.fmt2str(self.getFormat()),
                            unsafe=True)

    def getVolumeParams(self):
        volParams = {}
        volParams['volUUID'] = self.volUUID
        volParams['imgUUID'] = self.getImage()
        volParams['path'] = self.getVolumePath()
        volParams['disktype'] = self.getDiskType()
        volParams['prealloc'] = self.getType()
        volParams['volFormat'] = self.getFormat()
        volParams['capacity'] = self.getCapacity()
        volParams['apparentsize'] = self.getVolumeSize()
        volParams['parent'] = self.getParent()
        volParams['descr'] = self.getDescription()
        volParams['legality'] = self.getLegality()
        return volParams

    def getVmVolumeInfo(self):
        """
        Return VM volume information.

        Derived classes may override this if additional information is required
        by the virt layer to present the volume to a VM.
        """
        return {"type": self.DISK_TYPE, "path": self.getVolumePath()}

    def setMetaParam(self, key, value):
        """
        Set a value of a specific key
        """
        meta = self.getMetadata()
        try:
            meta[key] = value
            self.setMetadata(meta)
        except Exception:
            self.log.error("Volume.setMetaParam: %s: %s=%s" %
                           (self.volUUID, key, value))
            raise

    @deprecated  # valid for domain version < 3
    def setrw(self, rw):
        # Since domain version 3 (V3) VDSM is not changing the internal volumes
        # permissions to read-only because it would interfere with the live
        # snapshots and the live merge processes. E.g.: during a live snapshot
        # if the VM is running on the SPM it would lose the ability to write to
        # the current volume.
        # However to avoid lvm MDA corruption we still need to set the volume
        # as read-only on domain version 2. The corruption is triggered on the
        # HSMs that are using the resource manager to prepare the volume chain.
        if int(sdCache.produce(self.sdUUID).getVersion()) < 3:
            self._setrw(rw=rw)

    def setLeaf(self):
        self.setMetaParam(sc.VOLTYPE, sc.type2name(sc.LEAF_VOL))
        self.voltype = sc.type2name(sc.LEAF_VOL)
        self.setrw(rw=True)
        return self.voltype

    def setInternal(self):
        self.setMetaParam(sc.VOLTYPE, sc.type2name(sc.INTERNAL_VOL))
        self.voltype = sc.type2name(sc.INTERNAL_VOL)
        self.setrw(rw=False)
        return self.voltype

    def recheckIfLeaf(self):
        """
        Recheck if I am a leaf.
        """

        if self.isShared():
            return False

        type = self.getVolType()
        childrenNum = len(self.getChildren())

        if childrenNum == 0 and type != sc.LEAF_VOL:
            self.setLeaf()
        elif childrenNum > 0 and type != sc.INTERNAL_VOL:
            self.setInternal()

        return self.isLeaf()

    def setDescription(self, descr):
        """
        Set Volume Description
            'descr' - volume description
        """
        descr = VolumeMetadata.validate_description(descr)
        self.log.info("volUUID = %s descr = %s ", self.volUUID, descr)
        self.setMetaParam(sc.DESCRIPTION, descr)

    def setLegality(self, legality):
        """
        Set Volume Legality
            'legality' - volume legality
        """
        self.log.info("sdUUID=%s imgUUID=%s volUUID = %s legality = %s ",
                      self.sdUUID, self.imgUUID, self.volUUID, legality)
        self.setMetaParam(sc.LEGALITY, legality)

    def setDomain(self, sdUUID):
        self.setMetaParam(sc.DOMAIN, sdUUID)
        self.sdUUID = sdUUID
        return self.sdUUID

    def setShared(self):
        self.setMetaParam(sc.VOLTYPE, sc.type2name(sc.SHARED_VOL))
        self.voltype = sc.type2name(sc.SHARED_VOL)
        self.setrw(rw=False)
        return self.voltype

    def update_attributes(self, generation, vol_attr):
        """
        The required generation argument must match the volume generation.
        Raises se.GenerationMismatch if not.

        When the operation ends, increase the generation of the volume, unless
        it was provided. In this case the generation passed will be used.

        The volume type can be updated only from LEAF to SHARED for volumes
        without a parent.

        The description is limited to 210 bytes. Longer description will be
        truncated.

        The Volume Lease must be held.
        """
        meta = self.getMetadata()

        if generation != meta[sc.GENERATION]:
            raise se.GenerationMismatch(generation, meta[sc.GENERATION])

        if vol_attr.type is not None:
            if meta[sc.VOLTYPE] != sc.type2name(sc.LEAF_VOL):
                raise se.InvalidVolumeUpdate(
                    self.volUUID, "%s Volume cannot be updated to %s"
                                  % (meta[sc.VOLTYPE], vol_attr.type))

            # In block volume the parent is saved in LV tag
            # In File volume, getParent will read the metadata again
            puuid = self.getParent()
            if puuid is not None and puuid != sc.BLANK_UUID:
                raise se.InvalidVolumeUpdate(
                    self.volUUID, "Volume with parent %s cannot update to %s"
                                  % (puuid, vol_attr.type))

            meta[sc.VOLTYPE] = vol_attr.type

        if vol_attr.legality is not None:
            if meta[sc.LEGALITY] == vol_attr.legality:
                raise se.InvalidVolumeUpdate(
                    self.volUUID, "%s Volume cannot be updated to %s"
                                  % (vol_attr.legality, vol_attr.legality))

            meta[sc.LEGALITY] = vol_attr.legality

        if vol_attr.description is not None:
            desc = VolumeMetadata.validate_description(vol_attr.description)
            meta[sc.DESCRIPTION] = desc

        if vol_attr.generation is not None:
            next_gen = vol_attr.generation
        else:
            next_gen = _next_generation(meta[sc.GENERATION])

        meta[sc.GENERATION] = next_gen

        self.log.info("Updating volume attributes on %s"
                      " (generation=%d, attributes=%s)",
                      self.volUUID, next_gen, vol_attr)
        self.setMetadata(meta)

        if vol_attr.type is not None:
            # Note: must match setShared logic
            self.voltype = vol_attr.type

    def setCapacity(self, capacity):
        """
        Sets volume capacity in bytes.

        Arguments:
                capacity (int) - new capacity value in bytes.
        """
        self.setMetaParam(sc.CAPACITY, capacity)

    def updateInvalidatedSize(self):
        """
        Repair volume capacity that may become invalid.

        We know about 2 cases when the volume capacity is invalid:

        - qcow2 volume capacity was set to 0 during Volume.updateSize(), and
          the operation failed, leaving the invalid value on storage.

        - qcow2 volume was created with the wrong capacity, using the parent
          capacity (see https://bugzilla.redhat.com/1700623). This was fixed in
          https://gerrit.ovirt.org/c/99539/ but we have to handle broken
          volumes created by older versions.

        Both issues are relevant only to qcow2 volumes. To keep the code
        simpler and more robust against other cases that we did not discover
        yet, or future bugs, we check and repair also raw volumes. The only
        exception is shared volumes, which must be read-only.

        The prerequisite to run this is that the volume and its metadata are
        accessible.
        """

        # Shared volumes (templates) are immutable and must not be modified.
        if self.isShared():
            return

        # Bypass the size validation in getSize() by using metadata directly.
        capacity = self.getMetadata().capacity

        # We use unsafe here as image may be locked by qemu in some cases, for
        # example when preparing a disk of running VM. However, using unsafe
        # shouldn't cause any harm as virtual size is never changed by qemu.
        # We also don't specify an image format, as some images can have
        # corrupted qcow2 header (see https://bugzilla.redhat.com/1282239).
        qemu_info = qemuimg.info(self.getVolumePath(), unsafe=True)
        virtual_size = qemu_info["virtualsize"]

        # If capacity is smaller than virtual size, creating a snapshot on top
        # of this volume will create qcow2 volume with wrong virtual size. This
        # will corrupt the image later when qemu try to access data beyond the
        # wrong virtual size (https://bugzilla.redhat.com/1700189).
        if capacity < virtual_size:
            self.log.warning(
                "Repairing wrong %s for volume %s stored=%d actual=%d",
                sc.CAPACITY, self.volUUID, capacity, virtual_size)
            self.setMetaParam(sc.CAPACITY, virtual_size)

    def setType(self, prealloc):
        self.setMetaParam(sc.TYPE, sc.type2name(prealloc))

    def setFormat(self, volFormat):
        self.setMetaParam(sc.FORMAT, sc.type2name(volFormat))

    def validateDelete(self):
        """
        Validate volume before deleting
        """
        try:
            if self.isShared():
                raise se.CannotDeleteSharedVolume("img %s vol %s" %
                                                  (self.imgUUID, self.volUUID))
        except se.MetaDataKeyNotFoundError as e:
            # In case of metadata key error, we have corrupted
            # volume (One of metadata corruptions may be
            # previous volume deletion failure).
            # So, there is no reasons to avoid its deletion
            self.log.warning("Volume %s metadata error (%s)",
                             self.volUUID, str(e))
        if self.getChildren():
            raise se.VolumeImageHasChildren(self)

    @classmethod
    def createMetadata(cls, metaId, meta):
        cls._putMetadata(metaId, meta)

    @classmethod
    def newMetadata(cls, metaId, sdUUID, imgUUID, puuid, capacity, format,
                    type, voltype, disktype, desc="", legality=sc.ILLEGAL_VOL):
        meta = VolumeMetadata(sdUUID, imgUUID, puuid, capacity, format, type,
                              voltype, disktype, desc, legality)
        cls.createMetadata(metaId, meta)
        return meta

    def refreshVolume(self):
        pass

    def _shareLease(self, dstImgPath):
        """
        Internal utility method used during the share process and by the
        domain V3 upgrade.
        """
        pass  # Do not remove this method or the V3 upgrade will fail.

    def getParentVolume(self):
        """
        Return parent VolumeManifest object
        """
        puuid = self.getParent()
        if puuid and puuid != sc.BLANK_UUID:
            sd_manifest = sdCache.produce(self.sdUUID).manifest
            return sd_manifest.produceVolume(self.imgUUID, puuid)
        return None

    def prepare(self, rw=True, justme=False,
                chainrw=False, setrw=False, force=False):
        """
        Prepare volume for use by consumer.
        If justme is false, the entire COW chain is prepared.
        Note: setrw arg may be used only by SPM flows.
        """
        self.log.info("Volume: preparing volume %s/%s",
                      self.sdUUID, self.volUUID)

        if not force:
            # Cannot prepare ILLEGAL volume
            if not self.isLegal():
                raise se.prepareIllegalVolumeError(self.volUUID)

            if rw and self.isShared():
                if chainrw:
                    rw = False      # Shared cannot be set RW
                else:
                    raise se.SharedVolumeNonWritable(self)

            if (not chainrw and rw and self.isInternal() and setrw and
                    not self.recheckIfLeaf()):
                raise se.InternalVolumeNonWritable(self)

        self.llPrepare(rw=rw, setrw=setrw)
        self.updateInvalidatedSize()

        try:
            if justme:
                return True
            pvol = self.getParentVolume()
            if pvol:
                pvol.prepare(rw=chainrw, justme=False,
                             chainrw=chainrw, setrw=setrw)
        except Exception:
            self.log.error("Unexpected error", exc_info=True)
            self.teardown(self.sdUUID, self.volUUID)
            raise

        return True

    @classmethod
    def teardown(cls, sdUUID, volUUID, justme=False):
        """
        Teardown volume.
        If justme is false, the entire COW chain is teared down.
        """
        pass

    @contextmanager
    def operation(self, requested_gen=None, set_illegal=True):
        """
        If generation is given check that the volume's generation matches, and
        raise se.GenerationMismatch if not.

        If set_illegal is True, set volume to illegal before starting the
        operation.

        When the operation ends, increase the generation of the volume, and if
        set_illegal was True, mark the volume as legal.

        Must be called with the Volume Lease held.

        In order to detect interrupted datapath operations a volume should be
        marked ILLEGAL prior to the first modification of data and subsequently
        marked LEGAL again once the operation has completed.  Thus, if an
        interruption occurs the volume will remain in an ILLEGAL state.  When
        the volume is legal we want to call Volume.getInfo to determine if this
        operation has not been started or has finished successfully.  We enable
        this by incrementing the generation after the operation completes.

        During some operations the volume is already illegal when we start the
        operation, so we should not modify the legality of the volume during
        the operation. For example, in cold merge and image upload, we set the
        volume to illegal in the beginning of the flow, and set it to legal on
        the end on the flow.

        Example usage::

            with volume.operation(7):
                # volume is illegal here...

            # generation increased to 8

            with volume.operation(8, set_illegal=False):
                # volume legality unchanged

            # generation increased to 9
        """
        actual_gen = self.getMetaParam(sc.GENERATION)
        if requested_gen is not None and actual_gen != requested_gen:
            raise se.GenerationMismatch(requested_gen, actual_gen)
        self.log.info("Starting volume operation on %s (generation=%d, "
                      "set_illegal=%s)",
                      self.volUUID, actual_gen, set_illegal)
        if set_illegal:
            self.setLegality(sc.ILLEGAL_VOL)

        yield
        # Note: We intentionally do not use a try block here because we don't
        # want the following code to run if there was an error.
        #
        # IMPORTANT: In order to provide an atomic state change, both legality
        # and the generation must be updated together in one write.
        next_gen = _next_generation(actual_gen)
        metadata = self.getMetadata()
        if set_illegal:
            metadata[sc.LEGALITY] = sc.LEGAL_VOL
        metadata[sc.GENERATION] = next_gen
        self.setMetadata(metadata)
        self.log.info("Volume operation completed on %s (generation=%d)",
                      self.volUUID, next_gen)

    @classmethod
    def max_size(cls, virtual_size, format):
        """
        Return the required allocation for the provided virtual size.

        Arguments:
            virtual_size (int) - volume virtual size in bytes
            format (int) - sc.RAW_FORMAT or sc.COW_FORMAT

        Returns:
            maximum size of the volume in bytes
        """
        if format == sc.RAW_FORMAT:
            return virtual_size

        # TODO: use qemu-img measure instead of sc.COW_OVERHEAD.
        return utils.round(virtual_size * sc.COW_OVERHEAD, cls.align_size)

    def removeMetadata(self, metaId=None):
        raise NotImplementedError

    @classmethod
    def _putMetadata(cls, metaId, meta, **overrides):
        raise NotImplementedError

    @classmethod
    def getImageVolumes(cls, sdUUID, imgUUID):
        raise NotImplementedError

    @classmethod
    def newVolumeLease(cls, metaId, sdUUID, volUUID):
        raise NotImplementedError

    @classmethod
    def leaseVolumePath(cls, vol_path):
        raise NotImplementedError

    @property
    def oop(self):
        raise NotImplementedError

    def validateImagePath(self):
        raise NotImplementedError

    def validateVolumePath(self):
        raise NotImplementedError

    def getMetadataId(self):
        raise NotImplementedError

    def getMetadata(self, metaId=None):
        raise NotImplementedError

    def getParent(self):
        raise NotImplementedError

    def getChildren(self):
        raise NotImplementedError

    def getImage(self):
        raise NotImplementedError

    def getVolumeSize(self):
        raise NotImplementedError

    def getVolumeTrueSize(self):
        raise NotImplementedError

    def setMetadata(self, meta, metaId=None, **overrides):
        raise NotImplementedError

    @deprecated  # valid only for domain version < 3, see volume.setrw
    def _setrw(self, rw):
        raise NotImplementedError

    def llPrepare(self, rw=False, setrw=False):
        raise NotImplementedError

    def optimal_size(self):
        raise NotImplementedError

    def _share(self, dstImgPath):
        raise NotImplementedError

    # Implemented only in block storage

    def getDevPath(self):
        raise NotImplementedError

    def getVolumeTag(self, tagPrefix):
        raise NotImplementedError

    def changeVolumeTag(self, tagPrefix, uuid):
        raise NotImplementedError

    def getParentMeta(self):
        raise NotImplementedError

    def setParentMeta(self, puuid):
        raise NotImplementedError

    def getParentTag(self):
        raise NotImplementedError

    def setParentTag(self, puuid):
        raise NotImplementedError

    def getMetaSlot(self):
        raise NotImplementedError

    # Copy volume helpers.

    def requires_create(self):
        """
        Return True if we need to use qemuimg.convert(create=True) when this
        volume is the target image.

        We have 2 cases:

        1. Raw sparse image on filesystem not supporting punching holes (e.g.
           NFS < 4.2). qemu-img convert will fully allocate the entire image
           when trying to punch holes in the unallocated areas.

        2. qcow2 compat=0.10 when volume does not have a parent. qemu-img
           convert will fully allocate the image instead of skipping the
           unallocated areas.
           TODO: Remove when qemu-5.1.0 is available.
           https://bugzilla.redhat.com/1858632

        When qemu-img convert creates the target image it knows that the image
        is zeroed so it can skip the unallocated areas.
        """
        if self.getFormat() == sc.RAW_FORMAT:
            return self.isSparse()
        else:
            puuid = self.getParent()
            if puuid and puuid != sc.BLANK_UUID:
                return False

            dom = sdCache.produce(self.sdUUID)
            return dom.qcow2_compat() == "0.10"


class Volume(object):
    log = logging.getLogger('storage.Volume')
    manifestClass = VolumeManifest

    @classmethod
    def _create(cls, dom, imgUUID, volUUID, capacity, volFormat, preallocate,
                volParent, srcImgUUID, srcVolUUID, volPath, initial_size=None):
        raise NotImplementedError

    def __init__(self, repoPath, sdUUID, imgUUID, volUUID):
        self._manifest = self.manifestClass(repoPath, sdUUID, imgUUID, volUUID)

    @property
    def sdUUID(self):
        return self._manifest.sdUUID

    @property
    def imgUUID(self):
        return self._manifest.imgUUID

    @property
    def volUUID(self):
        return self._manifest.volUUID

    @property
    def repoPath(self):
        return self._manifest.repoPath

    @property
    def volumePath(self):
        return self._manifest.volumePath

    @property
    def imagePath(self):
        return self._manifest.imagePath

    @property
    def voltype(self):
        return self._manifest.voltype

    def getMetadataId(self):
        return self._manifest.getMetadataId()

    def getMetadata(self, metaId=None):
        """
        Get Meta data array of key,values lines
        """
        return self._manifest.getMetadata(metaId)

    def getParent(self):
        """
        Return parent volume UUID
        """
        return self._manifest.getParent()

    def getChildren(self):
        """ Return children volume UUIDs.

        Children can be found in any image of the volume SD.
        """
        return self._manifest.getChildren()

    def getImage(self):
        return self._manifest.getImage()

    @deprecated  # valid only for domain version < 3, see volume.setrw
    def _setrw(self, rw):
        """
        Set the read/write permission on the volume (deprecated)
        """
        self._manifest._setrw(rw)

    def _share(self, dstImgPath):
        return self._manifest._share(dstImgPath)

    @classmethod
    def _putMetadata(cls, metaId, meta, **overrides):
        cls.manifestClass._putMetadata(metaId, meta, **overrides)

    def setMetadata(self, meta, metaId=None, **overrides):
        return self._manifest.setMetadata(meta, metaId, **overrides)

    @classmethod
    def _getModuleAndClass(cls):
        clsName = cls.__name__
        clsModule = cls.__module__.split(".").pop()
        return clsModule, clsName

    def validate(self):
        """
        Validate that the volume can be accessed
        """
        self._manifest.validateImagePath()
        self._manifest.validateVolumePath()

    def __str__(self):
        return str(self.volUUID)

    # Even if it's not in use anymore we cannot remove this method because
    # we might have persisted recovery on storage calling it.
    # TODO: remove this in the next version.
    @classmethod
    def killProcRollback(cls, taskObj, pid, ctime):
        cls.log.info('ignoring killProcRollback request for pid %s and '
                     'ctime %s', pid, ctime)

    @classmethod
    # metaID0 is different between file and block volumes. For file volumes,
    # the value is the volume path, while for block volumes, the value is the
    # storage domain UUID.
    def renameLeaseRollback(cls, taskObj, metaID0, leaseOffset, sdUUID,
                            volUUID):
        cls.log.info("Rolling back lease rename (metaID0=%s, "
                     "leaseOffset=%s, sdUUID=%s, volUUID=%s)",
                     metaID0, leaseOffset, sdUUID, volUUID)
        try:
            metaID = (metaID0, int(leaseOffset))
            cls.newVolumeLease(metaID, sdUUID, volUUID)
        except Exception:
            cls.log.exception("Could not rollback lease rename (metaID=%s, "
                              "sdUUID=%s, volUUID=%s)",
                              metaID, sdUUID, volUUID)

    def renameLease(self, metaID, newUUID, recovery=True):
        self.log.debug("Renaming volume lease %s to %s",
                       self.volUUID, newUUID)
        if recovery:
            clsModule, clsName = self._getModuleAndClass()
            vars.task.pushRecovery(
                task.Recovery(
                    "Rename lease rollback: " + newUUID,
                    clsModule,
                    clsName,
                    "renameLeaseRollback",
                    # Convert metaID to strings because task.Recovery supports
                    # only string types.
                    [metaID[0], str(metaID[1]), self.sdUUID, self.volUUID]))
        self.newVolumeLease(metaID, self.sdUUID, newUUID)

    def clone(self, dstPath, volFormat, capacity):
        """
        Clone self volume to the specified dst_image_dir/dst_volUUID
        """
        wasleaf = False
        taskName = "parent volume rollback: " + self.volUUID
        vars.task.pushRecovery(
            task.Recovery(taskName, "volume", "Volume",
                          "parentVolumeRollback",
                          [self.sdUUID, self.imgUUID, self.volUUID]))
        if self.isLeaf():
            wasleaf = True
            self.setInternal()
        try:
            self.log.debug('cloning volume %s to %s', self.volumePath,
                           dstPath)
            parent = getBackingVolumePath(self.imgUUID, self.volUUID)
            domain = sdCache.produce(self.sdUUID)
            # Using unsafe=True in order to create volumes when the backing
            # chain isn't available. In this case qemu-img cannot get the size,
            # hence we have to provide it.
            operation = qemuimg.create(
                dstPath,
                size=capacity,
                backing=parent,
                format=sc.fmt2str(volFormat),
                qcow2Compat=domain.qcow2_compat(),
                backingFormat=sc.fmt2str(self.getFormat()),
                unsafe=True)
            operation.run()
        except Exception as e:
            self.log.exception('cannot clone image %s volume %s to %s',
                               self.imgUUID, self.volUUID, dstPath)
            # FIXME: might race with other clones
            if wasleaf:
                self.setLeaf()
            raise se.CannotCloneVolume(self.volumePath, dstPath, str(e))

    def _shareLease(self, dstImgPath):
        self._manifest._shareLease(dstImgPath)

    def share(self, dstImgPath):
        """
        Share this volume to dstImgPath
        """
        self.log.debug("Share volume %s to %s", self.volUUID, dstImgPath)

        if not self.isShared():
            raise se.VolumeNonShareable(self)

        if os.path.basename(dstImgPath) == os.path.basename(self.imagePath):
            raise se.VolumeOwnershipError(self)

        dstPath = os.path.join(dstImgPath, self.volUUID)
        clsModule, clsName = self._getModuleAndClass()

        try:
            vars.task.pushRecovery(
                task.Recovery("Share volume rollback: %s" % dstPath, clsModule,
                              clsName, "shareVolumeRollback", [dstPath])
            )

            self._share(dstImgPath)

        except Exception as e:
            raise se.CannotShareVolume(self.getVolumePath(), dstPath, str(e))

    def refreshVolume(self):
        return self._manifest.refreshVolume()

    @classmethod
    def parentVolumeRollback(cls, taskObj, sdUUID, pimgUUID, pvolUUID):
        cls.log.info("parentVolumeRollback: sdUUID=%s pimgUUID=%s"
                     " pvolUUID=%s" % (sdUUID, pimgUUID, pvolUUID))
        if pvolUUID != sc.BLANK_UUID and pimgUUID != sc.BLANK_UUID:
            pvol = sdCache.produce(sdUUID).produceVolume(pimgUUID,
                                                         pvolUUID)
            pvol.prepare()
            try:
                pvol.recheckIfLeaf()
            except Exception:
                cls.log.error("Unexpected error", exc_info=True)
            finally:
                pvol.teardown(sdUUID, pvolUUID)

    @classmethod
    def startCreateVolumeRollback(cls, taskObj, sdUUID, imgUUID, volUUID):
        cls.log.info("startCreateVolumeRollback: sdUUID=%s imgUUID=%s "
                     "volUUID=%s " % (sdUUID, imgUUID, volUUID))
        # This rollback doesn't actually do anything.
        # In general the createVolume rollbacks are a list of small rollbacks
        # that are replaced by the one major rollback at the end of the task.
        # This rollback is a simple marker that must be the first rollback
        # in the list of createVolume rollbacks.
        # We need it in cases when createVolume is part of a composite task and
        # not a task by itself. In such cases when we will replace the list of
        # small rollbacks with the major one, we want to be able remove only
        # the relevant rollbacks from the rollback list.
        pass

    @classmethod
    def createVolumeRollback(cls, taskObj, repoPath,
                             sdUUID, imgUUID, volUUID, imageDir):
        cls.log.info("createVolumeRollback: repoPath=%s sdUUID=%s imgUUID=%s "
                     "volUUID=%s imageDir=%s" %
                     (repoPath, sdUUID, imgUUID, volUUID, imageDir))
        vol = sdCache.produce(sdUUID).produceVolume(imgUUID, volUUID)
        pvol = vol.getParentVolume()
        # Remove volume
        vol.delete(postZero=False, force=True, discard=False)
        if len(cls.getImageVolumes(sdUUID, imgUUID)):
            # Don't remove the image folder itself
            return

        if not pvol or pvol.isShared():
            # Remove image folder with all leftovers
            if os.path.exists(imageDir):
                cls.log.info("Removing image directory %r", imageDir)
                fileUtils.cleanupdir(imageDir)

    @classmethod
    def create(cls, repoPath, sdUUID, imgUUID, capacity, volFormat,
               preallocate, diskType, volUUID, desc, srcImgUUID, srcVolUUID,
               initial_size=None):
        """
        Create a new volume with given size or snapshot
            'capacity' - in bytes
            'volFormat' - volume format COW / RAW
            'preallocate' - Preallocate / Sparse
            'diskType' - enum (vdsm.storage.constants.VOL_DISKTYPE)
            'srcImgUUID' - source image UUID
            'srcVolUUID' - source volume UUID
            'initial_size' - initial volume size in bytes,
                             in case of thin provisioning
        """
        # Do the input values validation first.
        if initial_size is not None:
            if initial_size < 0:
                cls.log.error("initial_size %d is negative", initial_size)
                raise se.InvalidParameterException(
                    "initial size", initial_size)

        # Round size and initial size to block size. To make code simple,
        # always round to 4k.
        # TODO: round the value to cls.align_size so that we can remove
        # block updating capacity for RAW volume type bellow.
        capacity = utils.round(capacity, sc.BLOCK_SIZE_4K)
        if initial_size is not None:
            initial_size = utils.round(initial_size, sc.BLOCK_SIZE_4K)

        dom = sdCache.produce(sdUUID)
        dom.validateCreateVolumeParams(
            volFormat, srcVolUUID, diskType=diskType, preallocate=preallocate)

        imgPath = dom.create_image(imgUUID)

        volPath = os.path.join(imgPath, volUUID)
        volParent = None
        volType = sc.type2name(sc.LEAF_VOL)

        # Get the specific class name and class module to be used in the
        # Recovery tasks.
        clsModule, clsName = cls._getModuleAndClass()

        try:
            if srcVolUUID != sc.BLANK_UUID:
                # When the srcImgUUID isn't specified we assume it's the same
                # as the imgUUID
                if srcImgUUID == sc.BLANK_UUID:
                    srcImgUUID = imgUUID

                volParent = cls(repoPath, sdUUID, srcImgUUID, srcVolUUID)

                if not volParent.isLegal():
                    raise se.createIllegalVolumeSnapshotError(
                        volParent.volUUID)

                if imgUUID != srcImgUUID:
                    volParent.share(imgPath)
                    volParent = cls(repoPath, sdUUID, imgUUID, srcVolUUID)

        except se.StorageException:
            cls.log.error("Unexpected error", exc_info=True)
            raise
        except Exception as e:
            cls.log.error("Unexpected error", exc_info=True)
            raise se.VolumeCannotGetParent(
                "Couldn't get parent %s for volume %s: %s" %
                (srcVolUUID, volUUID, e))

        if volParent:
            # Requested capacity must not be smaller then parent capacity,
            # as this will corrupt the new volume when qemu will try to
            # access areas beyond the volume virtual size.
            if capacity < volParent.getCapacity():
                cls.log.error(
                    "Requested capacity %d < parent capacity %d",
                    capacity, volParent.getCapacity())
                raise se.InvalidParameterException("capacity", capacity)

        try:
            cls.log.info("Creating volume %s", volUUID)

            # Rollback sentinel to mark the start of the task
            vars.task.pushRecovery(
                task.Recovery(task.ROLLBACK_SENTINEL, clsModule, clsName,
                              "startCreateVolumeRollback",
                              [sdUUID, imgUUID, volUUID])
            )

            # Create volume rollback
            vars.task.pushRecovery(
                task.Recovery("Halfbaked volume rollback", clsModule, clsName,
                              "halfbakedVolumeRollback",
                              [sdUUID, volUUID, volPath])
            )

            # Specific volume creation (block, file, etc...)
            try:
                metaId = cls._create(dom, imgUUID, volUUID, capacity,
                                     volFormat, preallocate, volParent,
                                     srcImgUUID, srcVolUUID, volPath,
                                     initial_size=initial_size)
            except (se.VolumeAlreadyExists, se.CannotCreateLogicalVolume,
                    se.VolumeCreationError, se.InvalidParameterException) as e:
                cls.log.error("Failed to create volume %s: %s", volPath, e)
                vars.task.popRecovery()
                raise
            # When the volume format is raw what the guest sees is the apparent
            # size of the file/device therefore if the requested size doesn't
            # match the apparent size (eg: physical extent granularity in LVM)
            # we need to update the size value so that the metadata reflects
            # the correct state.
            if volFormat == sc.RAW_FORMAT:
                apparent_size = dom.getVSize(imgUUID, volUUID)
                if apparent_size < capacity:
                    cls.log.error("The volume %s apparent size %s is "
                                  "smaller than the requested capacity %s",
                                  volUUID, apparent_size, capacity)
                    raise se.VolumeCreationError()
                if apparent_size > capacity:
                    cls.log.info("The requested size for volume %s doesn't "
                                 "match the granularity on domain %s, updating"
                                 " the volume capacity from %s to %s",
                                 volUUID, sdUUID, capacity, apparent_size)
                    capacity = apparent_size

            vars.task.pushRecovery(
                task.Recovery("Create volume metadata rollback", clsModule,
                              clsName, "createVolumeMetadataRollback",
                              [str(x) for x in metaId])
            )

            cls.newMetadata(metaId, sdUUID, imgUUID, srcVolUUID, capacity,
                            sc.type2name(volFormat), sc.type2name(preallocate),
                            volType, diskType, desc, sc.LEGAL_VOL)

            if dom.hasVolumeLeases():
                cls.newVolumeLease(metaId, sdUUID, volUUID)

        except se.StorageException:
            cls.log.error("Unexpected error", exc_info=True)
            raise
        except Exception as e:
            cls.log.error("Unexpected error", exc_info=True)
            raise se.VolumeCreationError("Volume creation %s failed: %s" %
                                         (volUUID, e))

        # Remove the rollback for the halfbaked volume
        vars.task.replaceRecoveries(
            task.Recovery("Create volume rollback", clsModule, clsName,
                          "createVolumeRollback",
                          [repoPath, sdUUID, imgUUID, volUUID, imgPath])
        )

        return volUUID

    def validateDelete(self):
        self._manifest.validateDelete()

    def extend(self, new_size):
        """
        Extend the apparent size of logical volume (thin provisioning)
        """
        pass

    def reduce(self, new_size, allowActive=False):
        """
        reduce a logical volume
        """
        pass

    def syncMetadata(self):
        volFormat = self.getFormat()
        if volFormat != sc.RAW_FORMAT:
            self.log.error("impossible to update metadata for volume %s "
                           "its format is not RAW", self.volUUID)
            return

        new_vol_capacity = self.getVolumeSize()
        old_vol_capacity = self.getCapacity()

        if old_vol_capacity == new_vol_capacity:
            self.log.debug("capacity metadata %s is up to date for volume %s",
                           old_vol_capacity, self.volUUID)
        else:
            self.log.debug("updating metadata for volume %s changing the "
                           "capacity %s to %s", self.volUUID, old_vol_capacity,
                           new_vol_capacity)
            self.setCapacity(new_vol_capacity)

    @classmethod
    def extendSizeFinalize(cls, taskObj, sdUUID, imgUUID, volUUID):
        cls.log.debug("finalizing size extension for volume %s on domain "
                      "%s", volUUID, sdUUID)
        # The rollback consists in just updating the metadata to be
        # consistent with the volume real/virtual size.
        sdCache.produce(sdUUID) \
               .produceVolume(imgUUID, volUUID).syncMetadata()

    def extendSize(self, new_capacity):
        """
        Extend the size (virtual disk size seen by the guest) of the volume.
        """
        if self.isShared():
            raise se.VolumeNonWritable(self.volUUID)

        volFormat = self.getFormat()
        if volFormat == sc.COW_FORMAT:
            self.log.debug("skipping cow size extension for volume %s to "
                           "capacity %s", self.volUUID, new_capacity)
            return
        elif volFormat != sc.RAW_FORMAT:
            raise se.IncorrectFormat(self.volUUID)

        # Note: This function previously prohibited extending non-leaf volumes.
        # If a disk is enlarged a volume may become larger than its parent.  In
        # order to support live merge of a larger volume into its raw parent we
        # must permit extension of this raw volume prior to starting the merge.
        isBase = self.getParent() == sc.BLANK_UUID
        if not (isBase or self.isLeaf()):
            raise se.VolumeNonWritable(self.volUUID)

        cur_raw_capacity = self.getVolumeSize()

        if new_capacity < cur_raw_capacity:
            self.log.error("current capacity of volume %s is larger than the "
                           "capacity requested in the extension (%s > %s)",
                           self.volUUID, cur_raw_capacity, new_capacity)
            raise se.VolumeResizeValueError(new_capacity)

        if new_capacity == cur_raw_capacity:
            self.log.debug("the requested capacity %s is equal to the current "
                           "capacity %s, skipping extension", new_capacity,
                           cur_raw_capacity)
        else:
            self.log.info("executing a raw capacity extension for volume %s "
                          "from capacity %s to capacity %s", self.volUUID,
                          cur_raw_capacity, new_capacity)
            vars.task.pushRecovery(task.Recovery(
                "Extend size for volume: " + self.volUUID, "volume",
                "Volume", "extendSizeFinalize",
                [self.sdUUID, self.imgUUID, self.volUUID]))
            self._extendSizeRaw(new_capacity)

        self.syncMetadata()  # update the metadata

    def setDescription(self, descr):
        self._manifest.setDescription(descr)

    def getDescription(self):
        return self._manifest.getDescription()

    def getLegality(self):
        return self._manifest.getLegality()

    def setLegality(self, legality):
        self._manifest.setLegality(legality)

    def setDomain(self, sdUUID):
        return self._manifest.setDomain(sdUUID)

    def setShared(self):
        return self._manifest.setShared()

    @deprecated  # valid for domain version < 3
    def setrw(self, rw):
        self._manifest.setrw(rw)

    def setLeaf(self):
        return self._manifest.setLeaf()

    def setInternal(self):
        return self._manifest.setInternal()

    def getVolType(self):
        return self._manifest.getVolType()

    def getCapacity(self):
        return self._manifest.getCapacity()

    @classmethod
    def max_size(cls, virtual_size, format):
        return cls.manifestClass.max_size(virtual_size, format)

    def optimal_size(self):
        return self._manifest.optimal_size()

    def getVolumeSize(self):
        return self._manifest.getVolumeSize()

    def getVolumeTrueSize(self):
        return self._manifest.getVolumeTrueSize()

    def setCapacity(self, capacity):
        self._manifest.setCapacity(capacity)

    def updateInvalidatedSize(self):
        self._manifest.updateInvalidatedSize()

    def getType(self):
        return self._manifest.getType()

    def setType(self, prealloc):
        self._manifest.setType(prealloc)

    def getDiskType(self):
        return self._manifest.getDiskType()

    def getFormat(self):
        return self._manifest.getFormat()

    def setFormat(self, volFormat):
        self._manifest.setFormat(volFormat)

    def isLegal(self):
        return self._manifest.isLegal()

    def isFake(self):
        return self._manifest.isFake()

    def isShared(self):
        return self._manifest.isShared()

    def isLeaf(self):
        return self._manifest.isLeaf()

    def isInternal(self):
        return self._manifest.isInternal()

    def isSparse(self):
        return self._manifest.isSparse()

    def recheckIfLeaf(self):
        """
        Recheck if I am a leaf.
        """
        return self._manifest.recheckIfLeaf()

    def prepare(self, rw=True, justme=False,
                chainrw=False, setrw=False, force=False):
        return self._manifest.prepare(rw, justme, chainrw, setrw, force)

    @classmethod
    def teardown(cls, sdUUID, volUUID, justme=False):
        return cls.manifestClass.teardown(sdUUID, volUUID, justme)

    def metadata2info(self, meta):
        return self._manifest.metadata2info(meta)

    @classmethod
    def newMetadata(cls, metaId, sdUUID, imgUUID, puuid, capacity, format,
                    type, voltype, disktype, desc="", legality=sc.ILLEGAL_VOL):
        return cls.manifestClass.newMetadata(
            metaId, sdUUID, imgUUID, puuid, capacity, format, type, voltype,
            disktype, desc, legality)

    def getInfo(self):
        return self._manifest.getInfo()

    def getQemuImageInfo(self):
        return self._manifest.getQemuImageInfo()

    def getParentVolume(self):
        """
        Return parent Volume object
        """
        puuid = self.getParent()
        if puuid and puuid != sc.BLANK_UUID:
            return sdCache.produce(self.sdUUID).produceVolume(self.imgUUID,
                                                              puuid)
        return None

    def setParent(self, puuid):
        """
        Set the parent volume UUID.  This information can be stored in multiple
        places depending on the underlying volume type.
        """
        self.setParentTag(puuid)
        self.setParentMeta(puuid)

    def getVolumePath(self):
        return self._manifest.getVolumePath()

    def getVmVolumeInfo(self):
        return self._manifest.getVmVolumeInfo()

    def getMetaParam(self, key):
        """
        Get a value of a specific key
        """
        return self._manifest.getMetaParam(key)

    def setMetaParam(self, key, value):
        """
        Set a value of a specific key
        """
        self._manifest.setMetaParam(key, value)

    def getVolumeParams(self):
        return self._manifest.getVolumeParams()

    def chunked(self):
        return self._manifest.chunked()

    @classmethod
    def createMetadata(cls, metaId, meta):
        return cls.manifestClass.createMetadata(metaId, meta)

    @classmethod
    def newVolumeLease(cls, metaId, sdUUID, volUUID):
        return cls.manifestClass.newVolumeLease(metaId, sdUUID, volUUID)

    @classmethod
    def getImageVolumes(cls, sdUUID, imgUUID):
        return cls.manifestClass.getImageVolumes(sdUUID, imgUUID)

    def _extendSizeRaw(self, newSize):
        raise NotImplementedError

    # Used only for block volume

    def setParentMeta(self, puuid):
        raise NotImplementedError

    def setParentTag(self, puuid):
        raise NotImplementedError

    def requires_create(self):
        return self._manifest.requires_create()


class VolumeLease(guarded.AbstractLock):
    """
    Extend AbstractLock so Volume Leases may be used with guarded utilities.
    """
    def __init__(self, host_id, sd_id, img_id, vol_id):
        self._host_id = host_id
        self._sd_id = sd_id
        self._img_id = img_id
        self._vol_id = vol_id

    @property
    def ns(self):
        return rm.getNamespace(sc.VOLUME_LEASE_NAMESPACE, self._sd_id)

    @property
    def name(self):
        return self._vol_id

    @property
    def mode(self):
        return rm.EXCLUSIVE  # All volume leases are exclusive

    def acquire(self):
        dom = sdCache.produce_manifest(self._sd_id)
        dom.acquireVolumeLease(self._host_id, self._img_id, self._vol_id)

    def release(self):
        dom = sdCache.produce_manifest(self._sd_id)
        dom.releaseVolumeLease(self._img_id, self._vol_id)
