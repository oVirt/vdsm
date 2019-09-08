#
# Copyright 2012-2017 Red Hat, Inc.
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
from __future__ import division

from contextlib import contextmanager
import os
import uuid

import pytest

from storage.storagetestlib import (
    fake_env,
    make_qemu_chain,
)

from testlib import make_uuid
from vdsm.constants import GIB
from vdsm.constants import MEGAB
from vdsm.storage import constants as sc
from vdsm.storage import fileVolume
from vdsm.storage import qemuimg

from . marks import xfail_python3


class TestGetDomUuidFromVolumePath(object):
    def test(self):
        testPath = os.path.join(sc.REPO_DATA_CENTER,
                                "spUUID/sdUUID/images/imgUUID/volUUID")
        assert fileVolume.getDomUuidFromVolumePath(testPath) == "sdUUID"


class TestFileVolumeManifest(object):

    @contextmanager
    def make_volume(self, size, storage_type='file', format=sc.RAW_FORMAT):
        img_id = make_uuid()
        vol_id = make_uuid()
        with fake_env(storage_type) as env:
            env.make_volume(size, img_id, vol_id, vol_format=format)
            vol = env.sd_manifest.produceVolume(img_id, vol_id)
            yield vol

    def test_max_size_raw(self):
        max_size = fileVolume.FileVolume.max_size(1 * GIB, sc.RAW_FORMAT)
        # verify that max size equals to virtual size.
        assert max_size == 1 * GIB

    def test_max_size_cow(self):
        max_size = fileVolume.FileVolume.max_size(10 * GIB, sc.COW_FORMAT)
        # verify that max size equals to virtual size with estimated cow
        # overhead, aligned to MiB.
        assert max_size == 11811160064

    def test_optimal_size_raw(self):
        size = 5 * MEGAB
        with self.make_volume(size=size) as vol:
            assert vol.optimal_size() == size

    def test_optimal_size_cow(self):
        size = 5 * MEGAB
        with self.make_volume(size=size, format=sc.COW_FORMAT) as vol:
            assert vol.optimal_size() == vol.getVolumeSize()

    def test_get_image_volumes(self):
        img_id = make_uuid()
        vol_id = make_uuid()
        remote_path = "[2001:db8:85a3::8a2e:370:7334]:1234:/path"
        size = 5 * MEGAB

        # Simulate a domain with an ipv6 address
        with fake_env(storage_type='file', remote_path=remote_path) as env:
            env.make_volume(size, img_id, vol_id)
            vol = env.sd_manifest.produceVolume(img_id, vol_id)
            vol_path = vol.getVolumePath()
            sduuid = fileVolume.getDomUuidFromVolumePath(vol_path)

            assert vol.getImageVolumes(sduuid, img_id) == [vol_id]

    @xfail_python3
    def test_get_children(self):
        remote_path = "[2001:db8:85a3::8a2e:370:7334]:1234:/path"
        size = 5 * MEGAB

        # Simulate a domain with an ipv6 address
        with fake_env(storage_type='file', remote_path=remote_path) as env:
            env.chain = make_qemu_chain(env, size, sc.name2type('raw'), 2)
            base_vol = env.chain[0]
            assert (env.chain[1].volUUID,) == base_vol.getChildren()

    @pytest.mark.parametrize("capacity, virtual_size, expected_capacity", [
        # capacity, virtual_size, expected_capacity
        (0, 128 * MEGAB, 128 * MEGAB),  # failed resize, repair capacity
        (128 * MEGAB, 256 * MEGAB, 256 * MEGAB),  # invalid size, repair cap
        (128 * MEGAB, 128 * MEGAB, 128 * MEGAB),  # normal case, no change
        (256 * MEGAB, 128 * MEGAB, 256 * MEGAB),  # cap > actual, no change
    ])
    def test_repair_capacity(self, capacity, virtual_size, expected_capacity):
        with self.make_volume(virtual_size, format=sc.COW_FORMAT) as vol:
            md = vol.getMetadata()
            md.capacity = capacity
            vol.setMetadata(md)
            assert md.capacity == capacity

            vol.updateInvalidatedSize()
            assert vol.getMetadata().capacity == expected_capacity

    @xfail_python3
    def test_new_volume_lease(self, fake_sanlock):
        size = 5 * MEGAB
        with self.make_volume(size=size, format=sc.COW_FORMAT) as vol:
            md_id = vol.getMetadataId()
            sd_uuid = vol.sdUUID
            vol_uuid = vol.volUUID

            # We just create volume in the test, but to get volume info
            # sanlock lockspace has to be initialized (as it calls
            # read_resource_owners for a lease), so initialize lockspace
            # manually here.
            fake_sanlock.write_lockspace(sd_uuid, "test_path")
            vol.newVolumeLease(md_id, sd_uuid, vol_uuid)
            info = vol.getInfo()

            expected = {
                "offset": 0,
                "owners": [],
                "path": "%s.lease" % vol.getVolumePath(),
                "version": None,
            }
            assert expected == info["lease"]


@pytest.mark.parametrize("domain_version", [3, 4, 5])
def test_volume_size_unaligned(monkeypatch, tmpdir, tmp_repo, fake_access,
                               fake_rescan, tmp_db, fake_task, domain_version):
    dom = tmp_repo.create_localfs_domain(name="domain", version=domain_version)

    img_uuid = str(uuid.uuid4())
    vol_uuid = str(uuid.uuid4())

    # Creating with unaligned size should align to 4k.
    unaligned_vol_capacity = 10 * 1024**3 + sc.BLOCK_SIZE_512
    expected_vol_capacity = 10 * 1024**3 + sc.BLOCK_SIZE_4K

    dom.createVolume(
        imgUUID=img_uuid,
        capacity=unaligned_vol_capacity,
        volFormat=sc.RAW_FORMAT,
        preallocate=sc.SPARSE_VOL,
        diskType=sc.DATA_DISKTYPE,
        volUUID=vol_uuid,
        desc="Test volume",
        srcImgUUID=sc.BLANK_UUID,
        srcVolUUID=sc.BLANK_UUID)
    vol = dom.produceVolume(img_uuid, vol_uuid)
    vol_path = vol.getVolumePath()
    qcow2_info = qemuimg.info(vol_path)

    assert qcow2_info["virtualsize"] == expected_vol_capacity
    assert vol.getCapacity() == expected_vol_capacity
