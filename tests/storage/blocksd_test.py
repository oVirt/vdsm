#
# Copyright 2014-2017 Red Hat, Inc.
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
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA
# 02110-1301  USA
#
# Refer to the README and COPYING files for full details of the license
#

from __future__ import absolute_import
from __future__ import division

import os

from monkeypatch import MonkeyPatch
from storage.storagefakelib import fake_vg
from testValidation import xfail
from testlib import VdsmTestCase
from vdsm.storage import blockSD
from vdsm.storage import lvm
from vdsm import constants


TESTDIR = os.path.dirname(__file__)


class TestMetadataValidity(VdsmTestCase):

    MIN_MD_SIZE = blockSD.VG_METADATASIZE * constants.MEGAB // 2
    MIN_MD_FREE = MIN_MD_SIZE * blockSD.VG_MDA_MIN_THRESHOLD

    def test_valid_ok(self):
        vg = fake_vg(
            vg_mda_size=self.MIN_MD_SIZE, vg_mda_free=self.MIN_MD_FREE)
        self.assertEqual(True, blockSD.metadataValidity(vg)['mdavalid'])

    def test_valid_bad(self):
        vg = fake_vg(
            vg_mda_size=self.MIN_MD_SIZE - 1, vg_mda_free=self.MIN_MD_FREE)
        self.assertEqual(False, blockSD.metadataValidity(vg)['mdavalid'])

    def test_threshold_ok(self):
        vg = fake_vg(
            vg_mda_size=self.MIN_MD_SIZE, vg_mda_free=self.MIN_MD_FREE + 1)
        self.assertEqual(True, blockSD.metadataValidity(vg)['mdathreshold'])

    def test_threshold_bad(self):
        vg = fake_vg(
            vg_mda_size=self.MIN_MD_SIZE, vg_mda_free=self.MIN_MD_FREE)
        self.assertEqual(False, blockSD.metadataValidity(vg)['mdathreshold'])


def fakeGetLV(vgName):
    """ This function returns lvs output in lvm.getLV() format.

    Input file name: lvs_<sdName>.out
    Input file should be the output of:
    lvs --noheadings --units b --nosuffix --separator '|' \
        -o uuid,name,vg_name,attr,size,seg_start_pe,devices,tags <sdName>

    """
    # TODO: simplify by returning fake lvs instead of parsing real lvs output.
    lvs_output = os.path.join(TESTDIR, 'lvs_%s.out' % vgName)
    lvs = []
    with open(lvs_output) as f:
        for line in f:
            fields = [field.strip() for field in line.split(lvm.SEPARATOR)]
            lvs.append(lvm.makeLV(*fields))
    return lvs


class TestGetAllVolumes(VdsmTestCase):
    # TODO: add more tests, see fileSDTests.py

    @MonkeyPatch(lvm, 'getLV', fakeGetLV)
    def test_volumes_count(self):
        sdName = "3386c6f2-926f-42c4-839c-38287fac8998"
        allVols = blockSD.getAllVolumes(sdName)
        self.assertEqual(len(allVols), 23)

    @MonkeyPatch(lvm, 'getLV', fakeGetLV)
    def test_missing_tags(self):
        sdName = "f9e55e18-67c4-4377-8e39-5833ca422bef"
        allVols = blockSD.getAllVolumes(sdName)
        self.assertEqual(len(allVols), 1)


class TestDecodeValidity(VdsmTestCase):

    def test_all_keys(self):
        value = ('pv:myname,uuid:Gk8q,pestart:0,'
                 'pecount:77,mapoffset:0')
        pvinfo = blockSD.decodePVInfo(value)
        self.assertEqual(pvinfo["guid"], 'myname')
        self.assertEqual(pvinfo["uuid"], 'Gk8q')
        self.assertEqual(pvinfo["pestart"], '0')
        self.assertEqual(pvinfo["pecount"], '77')
        self.assertEqual(pvinfo["mapoffset"], '0')

    def test_decode_pv_colon(self):
        pvinfo = blockSD.decodePVInfo('pv:my:name')
        self.assertEqual(pvinfo["guid"], 'my:name')

    @xfail('Comma in PV name is not supported yet')
    def test_decode_pv_comma(self):
        pvinfo = blockSD.decodePVInfo('pv:my,name')
        self.assertEqual(pvinfo["guid"], 'my,name')
