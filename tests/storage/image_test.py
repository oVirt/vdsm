#
# Copyright 2017 Red Hat, Inc.
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

from monkeypatch import MonkeyPatch
from testlib import expandPermutations, permutations
from testlib import VdsmTestCase

from vdsm.storage import constants as sc
from vdsm.storage import image

GB_IN_BLK = 1024**3 // 512


def fakeEstimateChainSize(self, sdUUID, imgUUID, volUUID, size):
    return GB_IN_BLK * 2.25


def fake_estimate_qcow2_size(self, src_vol_params, dst_sd_id):
    return GB_IN_BLK * 1.25


@expandPermutations
class TestCalculateVolAlloc(VdsmTestCase):

    @permutations([
        # srcVolParams, destVolFormt, expectedAlloc
        # copy raw to raw, using virtual size
        (dict(size=GB_IN_BLK * 2,
              volFormat=sc.RAW_FORMAT,
              apparentsize=GB_IN_BLK),
         sc.RAW_FORMAT,
         GB_IN_BLK * 2),
        # copy raw to qcow, using estimated chain size
        (dict(size=GB_IN_BLK * 2,
              volFormat=sc.RAW_FORMAT,
              apparentsize=GB_IN_BLK,
              prealloc=sc.SPARSE_VOL,
              parent="parentUUID",
              imgUUID="imgUUID",
              volUUID="volUUID"),
         sc.COW_FORMAT,
         GB_IN_BLK * 1.25),
        # copy single cow volume to raw, using virtual size
        (dict(size=GB_IN_BLK * 2,
              volFormat=sc.COW_FORMAT,
              apparentsize=GB_IN_BLK),
         sc.RAW_FORMAT,
         GB_IN_BLK * 2),
        # copy cow chain to raw, using virtual size
        (dict(size=GB_IN_BLK * 2,
              volFormat=sc.COW_FORMAT,
              apparentsize=GB_IN_BLK,
              parent="parentUUID"),
         sc.RAW_FORMAT,
         GB_IN_BLK * 2),
        # copy single cow to cow, using estimated size.
        (dict(size=GB_IN_BLK * 2,
              volFormat=sc.COW_FORMAT,
              apparentsize=GB_IN_BLK,
              parent=sc.BLANK_UUID),
         sc.COW_FORMAT,
         GB_IN_BLK * 1.25),
        # copy qcow chain to cow, using estimated chain size
        (dict(size=GB_IN_BLK * 2,
              volFormat=sc.COW_FORMAT,
              apparentsize=GB_IN_BLK,
              prealloc=sc.SPARSE_VOL,
              parent="parentUUID",
              imgUUID="imgUUID",
              volUUID="volUUID"),
         sc.COW_FORMAT,
         GB_IN_BLK * 2.25),
    ])
    @MonkeyPatch(image.Image, 'estimateChainSize', fakeEstimateChainSize)
    @MonkeyPatch(image.Image, 'estimate_qcow2_size', fake_estimate_qcow2_size)
    def test_calculate_vol_alloc(
            self, src_params, dest_format, expected_blk):
        img = image.Image("/path/to/repo")
        alloc_blk = img.calculate_vol_alloc("src_sd_id", src_params,
                                            "dst_sd_id", dest_format)
        self.assertEqual(alloc_blk, expected_blk)
