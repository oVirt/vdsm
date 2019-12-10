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

from __future__ import absolute_import
from __future__ import division

from monkeypatch import MonkeyPatch
import pytest

from storage.storagefakelib import FakeBlockSD
from storage.storagefakelib import FakeFileSD
from storage.storagefakelib import FakeStorageDomainCache

from testlib import expandPermutations, permutations
from testlib import make_config
from testlib import VdsmTestCase

from vdsm.common.units import GiB
from vdsm.storage import constants as sc
from vdsm.storage import image
from vdsm.storage import qemuimg

CONFIG = make_config([('irs', 'volume_utilization_chunk_mb', '1024')])


def fake_estimate_chain_size(self, sdUUID, imgUUID, volUUID, size):
    return 2.25 * GiB


def fake_estimate_qcow2_size(self, src_vol_params, dst_sd_id):
    return 1.25 * GiB


@expandPermutations
class TestCalculateVolAlloc(VdsmTestCase):

    @permutations([
        # srcVolParams, destVolFormt, expectedAlloc
        # copy raw to raw, using virtual size
        (dict(capacity=2 * GiB,
              volFormat=sc.RAW_FORMAT,
              apparentsize=GiB),
         sc.RAW_FORMAT,
         2 * GiB),
        # copy raw to qcow, using estimated chain size
        (dict(capacity=2 * GiB,
              volFormat=sc.RAW_FORMAT,
              apparentsize=GiB,
              prealloc=sc.SPARSE_VOL,
              parent="parentUUID",
              imgUUID="imgUUID",
              volUUID="volUUID"),
         sc.COW_FORMAT,
         1.25 * GiB),
        # copy single cow volume to raw, using virtual size
        (dict(capacity=2 * GiB,
              volFormat=sc.COW_FORMAT,
              apparentsize=GiB),
         sc.RAW_FORMAT,
         2 * GiB),
        # copy cow chain to raw, using virtual size
        (dict(capacity=2 * GiB,
              volFormat=sc.COW_FORMAT,
              apparentsize=GiB,
              parent="parentUUID"),
         sc.RAW_FORMAT,
         2 * GiB),
        # copy single cow to cow, using estimated size.
        (dict(capacity=2 * GiB,
              volFormat=sc.COW_FORMAT,
              apparentsize=GiB,
              parent=sc.BLANK_UUID),
         sc.COW_FORMAT,
         1.25 * GiB),
        # copy qcow chain to cow, using estimated chain size
        (dict(capacity=2 * GiB,
              volFormat=sc.COW_FORMAT,
              apparentsize=GiB,
              prealloc=sc.SPARSE_VOL,
              parent="parentUUID",
              imgUUID="imgUUID",
              volUUID="volUUID"),
         sc.COW_FORMAT,
         2.25 * GiB),
    ])
    @MonkeyPatch(image.Image, 'estimateChainSize', fake_estimate_chain_size)
    @MonkeyPatch(
        image.Image, 'estimate_qcow2_size', fake_estimate_qcow2_size)
    def test_calculate_vol_alloc(
            self, src_params, dest_format, expected):
        img = image.Image("/path/to/repo")
        allocation = img.calculate_vol_alloc("src_sd_id", src_params,
                                             "dst_sd_id", dest_format)
        self.assertEqual(allocation, expected)


class TestEstimateQcow2Size:

    @pytest.mark.parametrize('sd_class', [FakeFileSD, FakeBlockSD])
    def test_raw_to_qcow2_estimated_size(
            self, monkeypatch, sd_class):
        monkeypatch.setattr(image, "config", CONFIG)
        monkeypatch.setattr(
            qemuimg,
            'measure',
            # the estimated size for converting 1 gb
            # raw empty volume to qcow2 format
            # cmd:
            #   qemu-img measure -f raw -O qcow2 test.raw
            # output:
            #   required size: 393216
            #   fully allocated size: 1074135040
            lambda **args: {"required": 393216})
        monkeypatch.setattr(image, 'sdCache', FakeStorageDomainCache())

        image.sdCache.domains['sdUUID'] = sd_class("fake manifest")
        img = image.Image("/path/to/repo")

        vol_params = dict(
            capacity=GiB,
            volFormat=sc.RAW_FORMAT,
            path='path')
        estimated_size = img.estimate_qcow2_size(vol_params, "sdUUID")

        assert estimated_size == 1074135040

    @pytest.mark.parametrize('sd_class', [FakeFileSD, FakeBlockSD])
    def test_qcow2_to_qcow2_estimated_size(
            self, monkeypatch, sd_class):
        monkeypatch.setattr(image, "config", CONFIG)
        monkeypatch.setattr(
            qemuimg,
            'measure',
            # the estimated size for converting 1 gb
            # qcow2 empty volume to qcow2 format
            # cmd:
            #   qemu-img measure -f qcow2 -O qcow2 test.qcow2
            # output:
            #   required size: 393216
            #   fully allocated size: 1074135040
            lambda **args: {"required": 393216})
        monkeypatch.setattr(image, 'sdCache', FakeStorageDomainCache())

        image.sdCache.domains['sdUUID'] = sd_class("fake manifest")
        img = image.Image("/path/to/repo")

        vol_params = dict(
            capacity=GiB,
            volFormat=sc.COW_FORMAT,
            path='path')
        estimated_size = img.estimate_qcow2_size(vol_params, "sdUUID")

        assert estimated_size == 1074135040

    @pytest.mark.parametrize("storage,format,prealloc,estimate,expected", [
        # File raw preallocated, avoid prealocation.
        ("file", sc.RAW_FORMAT, sc.PREALLOCATED_VOL, 10 * GiB, 0),

        # File - anything else no initial size.
        ("file", sc.RAW_FORMAT, sc.SPARSE_VOL, 10 * GiB, None),
        ("file", sc.COW_FORMAT, sc.SPARSE_VOL, 10 * GiB, None),
        ("file", sc.COW_FORMAT, sc.PREALLOCATED_VOL, 10 * GiB, None),

        # Block qcow2 thin, return estimate.
        ("block", sc.COW_FORMAT, sc.SPARSE_VOL, 10 * GiB, 10 * GiB),

        # Block - anything else no initial size.
        ("block", sc.COW_FORMAT, sc.PREALLOCATED_VOL, 10 * GiB, None),
        ("block", sc.RAW_FORMAT, sc.PREALLOCATED_VOL, 10 * GiB, None),
    ])
    def test_calculate_initial_size_file_raw_prealloc(
            self, storage, format, prealloc, estimate, expected):
        img = image.Image("/path")
        initial_size = img.calculate_initial_size(
            storage == "file", format, prealloc, estimate)

        assert initial_size == expected
