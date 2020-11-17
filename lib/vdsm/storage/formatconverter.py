#
# Copyright 2012-2018 Red Hat, Inc.
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

"""
FormatConverter - Responsible for the storage domains' version.

A storage domain's version reflects its meta data compatibility.
This module is usually being used for upgrade process, once a Data Center
will be able to use the new functionaility reflected by its storage domain's
new version.
Supported storage formats - Data storage domain should be supported
for version 3 and 4.
Version 4 is different from version 3 by supporting qcow version 1.1 as
well the old qcow 0.10 version which was supported in version 3 and below.
"""
from __future__ import absolute_import
from __future__ import division

import logging

import six

from vdsm.common import cmdutils
from vdsm.common.units import MiB
from vdsm.storage import constants as sc
from vdsm.storage import exception as se
from vdsm.storage import qemuimg
from vdsm.storage import sd

log = logging.getLogger("storage.format")


def _v3_reset_meta_volsize(vol):
    """
    This function should only be used inside of v2->v3 domain format
    conversion flow.
    It measures size of a volume and updates metadata to match it.
    We usually use metadata as a authoritative source, but in
    this case we break that rule.

    Arguments:
        vol (Volume): Volume to reset
    """

    # BZ811880 Verifying that the volume size is the same size advertised
    # by the metadata
    log.debug("Checking the volume size for the volume %s", vol.volUUID)

    meta_vol_size = int(vol.getMetaParam(sc.CAPACITY))

    if vol.getFormat() == sc.COW_FORMAT:
        qemuVolInfo = qemuimg.info(vol.getVolumePath(),
                                   qemuimg.FORMAT.QCOW2)
        virtual_vol_size = qemuVolInfo["virtual-size"]
    else:
        virtual_vol_size = vol.getVolumeSize()

    if meta_vol_size != virtual_vol_size:
        log.warning("Fixing the mismatch between the metadata volume size "
                    "(%s) and the volume virtual size (%s) for the volume "
                    "%s", meta_vol_size, virtual_vol_size, vol.volUUID)
        vol.setMetaParam(sc.CAPACITY, virtual_vol_size)


def v3DomainConverter(repoPath, hostId, domain, isMsd):
    targetVersion = 3
    currentVersion = domain.getVersion()

    log.debug("Starting conversion for domain %s from version %s "
              "to version %s", domain.sdUUID, currentVersion, targetVersion)

    # For block domains if we're upgrading from version 0 we need to first
    # upgrade to version 2 and then proceed to upgrade to version 3.
    if domain.getStorageType() in sd.BLOCK_DOMAIN_TYPES:
        if currentVersion != 2:
            log.debug("Unsupported conversion from version %s to version %s",
                      currentVersion, targetVersion)
            raise se.UnsupportedDomainVersion(currentVersion)

    if domain.getStorageType() in sd.FILE_DOMAIN_TYPES:
        log.debug("Setting permissions for domain %s", domain.sdUUID)
        domain.setMetadataPermissions()

    log.debug("Initializing the new cluster lock for domain %s", domain.sdUUID)
    newClusterLock = domain._makeClusterLock(targetVersion)
    newClusterLock.initLock(domain.getClusterLease())

    log.debug("Acquiring the host id %s for domain %s", hostId, domain.sdUUID)
    newClusterLock.acquireHostId(hostId, wait=True)

    def v3UpgradeVolumePermissions(vol):
        log.debug("Changing permissions (read-write) for the "
                  "volume %s", vol.volUUID)
        # Using the internal call to skip the domain V3 validation,
        # see volume.setrw for more details.
        vol._setrw(True)

    def v3ReallocateMetadataSlot(domain, allVolumes):
        if not domain.getStorageType() in sd.BLOCK_DOMAIN_TYPES:
            log.debug("The metadata reallocation check is not needed for "
                      "domain %s", domain.sdUUID)
            return

        leasesSize = domain.getLeasesFileSize() // MiB
        metaMaxSlot = leasesSize - sd.RESERVED_LEASES - 1

        log.debug("Starting metadata reallocation check for domain %s with "
                  "metaMaxSlot %s (leases volume size %s)", domain.sdUUID,
                  metaMaxSlot, leasesSize)

        # Updating the volumes one by one, doesn't require activation
        for volUUID, (imgUUIDs, parentUUID) in six.iteritems(allVolumes):
            # The first imgUUID is the imgUUID of the template or the only
            # imgUUID where the volUUID appears.
            vol = domain.produceVolume(imgUUIDs[0], volUUID)
            metaSlot = vol.getMetaSlot()

            if metaSlot < metaMaxSlot:
                continue

            log.debug("Reallocating metadata slot %s for volume %s",
                      metaSlot, vol.volUUID)
            metaContent = vol.getMetadata()

            with domain.acquireVolumeMetadataSlot(vol.volUUID) as newMetaSlot:
                if newMetaSlot > metaMaxSlot:
                    raise se.NoSpaceLeftOnDomain(domain.sdUUID)

                log.debug("Copying metadata for volume %s to the new slot %s",
                          vol.volUUID, newMetaSlot)
                vol.createMetadata((domain.sdUUID, newMetaSlot), metaContent)

                log.debug("Switching the metadata slot for volume %s to %s",
                          vol.volUUID, newMetaSlot)
                vol.changeVolumeTag(sc.TAG_PREFIX_MD, str(newMetaSlot))

    try:
        if isMsd:
            log.debug("Acquiring the cluster lock for domain %s with "
                      "host id: %s", domain.sdUUID, hostId)
            newClusterLock.acquire(hostId, domain.getClusterLease())

        allVolumes = domain.getAllVolumes()
        allImages = {}  # {images: parent_image}

        # Few vdsm releases (4.9 prior 496c0c3, BZ#732980) generated metadata
        # offsets higher than 1947 (LEASES_SIZE - RESERVED_LEASES - 1).
        # This function reallocates such slots to free ones in order to use the
        # same offsets for the volume resource leases.
        v3ReallocateMetadataSlot(domain, allVolumes)

        # Updating the volumes one by one, doesn't require activation
        for volUUID, (imgUUIDs, parentUUID) in six.iteritems(allVolumes):
            log.debug("Converting volume: %s", volUUID)

            # Maintaining a dict of {images: parent_image}
            allImages.update((i, None) for i in imgUUIDs)

            # The first imgUUID is the imgUUID of the template or the
            # only imgUUID where the volUUID appears.
            vol = domain.produceVolume(imgUUIDs[0], volUUID)
            v3UpgradeVolumePermissions(vol)

            log.debug("Creating the volume lease for %s", volUUID)
            metaId = vol.getMetadataId()
            vol.newVolumeLease(metaId, domain.sdUUID, volUUID)

            # If this volume is used as a template let's update the other
            # volume's permissions and share the volume lease (at the moment
            # of this writing this is strictly needed only on file domains).
            for imgUUID in imgUUIDs[1:]:
                allImages[imgUUID] = imgUUIDs[0]
                dstVol = domain.produceVolume(imgUUID, volUUID)

                v3UpgradeVolumePermissions(dstVol)

                # Sharing the original template volume lease file with the
                # same volume in the other images.
                vol._shareLease(dstVol.imagePath)

        # Updating the volumes to fix BZ#811880, here the activation is
        # required and to be more effective we do it by image (one shot).
        for imgUUID in allImages:
            log.debug("Converting image: %s", imgUUID)

            # XXX: The only reason to prepare the image is to verify the volume
            # virtual size configured in the qcow2 header (BZ#811880).
            # The activation and deactivation of the LVs might lead to a race
            # with the creation or destruction of a VM on the SPM.
            #
            # The analyzed scenarios are:
            #  1. A VM is currently running on the image we are preparing.
            #     This is safe because the prepare is superfluous and the
            #     teardown is going to fail (the LVs are in use by the VM)
            #  2. A VM using this image is started after the prepare.
            #     This is safe because the prepare issued by the VM is
            #     superfluous and our teardown is going to fail (the LVs are
            #     in use by the VM).
            #  3. A VM using this image is started and the teardown is
            #     executed before that the actual QEMU process is started.
            #     This is safe because the VM is going to fail (the engine
            #     should retry later) but there is no risk of corruption.
            #  4. A VM using this image is destroyed after the prepare and
            #     before reading the image size.
            #     This is safe because the upgrade process will fail (unable
            #     to read the image virtual size) and it can be restarted
            #     later.
            imgVolumes = sd.getVolsOfImage(allVolumes, imgUUID).keys()
            try:
                try:
                    domain.activateVolumes(imgUUID, imgVolumes)
                except (OSError, se.CannotActivateLogicalVolumes):
                    log.error("Image %s can't be activated.",
                              imgUUID, exc_info=True)

                for volUUID in imgVolumes:
                    try:
                        vol = domain.produceVolume(imgUUID, volUUID)
                        _v3_reset_meta_volsize(vol)  # BZ#811880
                    except cmdutils.Error:
                        log.error("It is not possible to read the volume %s "
                                  "using qemu-img, the content looks damaged",
                                  volUUID, exc_info=True)

            except se.VolumeDoesNotExist:
                log.error("It is not possible to prepare the image %s, the "
                          "volume chain looks damaged", imgUUID,
                          exc_info=True)

            except se.MetaDataKeyNotFoundError:
                log.error("It is not possible to prepare the image %s, the "
                          "volume metadata looks damaged", imgUUID,
                          exc_info=True)

            finally:
                try:
                    domain.deactivateImage(imgUUID)
                except se.CannotDeactivateLogicalVolume:
                    log.warning("Unable to teardown the image %s, this error "
                                "is not critical since the volume might be in"
                                " use", imgUUID, exc_info=True)

        log.debug("Finalizing the storage domain upgrade from version %s to "
                  "version %s for domain %s", currentVersion, targetVersion,
                  domain.sdUUID)
        domain.setMetaParam(sd.DMDK_VERSION, targetVersion)

    except:
        if isMsd:
            try:
                log.error("Releasing the cluster lock for domain %s with "
                          "host id: %s", domain.sdUUID, hostId)
                newClusterLock.release(domain.getClusterLease())
            except:
                log.error("Unable to release the cluster lock for domain "
                          "%s with host id: %s", domain.sdUUID, hostId,
                          exc_info=True)

        try:
            log.error("Releasing the host id %s for domain %s", hostId,
                      domain.sdUUID)
            newClusterLock.releaseHostId(hostId, wait=True, unused=True)
        except:
            log.error("Unable to release the host id %s for domain %s",
                      hostId, domain.sdUUID, exc_info=True)

        raise

    # Releasing the old cluster lock (safelease). This lock was acquired
    # by the regular startSpm flow and now must be replaced by the new one
    # (sanlock). Since we are already at the end of the process (no way to
    # safely rollback to version 0 or 2) we should ignore the cluster lock
    # release errors.
    if isMsd:
        try:
            domain._clusterLock.release(domain.getClusterLease())
        except:
            log.error("Unable to release the old cluster lock for domain "
                      "%s ", domain.sdUUID, exc_info=True)

    # This is not strictly required since the domain object is destroyed right
    # after the upgrade but let's not make assumptions about future behaviors
    log.debug("Switching the cluster lock for domain %s", domain.sdUUID)
    domain._clusterLock = newClusterLock


def v4DomainConverter(repoPath, hostId, domain, isMsd):
    targetVersion = 4

    log.debug("Starting conversion of domain %s to version %s",
              domain.sdUUID, targetVersion)

    if domain.supports_external_leases(targetVersion):
        # Try to create and format the new external leases volume. If this
        # fail, the conversion will fail and the domain will remain in version
        # 3.  If the volume exists (leftover from previous upgrade), we reuse
        # it.
        domain.create_external_leases()

        # We have either a new or existing volume. Always format it to make
        # sure it is properly formatted - formatting is cheap.
        xleases_path = domain.external_leases_path()

        # V4 domain always uses 1m alignment and 512 bytes block size.
        domain.format_external_leases(
            domain.sdUUID,
            xleases_path,
            alignment=sc.ALIGNMENT_1M,
            block_size=sc.BLOCK_SIZE_512)

    # We may have now a good external leases volume, try to change the domain
    # version to 4. If this fail, conversion will fail, and the domain will
    # remain in version 3 with unused external leases volume.  Converting the
    # domain again will resuse the external leases volume.
    domain.setMetaParam(sd.DMDK_VERSION, targetVersion)

    log.debug("Conversion of domain %s to version = %s has been completed.",
              domain.sdUUID, targetVersion)


def v5DomainConverter(repoPath, hostId, domain, isMsd):
    target_version = 5

    if domain.getVersion() == 3:
        v4DomainConverter(repoPath, hostId, domain, isMsd)

    log.debug("Starting conversion of domain %s to version %s",
              domain.sdUUID, target_version)

    # 1. Add v5 keys to volume metadata. If this fail or interrupted, we still
    # have valid v4 metadata.
    domain.convert_volumes_metadata(target_version)

    # 2. All volumes were converted, we can switch the domain to v5 now.
    # If this fails or interrupted, the conversion will fail and the domain
    # will remain a valid v4 domain.
    domain.convert_metadata(target_version)

    # 3. Remove legacy volume metadata keys. If this fails or interrupted we
    # can safely log and continue; the old keys are ignored when reading
    # metadata, and will be eventually removed in the next metadata update.
    try:
        domain.finalize_volumes_metadata(target_version)
    except Exception:
        log.exception("Error finalizing volume metadata")

    log.debug("Conversion of domain %s to version %s has been completed",
              domain.sdUUID, target_version)


_IMAGE_REPOSITORY_CONVERSION_TABLE = {
    ('0', '3'): v3DomainConverter,
    ('2', '3'): v3DomainConverter,
    ('3', '4'): v4DomainConverter,
    ('3', '5'): v5DomainConverter,
    ('4', '5'): v5DomainConverter,
}


class FormatConverter(object):
    def __init__(self, conversionTable):
        self._convTable = conversionTable

    def _getConverter(self, sourceFormat, targetFormat):
        return self._convTable[(sourceFormat, targetFormat)]

    def convert(self, repoPath, hostId, imageRepo, isMsd, targetFormat):
        sourceFormat = imageRepo.getFormat()
        if sourceFormat == targetFormat:
            return

        converter = self._getConverter(sourceFormat, targetFormat)
        converter(repoPath, hostId, imageRepo, isMsd)


def DefaultFormatConverter():
    return FormatConverter(_IMAGE_REPOSITORY_CONVERSION_TABLE)
