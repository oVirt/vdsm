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

    dom = localFsSD.LocalFsStorageDomain.create(
        sdUUID=str(uuid.uuid4()),
        domainName="domain",
        domClass=sd.DATA_DOMAIN,
        remotePath=remote_path,
        version=3,
        storageType=sd.LOCALFS_DOMAIN,
        block_size=sc.BLOCK_SIZE_512,
        alignment=sc.ALIGNMENT_1M)

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
