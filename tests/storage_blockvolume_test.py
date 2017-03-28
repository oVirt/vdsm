#
# Copyright 2015-2016 Red Hat, Inc.
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


from vdsm.config import config
from vdsm.constants import GIB
from vdsm.storage import constants as sc
from vdsm.storage import exception as se

from storage.blockVolume import BlockVolume
from testlib import permutations, expandPermutations
from testlib import VdsmTestCase as TestCaseBase


@expandPermutations
class BlockVolumeSizeTests(TestCaseBase):

    @permutations(
        # (preallocate, capacity, initial_size), result
        [[(sc.PREALLOCATED_VOL, 2048, None), 1],
         [(sc.PREALLOCATED_VOL, 2049, None), 2],
         [(sc.PREALLOCATED_VOL, 2097152, None), 1024],
         [(sc.SPARSE_VOL, 9999, None),
          config.getint("irs", "volume_utilization_chunk_mb")],
         [(sc.SPARSE_VOL, 8388608, 1860), 1],
         [(sc.SPARSE_VOL, 8388608, 1870), 2],
         ])
    def test_block_volume_size(self, args, result):
        size = BlockVolume.calculate_volume_alloc_size(*args)
        self.assertEqual(size, result)

    @permutations(
        # preallocate
        [[sc.PREALLOCATED_VOL],
         [sc.SPARSE_VOL],
         ])
    def test_fail_invalid_block_volume_size(self, preallocate):
        with self.assertRaises(se.InvalidParameterException):
            BlockVolume.calculate_volume_alloc_size(preallocate, 2048, 2049)


class TestBlockVolumeManifest(TestCaseBase):

    def test_max_size_raw(self):
        # # verify that max size equals to virtual size.
        self.assertEqual(BlockVolume.max_size(1 * GIB, sc.RAW_FORMAT),
                         1 * GIB)

    def test_max_size_cow(self):
        # verify that max size equals to virtual size with estimated cow
        # overhead, aligned to vg extent size.
        self.assertEqual(BlockVolume.max_size(10 * GIB, sc.COW_FORMAT),
                         11811160064)
