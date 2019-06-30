#
# Copyright 2018-2019 Red Hat, Inc.
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

import uuid

import pytest

from vdsm.storage import blockSD
from vdsm.storage import constants as sc
from vdsm.storage import exception as se
from vdsm.storage import formatconverter
from vdsm.storage import lvm
from vdsm.storage import misc
from vdsm.storage import sd
from vdsm.storage.formatconverter import _v3_reset_meta_volsize
from vdsm.storage.sdc import sdCache

from .storagetestlib import (
    fake_volume,
    MB
)

from . constants import CLEARED_VOLUME_METADATA
from . marks import requires_root, xfail_python3

# This metadata is missing required keys (DOMAIN, VOLTYPE, LEGALITY).
INVALID_VOLUME_METADATA = b"""\
CTIME=1542308390
FORMAT=RAW
DISKTYPE=DATA
CAP=1125899906842624
DESCRIPTION=Volume with invalid metadata
IMAGE=bc9d15fa-70eb-40aa-8a2e-e4f27664752f
PUUID=00000000-0000-0000-0000-000000000000
TYPE=PREALLOCATED
EOF
""".ljust(sc.METADATA_SIZE, b"\0")


@pytest.fixture(params=[sc.RAW_FORMAT, sc.COW_FORMAT])
def vol(request):
    with fake_volume(format=request.param, size=MB) as vol:
        yield vol


def test_v3_reset_meta_vol_size_metadata_no_change_needed(vol):
    original_size_blk = vol.getSize()
    _v3_reset_meta_volsize(vol)
    assert vol.getSize() == original_size_blk


def test_v3_reset_meta_vol_size_metadata_wrong(vol):
    original_size_blk = vol.getSize()
    vol.setSize(1024)
    _v3_reset_meta_volsize(vol)
    assert vol.getSize() == original_size_blk


def test_convert_from_v3_to_v4_localfs(tmpdir, tmp_repo, fake_access):
    dom = tmp_repo.create_localfs_domain(name="domain", version=3)

    assert dom.getVersion() == 3

    fc = formatconverter.DefaultFormatConverter()

    fc.convert(
        repoPath=tmp_repo.path,
        hostId=1,
        imageRepo=dom,
        isMsd=False,
        targetFormat='4')

    # LocalFS do not support external leases, so the only change is the
    # version.
    assert dom.getVersion() == 4


@pytest.mark.parametrize("src_version", [
    3,
    4,
])
def test_convert_to_v5_localfs(tmpdir, tmp_repo, tmp_db, fake_access,
                               fake_rescan, fake_task, src_version):
    dom = tmp_repo.create_localfs_domain(name="domain", version=src_version)

    # Create some volumes in v4 format.
    for i in range(3):
        dom.createVolume(
            desc="Awesome volume %d" % i,
            diskType="DATA",
            imgUUID=str(uuid.uuid4()),
            preallocate=sc.SPARSE_VOL,
            size=10 * 1024**3,
            srcImgUUID=sc.BLANK_UUID,
            srcVolUUID=sc.BLANK_UUID,
            volFormat=sc.COW_FORMAT,
            volUUID=str(uuid.uuid4()))

    # Record domain and volumes metadata before conversion.
    old_dom_md = dom.getMetadata()
    volumes_md = {vol.volUUID: vol.getMetadata() for vol in dom.iter_volumes()}

    # Simulate a partly-deleted volume with cleared metada. Such volumes could
    # be created by vdsm < 4.20.34-1.

    img_id = str(uuid.uuid4())
    vol_id = str(uuid.uuid4())

    dom.createVolume(
        desc="Half deleted volume",
        diskType="DATA",
        imgUUID=img_id,
        preallocate=sc.SPARSE_VOL,
        size=10 * 1024**3,
        srcImgUUID=sc.BLANK_UUID,
        srcVolUUID=sc.BLANK_UUID,
        volFormat=sc.COW_FORMAT,
        volUUID=vol_id)

    partly_deleted_vol = dom.produceVolume(img_id, vol_id)
    meta_path = partly_deleted_vol.getMetaVolumePath()
    with open(meta_path, "wb") as f:
        f.write(CLEARED_VOLUME_METADATA)

    # Simulate a volume with invalid metada to make sure such volume will not
    # break conversion.

    img_id = str(uuid.uuid4())
    vol_id = str(uuid.uuid4())

    dom.createVolume(
        desc="Volume with invalid metadata",
        diskType="DATA",
        imgUUID=img_id,
        preallocate=sc.SPARSE_VOL,
        size=10 * 1024**3,
        srcImgUUID=sc.BLANK_UUID,
        srcVolUUID=sc.BLANK_UUID,
        volFormat=sc.COW_FORMAT,
        volUUID=vol_id)

    invalid_md_vol = dom.produceVolume(img_id, vol_id)
    meta_path = invalid_md_vol.getMetaVolumePath()
    with open(meta_path, "wb") as f:
        f.write(INVALID_VOLUME_METADATA)

    # These volumes will not be converted to V5 format.
    skip_volumes = {partly_deleted_vol.volUUID, invalid_md_vol.volUUID}

    # Convert the domain.

    fc = formatconverter.DefaultFormatConverter()

    fc.convert(
        repoPath=tmp_repo.path,
        hostId=1,
        imageRepo=dom,
        isMsd=False,
        targetFormat='5')

    # Verify changes in domain metadata.

    new_dom_md = dom.getMetadata()

    # Values modified in v5.
    assert old_dom_md.pop("VERSION") == src_version
    assert new_dom_md.pop("VERSION") == 5

    # Keys added in v5.
    assert new_dom_md.pop("BLOCK_SIZE") == sc.BLOCK_SIZE_512
    assert new_dom_md.pop("ALIGNMENT") == sc.ALIGNMENT_1M

    # Rest of the values should not be modified.
    assert new_dom_md == old_dom_md

    # Verify that volumes metadata was converted to v5 format.

    for vol in dom.iter_volumes():
        if vol.volUUID in skip_volumes:
            continue
        vol_md = volumes_md[vol.volUUID]
        meta_path = vol.getMetaVolumePath()
        with open(meta_path) as f:
            data = f.read()
        assert data == vol_md.storage_format(5)

    # Verify that invalid metadata was left without change.

    meta_path = partly_deleted_vol.getMetaVolumePath()
    with open(meta_path, "rb") as f:
        assert f.read() == CLEARED_VOLUME_METADATA

    meta_path = invalid_md_vol.getMetaVolumePath()
    with open(meta_path, "rb") as f:
        assert f.read() == INVALID_VOLUME_METADATA


@requires_root
@xfail_python3
@pytest.mark.root
@pytest.mark.parametrize("src_version", [
    3,
    4,
])
def test_convert_to_v5_block(tmpdir, tmp_repo, tmp_storage, tmp_db,
                             fake_rescan, fake_task, fake_sanlock,
                             src_version):
    sd_uuid = str(uuid.uuid4())

    dev = tmp_storage.create_device(20 * 1024 ** 3)
    lvm.createVG(sd_uuid, [dev], blockSD.STORAGE_UNREADY_DOMAIN_TAG, 128)
    vg = lvm.getVG(sd_uuid)

    dom = blockSD.BlockStorageDomain.create(
        sdUUID=sd_uuid,
        domainName="domain",
        domClass=sd.DATA_DOMAIN,
        vgUUID=vg.uuid,
        version=src_version,
        storageType=sd.ISCSI_DOMAIN,
        block_size=sc.BLOCK_SIZE_512,
        alignment=sc.ALIGNMENT_1M)

    sdCache.knownSDs[sd_uuid] = blockSD.findDomain
    sdCache.manuallyAddDomain(dom)

    # Create domain directory structure.
    dom.refresh()

    # Only attached domains are converted.
    dom.attach(tmp_repo.pool_id)

    # Create some volumes in v4 format.
    for i in range(3):
        dom.createVolume(
            desc="Awesome volume %d" % i,
            diskType="DATA",
            imgUUID=str(uuid.uuid4()),
            preallocate=sc.SPARSE_VOL,
            size=10 * 1024**3,
            srcImgUUID=sc.BLANK_UUID,
            srcVolUUID=sc.BLANK_UUID,
            volFormat=sc.COW_FORMAT,
            volUUID=str(uuid.uuid4()))

    # Record domain and volumes metadata before conversion.
    old_dom_md = dom.getMetadata()
    volumes_md = {vol.volUUID: vol.getMetadata() for vol in dom.iter_volumes()}

    # Simulate a partly-deleted volume with cleared metada. Such volumes could
    # be created by vdsm < 4.20.34-1.

    img_id = str(uuid.uuid4())
    vol_id = str(uuid.uuid4())

    dom.createVolume(
        desc="Half deleted volume",
        diskType="DATA",
        imgUUID=img_id,
        preallocate=sc.SPARSE_VOL,
        size=10 * 1024**3,
        srcImgUUID=sc.BLANK_UUID,
        srcVolUUID=sc.BLANK_UUID,
        volFormat=sc.COW_FORMAT,
        volUUID=vol_id)

    partly_deleted_vol = dom.produceVolume(img_id, vol_id)
    slot = partly_deleted_vol.getMetadataId()[1]
    dom.manifest.write_metadata_block(slot, CLEARED_VOLUME_METADATA)

    # Simulate a volume with invalid metada to make sure such volume will not
    # break conversion.

    img_id = str(uuid.uuid4())
    vol_id = str(uuid.uuid4())

    dom.createVolume(
        desc="Volume with invalid metadata",
        diskType="DATA",
        imgUUID=img_id,
        preallocate=sc.SPARSE_VOL,
        size=10 * 1024**3,
        srcImgUUID=sc.BLANK_UUID,
        srcVolUUID=sc.BLANK_UUID,
        volFormat=sc.COW_FORMAT,
        volUUID=vol_id)

    invalid_md_vol = dom.produceVolume(img_id, vol_id)
    slot = invalid_md_vol.getMetadataId()[1]
    dom.manifest.write_metadata_block(slot, INVALID_VOLUME_METADATA)

    # These volumes will not be converted to V5 format.
    skip_volumes = {partly_deleted_vol.volUUID, invalid_md_vol.volUUID}

    # Convert the domain.

    fc = formatconverter.DefaultFormatConverter()

    fc.convert(
        repoPath=tmp_repo.path,
        hostId=1,
        imageRepo=dom,
        isMsd=False,
        targetFormat='5')

    # Verify changes in domain metadata.

    new_dom_md = dom.getMetadata()

    # Keys modified in v5.
    assert old_dom_md.pop("VERSION") == src_version
    assert new_dom_md.pop("VERSION") == 5

    # Keys added in V5.
    assert new_dom_md.pop("BLOCK_SIZE") == sc.BLOCK_SIZE_512
    assert new_dom_md.pop("ALIGNMENT") == sc.ALIGNMENT_1M

    # Kyes removed in v5.
    assert old_dom_md.pop("LOGBLKSIZE") == sc.BLOCK_SIZE_512
    assert old_dom_md.pop("PHYBLKSIZE") == sc.BLOCK_SIZE_512

    # Rest of the keys must not be modifed by conversion.
    assert old_dom_md == new_dom_md

    # Verify that xleases volume is created when upgrading from version < 4.
    xleases_vol = lvm.getLV(sd_uuid, "xleases")
    assert int(xleases_vol.size) == 1024 * dom.alignment

    with pytest.raises(se.NoSuchLease):
        dom.manifest.lease_info("no-such-lease")

    # Verify that volumes metadta was converted to v5 format.

    for vol in dom.iter_volumes():
        if vol.volUUID in skip_volumes:
            continue
        vol_md = volumes_md[vol.volUUID]
        _, slot = vol.getMetadataId()
        data = dom.manifest.read_metadata_block(slot)
        data = data.rstrip("\0")
        assert data == vol_md.storage_format(5)

    # Verify that invalid metadata was copied to v5 area.

    slot = partly_deleted_vol.getMetadataId()[1]
    assert dom.manifest.read_metadata_block(slot) == CLEARED_VOLUME_METADATA

    slot = invalid_md_vol.getMetadataId()[1]
    assert dom.manifest.read_metadata_block(slot) == INVALID_VOLUME_METADATA

    # Check that v4 metadata area is zeroed.

    meta_path = dom.manifest.metadata_volume_path()
    offset = blockSD.METADATA_BASE_V4
    size = blockSD.METADATA_BASE_V5 - blockSD.METADATA_BASE_V4
    data = misc.readblock(meta_path, offset, size)
    assert data == "\0" * size
