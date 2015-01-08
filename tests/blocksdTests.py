#
# Copyright 2014-2015 Red Hat, Inc.
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

import collections
import os

from testlib import VdsmTestCase as TestCaseBase

from storage import blockSD
from storage import lvm
from vdsm import constants

# Make it easy to test the values we care about
VG = collections.namedtuple("VG", ['vg_mda_size', 'vg_mda_free'])

TESTDIR = os.path.dirname(__file__)


class MatadataValidityTests(TestCaseBase):

    MIN_MD_SIZE = blockSD.VG_METADATASIZE * constants.MEGAB / 2
    MIN_MD_FREE = MIN_MD_SIZE * blockSD.VG_MDA_MIN_THRESHOLD

    def test_valid_ok(self):
        vg = VG(self.MIN_MD_SIZE, self.MIN_MD_FREE)
        self.assertEquals(True, blockSD.metadataValidity(vg)['mdavalid'])

    def test_valid_bad(self):
        vg = VG(self.MIN_MD_SIZE - 1, self.MIN_MD_FREE)
        self.assertEquals(False, blockSD.metadataValidity(vg)['mdavalid'])

    def test_threshold_ok(self):
        vg = VG(self.MIN_MD_SIZE, self.MIN_MD_FREE + 1)
        self.assertEquals(True, blockSD.metadataValidity(vg)['mdathreshold'])

    def test_threshold_bad(self):
        vg = VG(self.MIN_MD_SIZE, self.MIN_MD_FREE)
        self.assertEquals(False, blockSD.metadataValidity(vg)['mdathreshold'])


class TestBlockGetAllVolumes(TestCaseBase):
    def mGetLV(self, vgName):
        """ This function returns lvs output in lvm.getLV() format.

        Input file name: lvs_<sdName>.out
        Input file should be the output of:
        lvs --noheadings --units b --nosuffix --separator '|' \
            -o uuid,name,vg_name,attr,size,seg_start_pe,devices,tags <sdName>
        """
        lvs = []
        lvs_out = open(os.path.join(TESTDIR, 'lvs_%s.out' % vgName),
                       "r").read()
        for line in lvs_out.split():
            fields = [field.strip() for field in
                      line.split(lvm.SEPARATOR)]
            lvs.append(lvm.makeLV(*fields))
        return lvs

    def test_getAllVolumes(self):
        blockSD.lvm.getLV = self.mGetLV
        sdName = "3386c6f2-926f-42c4-839c-38287fac8998"
        allVols = blockSD.getAllVolumes(sdName)
        self.assertEqual(len(allVols), 23)
