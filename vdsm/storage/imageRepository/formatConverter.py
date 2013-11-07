#
# Copyright 2012 Red Hat, Inc.
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

import logging

from vdsm import constants
from vdsm import qemuImg

from storage import sd
from storage import blockSD
from storage import volume
from storage import blockVolume
from storage import storage_exception as se


def __convertDomainMetadataToTags(domain, targetVersion):
    log = logging.getLogger('Storage.DomainMetadataToTags')

    newMetadata = blockSD.TagBasedSDMetadata(domain.sdUUID)
    oldMetadata = domain._metadata

    # We use _dict to bypass the validators in order to copy all metadata
    metadata = oldMetadata._dict.copy()
    metadata[sd.DMDK_VERSION] = str(targetVersion)  # Must be a string

    log.debug("Converting domain %s to tag based metadata", domain.sdUUID)
    newMetadata._dict.update(metadata)

    try:
        # If we can't clear the old metadata we don't have any clue on what
        # actually happened. We prepare the convertError exception to raise
        # later on if we discover that the upgrade didn't take place.
        oldMetadata._dict.clear()
    except Exception as convertError:
        log.error("Could not clear the old metadata", exc_info=True)
    else:
        # We don't have any valuable information to add here
        convertError = RuntimeError("Unknown metadata conversion error")

    # If this fails, there's nothing we can do, let's bubble the exception
    chkMetadata = blockSD.selectMetadata(domain.sdUUID)

    if chkMetadata[sd.DMDK_VERSION] == int(targetVersion):
        # Switching to the newMetadata (successful upgrade), the oldMetadata
        # was cleared after all.
        domain._metadata = chkMetadata
        log.debug("Conversion of domain %s to tag based metadata completed, "
                  "target version = %s", domain.sdUUID, targetVersion)
    else:
        # The upgrade failed, cleaning up the new metadata
        log.error("Could not convert domain %s to tag based metadata, "
                  "target version = %s", domain.sdUUID, targetVersion)
        newMetadata._dict.clear()
        # Raising the oldMetadata_dict.clear() exception or the default one
        raise convertError


def v2DomainConverter(repoPath, hostId, domain, isMsd):
    log = logging.getLogger('Storage.v2DomainConverter')
    targetVersion = 2

    if domain.getStorageType() in sd.BLOCK_DOMAIN_TYPES:
        log.debug("Trying to upgrade domain %s to tag based metadata "
                  "version %s", domain.sdUUID, targetVersion)

        __convertDomainMetadataToTags(domain, targetVersion)

    else:
        log.debug("Skipping the upgrade to tag based metadata version %s "
                  "for the domain %s", targetVersion, domain.sdUUID)


def v3DomainConverter(repoPath, hostId, domain, isMsd):
    log = logging.getLogger('Storage.v3DomainConverter')

    targetVersion = 3
    currentVersion = domain.getVersion()

    log.debug("Starting conversion for domain %s from version %s "
              "to version %s", domain.sdUUID, currentVersion, targetVersion)

    targetVersion = 3
    currentVersion = domain.getVersion()

    # For block domains if we're upgrading from version 0 we need to first
    # upgrade to version 2 and then proceed to upgrade to version 3.
    if domain.getStorageType() in sd.BLOCK_DOMAIN_TYPES:
        if currentVersion == 0:
            log.debug("Upgrading domain %s from version %s to version 2",
                      domain.sdUUID, currentVersion)
            v2DomainConverter(repoPath, hostId, domain, isMsd)
            currentVersion = domain.getVersion()

        if currentVersion != 2:
            log.debug("Unsupported conversion from version %s to version %s",
                      currentVersion, targetVersion)
            raise se.UnsupportedDomainVersion(currentVersion)

    if domain.getStorageType() in sd.FILE_DOMAIN_TYPES:
        log.debug("Setting permissions for domain %s", domain.sdUUID)
        domain.setMetadataPermissions()

    log.debug("Initializing the new cluster lock for domain %s", domain.sdUUID)
    newClusterLock = domain._makeClusterLock(targetVersion)
    newClusterLock.initLock()

    log.debug("Acquiring the host id %s for domain %s", hostId, domain.sdUUID)
    newClusterLock.acquireHostId(hostId, async=False)

    V2META_SECTORSIZE = 512

    def v3ResetMetaVolSize(vol):
        # BZ811880 Verifiying that the volume size is the same size advertised
        # by the metadata
        log.debug("Checking the volume size for the volume %s", vol.volUUID)

        metaVolSize = int(vol.getMetaParam(volume.SIZE))

        if vol.getFormat() == volume.COW_FORMAT:
            qemuVolInfo = qemuImg.info(vol.getVolumePath(),
                                       qemuImg.FORMAT.QCOW2)
            virtVolSize = qemuVolInfo["virtualsize"] / V2META_SECTORSIZE
        else:
            virtVolSize = vol.getVolumeSize()

        if metaVolSize != virtVolSize:
            log.warn("Fixing the mismatch between the metadata volume size "
                     "(%s) and the volume virtual size (%s) for the volume "
                     "%s", vol.volUUID, metaVolSize, virtVolSize)
            vol.setMetaParam(volume.SIZE, str(virtVolSize))

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

        leasesSize = domain.getLeasesFileSize() / constants.MEGAB
        metaMaxSlot = leasesSize - blockVolume.RESERVED_LEASES - 1

        log.debug("Starting metadata reallocation check for domain %s with "
                  "metaMaxSlot %s (leases volume size %s)", domain.sdUUID,
                  metaMaxSlot, leasesSize)

        # Updating the volumes one by one, doesn't require activation
        for volUUID, (imgUUIDs, parentUUID) in allVolumes.iteritems():
            # The first imgUUID is the imgUUID of the template or the only
            # imgUUID where the volUUID appears.
            vol = domain.produceVolume(imgUUIDs[0], volUUID)
            metaOffset = vol.getMetaOffset()

            if metaOffset < metaMaxSlot:
                continue

            log.debug("Reallocating metadata slot %s for volume %s",
                      metaOffset, vol.volUUID)
            metaContent = vol.getMetadata()

            with vol._tagCreateLock:
                newMetaOffset = domain.mapMetaOffset(
                    vol.volUUID, blockVolume.VOLUME_MDNUMBLKS)
                if newMetaOffset > metaMaxSlot:
                    raise se.NoSpaceLeftOnDomain(domain.sdUUID)

                log.debug("Copying metadata for volume %s to the new slot %s",
                          vol.volUUID, newMetaOffset)
                vol.createMetadata((domain.sdUUID, newMetaOffset), metaContent)

                log.debug("Switching the metadata slot for volume %s to %s",
                          vol.volUUID, newMetaOffset)
                vol.changeVolumeTag(blockVolume.TAG_PREFIX_MD,
                                    str(newMetaOffset))

    try:
        if isMsd:
            log.debug("Acquiring the cluster lock for domain %s with "
                      "host id: %s", domain.sdUUID, hostId)
            newClusterLock.acquire(hostId)

        allVolumes = domain.getAllVolumes()
        allImages = {}  # {images: parent_image}

        # Few vdsm releases (4.9 prior 496c0c3, BZ#732980) generated metadata
        # offsets higher than 1947 (LEASES_SIZE - RESERVED_LEASES - 1).
        # This function reallocates such slots to free ones in order to use the
        # same offsets for the volume resource leases.
        v3ReallocateMetadataSlot(domain, allVolumes)

        # Updating the volumes one by one, doesn't require activation
        for volUUID, (imgUUIDs, parentUUID) in allVolumes.iteritems():
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
                        v3ResetMetaVolSize(vol)  # BZ#811880
                    except qemuImg.QImgError:
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
                newClusterLock.release()
            except:
                log.error("Unable to release the cluster lock for domain "
                          "%s with host id: %s", domain.sdUUID, hostId,
                          exc_info=True)

        try:
            log.error("Releasing the host id %s for domain %s", hostId,
                      domain.sdUUID)
            newClusterLock.releaseHostId(hostId, async=False, unused=True)
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
            domain._clusterLock.release()
        except:
            log.error("Unable to release the old cluster lock for domain "
                      "%s ", domain.sdUUID, exc_info=True)

    # This is not strictly required since the domain object is destroyed right
    # after the upgrade but let's not make assumptions about future behaviors
    log.debug("Switching the cluster lock for domain %s", domain.sdUUID)
    domain._clusterLock = newClusterLock


_IMAGE_REPOSITORY_CONVERSION_TABLE = {
    ('0', '2'): v2DomainConverter,
    ('0', '3'): v3DomainConverter,
    ('2', '3'): v3DomainConverter,
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
