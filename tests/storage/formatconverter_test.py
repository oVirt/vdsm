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

from vdsm.storage import constants as sc
from vdsm.storage import formatconverter
from vdsm.storage import localFsSD
from vdsm.storage import sd
from vdsm.storage.formatconverter import _v3_reset_meta_volsize
from vdsm.storage.sdc import sdCache

from .storagetestlib import (
    fake_volume,
    MB
)


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
    remote_path = str(tmpdir.mkdir("domain"))
    tmp_repo.connect_localfs(remote_path)
    sd_uuid = str(uuid.uuid4())

    dom = localFsSD.LocalFsStorageDomain.create(
        sdUUID=sd_uuid,
        domainName="domain",
        domClass=sd.DATA_DOMAIN,
        remotePath=remote_path,
        version=3,
        storageType=sd.LOCALFS_DOMAIN,
        block_size=sc.BLOCK_SIZE_512,
        alignment=sc.ALIGNMENT_1M)

    sdCache.knownSDs[sd_uuid] = localFsSD.findDomain
    sdCache.manuallyAddDomain(dom)

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


def test_convert_from_v4_to_v5_localfs(tmpdir, tmp_repo, tmp_db, fake_access,
                                       fake_rescan, fake_task):
    remote_path = str(tmpdir.mkdir("domain"))
    tmp_repo.connect_localfs(remote_path)
    sd_uuid = str(uuid.uuid4())

    dom = localFsSD.LocalFsStorageDomain.create(
        sdUUID=sd_uuid,
        domainName="domain",
        domClass=sd.DATA_DOMAIN,
        remotePath=remote_path,
        version=4,
        storageType=sd.LOCALFS_DOMAIN,
        block_size=sc.BLOCK_SIZE_512,
        alignment=sc.ALIGNMENT_1M)

    sdCache.knownSDs[sd_uuid] = localFsSD.findDomain
    sdCache.manuallyAddDomain(dom)

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
    assert old_dom_md.pop("VERSION") == 4
    assert new_dom_md.pop("VERSION") == 5

    # Keys added in v5.
    assert new_dom_md.pop("BLOCK_SIZE") == sc.BLOCK_SIZE_512
    assert new_dom_md.pop("ALIGNMENT") == sc.ALIGNMENT_1M

    # Rest of the values should not be modified.
    assert new_dom_md == old_dom_md

    # Verify that volumes metadata was converted to v5 format.

    for vol in dom.iter_volumes():
        vol_md = volumes_md[vol.volUUID]
        meta_path = vol.getMetaVolumePath()
        with open(meta_path) as f:
            data = f.read()
        assert data == vol_md.storage_format(5)
