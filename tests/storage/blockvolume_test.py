#
# Copyright 2015-2017 Red Hat, Inc.
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

from vdsm import utils
from vdsm.common.units import MiB, GiB
from vdsm.storage import blockVolume
from vdsm.storage import constants as sc
from vdsm.storage import exception as se
from vdsm.storage import qemuimg
from vdsm.storage.blockVolume import BlockVolume

from monkeypatch import MonkeyPatch
from monkeypatch import MonkeyPatchScope

from storage.storagetestlib import (
    fake_env,
    make_qemu_chain,
)

from testlib import make_config
from testlib import make_uuid
from testlib import permutations, expandPermutations
from testlib import VdsmTestCase


CONFIG = make_config([
    ('irs', 'volume_utilization_chunk_mb', '1024'),
    ('irs', 'volume_utilizzation_precent', '50'),
])


@expandPermutations
class TestBlockVolumeSize(VdsmTestCase):

    @permutations([
        # (preallocate, format, capacity, initial size),
        # allocation size in bytes
        # Preallocate, raw, capacity 1 MiB, No initial size.
        [(sc.PREALLOCATED_VOL, sc.RAW_FORMAT, MiB, None), MiB],
        # Preallocate, raw, capacity 1 MiB + 1 byte, No initial size.
        [(sc.PREALLOCATED_VOL, sc.RAW_FORMAT, MiB + 1, None), MiB + 1],
        # Preallocate, raw, capacity 1 GiB, No initial size.
        [(sc.PREALLOCATED_VOL, sc.RAW_FORMAT, GiB, None), GiB],
        # Preallocate, cow, capacity 2 GiB, initial size GiB.
        # Expected GiB allocated
        [(sc.PREALLOCATED_VOL, sc.COW_FORMAT, 2 * GiB, GiB), GiB],
        # Preallocate, cow, capacity 2 GiB, No initial size.
        # Expected GiB allocated
        [(sc.PREALLOCATED_VOL, sc.COW_FORMAT, GiB, None), GiB],
        # Sparse, cow, capacity config.volume_utilization_chunk_mb - 1,
        # No initial size.
        # Expected 1024 MiB allocated (config.volume_utilization_chunk_mb)
        [(sc.SPARSE_VOL, sc.COW_FORMAT, (CONFIG.getint(
            "irs", "volume_utilization_chunk_mb") - 1) * MiB, None), GiB],
        # Sparse, cow, capacity 4 GiB, initial size 952320 B.
        [(sc.SPARSE_VOL, sc.COW_FORMAT, 4 * GiB, 952320),
         int(952320 * blockVolume.QCOW_OVERHEAD_FACTOR)],
        # Sparse, cow, capacity 4 GiB, initial size 1870.
        [(sc.SPARSE_VOL, sc.COW_FORMAT, 4 * GiB, 957440),
         int(957440 * blockVolume.QCOW_OVERHEAD_FACTOR)],
        # Sparse, cow, capacity 1 GiB, initial size 2359296.
        [(sc.SPARSE_VOL, sc.COW_FORMAT, GiB,
          BlockVolume.max_size(GiB, sc.COW_FORMAT)),
         int(BlockVolume.max_size(GiB, sc.COW_FORMAT) *
             blockVolume.QCOW_OVERHEAD_FACTOR)],
    ])
    @MonkeyPatch(blockVolume, 'config', CONFIG)
    def test_block_volume_size(self, args, result):
        size = BlockVolume.calculate_volume_alloc_size(*args)
        self.assertEqual(size, result)

    @permutations([
        [sc.PREALLOCATED_VOL, sc.RAW_FORMAT],
        [sc.PREALLOCATED_VOL, sc.COW_FORMAT],
        [sc.SPARSE_VOL, sc.COW_FORMAT],
    ])
    def test_fail_invalid_block_volume_size(self, preallocate, vol_format):
        with self.assertRaises(se.InvalidParameterException):
            max_size = BlockVolume.max_size(GiB, vol_format)
            BlockVolume.calculate_volume_alloc_size(preallocate,
                                                    vol_format,
                                                    GiB,
                                                    max_size + 1)


@expandPermutations
class TestBlockVolumeManifest(VdsmTestCase):

    @contextmanager
    def make_volume(self, size, format=sc.RAW_FORMAT, prealloc=sc.SPARSE_VOL):
        img_id = make_uuid()
        vol_id = make_uuid()
        # TODO fix make_volume helper to create the qcow image when needed
        with fake_env(storage_type='block') as env:
            if format == sc.RAW_FORMAT:
                env.make_volume(
                    size, img_id, vol_id, vol_format=format, prealloc=prealloc)
                vol = env.sd_manifest.produceVolume(img_id, vol_id)
                yield vol
            else:
                chain = make_qemu_chain(
                    env, size, format, chain_len=1, prealloc=prealloc)
                yield chain[0]

    def test_max_size_raw(self):
        # verify that max size equals to virtual size.
        self.assertEqual(BlockVolume.max_size(1 * GiB, sc.RAW_FORMAT), 1 * GiB)

    def test_max_size_cow(self):
        # verify that max size equals to virtual size with estimated cow
        # overhead, aligned to vg extent size.
        self.assertEqual(BlockVolume.max_size(10 * GiB, sc.COW_FORMAT),
                         11811160064)

    @MonkeyPatch(blockVolume, 'config', CONFIG)
    def test_optimal_size_raw(self):
        # verify optimal size equals to virtual size.
        with self.make_volume(size=GiB) as vol:
            self.assertEqual(vol.optimal_size(), GiB)

    @MonkeyPatch(blockVolume, 'config', CONFIG)
    def test_optimal_size_cow_leaf(self):
        # Optimal size calculated using actual size and chunk size.
        with self.make_volume(size=2 * GiB, format=sc.COW_FORMAT) as vol:
            chunk_size = GiB
            check = qemuimg.check(vol.getVolumePath(), qemuimg.FORMAT.QCOW2)
            optimal_size = utils.round(
                check['offset'] + chunk_size, vol.align_size)
            self.assertEqual(vol.optimal_size(), optimal_size)
            self.assertEqual(
                vol.optimal_cow_size(check["offset"], 2 * GiB, vol.isLeaf()),
                optimal_size)

    @MonkeyPatch(blockVolume, 'config', CONFIG)
    def test_optimal_size_cow_leaf_max(self):
        # Optimal size is limited to maximum size.
        size = 512 * MiB
        with self.make_volume(size=size, format=sc.COW_FORMAT) as vol:
            max_size = vol.max_size(size, vol.getFormat())
            self.assertEqual(vol.optimal_size(), max_size)
            check = qemuimg.check(vol.getVolumePath(), qemuimg.FORMAT.QCOW2)
            self.assertEqual(
                vol.optimal_cow_size(check["offset"], 512 * MiB, vol.isLeaf()),
                max_size)

    @permutations([
        # virtual_size, actual_size, optimal_size
        # Limited by max size.
        (512 * MiB, 200 * MiB, 256 * MiB),
        # Empty qcow2 image - align to extent size.
        (2 * GiB, 262144, sc.VG_EXTENT_SIZE),
        # Align to extent size.
        (2 * GiB, 1023 * MiB, 1024 * MiB),
        (2 * GiB, 1024 * MiB, 1024 * MiB),
    ])
    def test_optimal_size_cow_internal(
            self, virtual_size, actual_size, optimal_size):
        def fake_check(path, format):
            return {'offset': actual_size}

        with fake_env('block') as env:
            # In order to test edge cases, mainly of volumes with big data, we
            # fake qemuimg check to return big volumes size, instead of writing
            # big data to volumes, an operation that takes long time.
            with MonkeyPatchScope([(qemuimg, 'check', fake_check)]):
                env.chain = make_qemu_chain(
                    env, virtual_size, sc.COW_FORMAT, 3)
                vol = env.chain[1]
                self.assertEqual(vol.optimal_size(), optimal_size)
                self.assertEqual(
                    vol.optimal_cow_size(
                        actual_size, virtual_size, vol.isLeaf()),
                    optimal_size)

    @permutations([
        # capacity, virtual_size, expected_capacity
        (0, 128 * MiB, 128 * MiB),  # failed resize, repair capacity
        (128 * MiB, 256 * MiB, 256 * MiB),  # invalid size, repair cap
        (128 * MiB, 128 * MiB, 128 * MiB),  # normal case, no change
        (256 * MiB, 128 * MiB, 256 * MiB),  # cap > actual, no change
    ])
    def test_repair_capacity(self, capacity, virtual_size, expected_capacity):
        with self.make_volume(virtual_size, format=sc.COW_FORMAT) as vol:
            md = vol.getMetadata()
            md.capacity = capacity
            vol.setMetadata(md)
            assert md.capacity == capacity

            vol.updateInvalidatedSize()
            assert vol.getMetadata().capacity == expected_capacity

    @permutations([
        # format, prealloc, can_reduce
        # Raw or preallocated disks cannot be reduced.
        (sc.RAW_FORMAT, sc.PREALLOCATED_VOL, False),
        (sc.COW_FORMAT, sc.PREALLOCATED_VOL, False),
        # Cow sparse can be reduced.
        (sc.COW_FORMAT, sc.SPARSE_VOL, True),
        # Raw sparse is an invalid combination.
    ])
    def test_can_reduce(self, format, prealloc, can_reduce):
        with self.make_volume(
                size=GiB, format=format, prealloc=prealloc) as vol:
            assert vol.can_reduce() == can_reduce
