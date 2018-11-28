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

import pytest

from storage.storagefakelib import fake_vg
from vdsm.storage import blockSD
from vdsm.storage import exception as se
from vdsm.storage import lvm
from vdsm import constants


TESTDIR = os.path.dirname(__file__)


class TestMetadataValidity:

    MIN_MD_SIZE = blockSD.VG_METADATASIZE * constants.MEGAB // 2
    MIN_MD_FREE = MIN_MD_SIZE * blockSD.VG_MDA_MIN_THRESHOLD

    def test_valid_ok(self):
        vg = fake_vg(
            vg_mda_size=self.MIN_MD_SIZE, vg_mda_free=self.MIN_MD_FREE)
        assert blockSD.metadataValidity(vg)['mdavalid']

    def test_valid_bad(self):
        vg = fake_vg(
            vg_mda_size=self.MIN_MD_SIZE - 1, vg_mda_free=self.MIN_MD_FREE)
        assert not blockSD.metadataValidity(vg)['mdavalid']

    def test_threshold_ok(self):
        vg = fake_vg(
            vg_mda_size=self.MIN_MD_SIZE, vg_mda_free=self.MIN_MD_FREE + 1)
        assert blockSD.metadataValidity(vg)['mdathreshold']

    def test_threshold_bad(self):
        vg = fake_vg(
            vg_mda_size=self.MIN_MD_SIZE, vg_mda_free=self.MIN_MD_FREE)
        assert not blockSD.metadataValidity(vg)['mdathreshold']


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


class TestGetAllVolumes:
    # TODO: add more tests, see fileSDTests.py

    def test_volumes_count(self, monkeypatch):
        monkeypatch.setattr(lvm, 'getLV', fakeGetLV)
        sdName = "3386c6f2-926f-42c4-839c-38287fac8998"
        allVols = blockSD.getAllVolumes(sdName)
        assert len(allVols) == 23

    def test_missing_tags(self, monkeypatch):
        monkeypatch.setattr(lvm, 'getLV', fakeGetLV)
        sdName = "f9e55e18-67c4-4377-8e39-5833ca422bef"
        allVols = blockSD.getAllVolumes(sdName)
        assert len(allVols) == 1


class TestDecodeValidity:

    def test_all_keys(self):
        value = ('pv:myname,uuid:Gk8q,pestart:0,'
                 'pecount:77,mapoffset:0')
        pvinfo = blockSD.decodePVInfo(value)
        assert pvinfo["guid"] == 'myname'
        assert pvinfo["uuid"] == 'Gk8q'
        assert pvinfo["pestart"] == '0'
        assert pvinfo["pecount"] == '77'
        assert pvinfo["mapoffset"] == '0'

    def test_decode_pv_colon(self):
        pvinfo = blockSD.decodePVInfo('pv:my:name')
        assert pvinfo["guid"] == 'my:name'

    @pytest.mark.xfail(reason='Comma in PV name is not supported yet')
    def test_decode_pv_comma(self):
        pvinfo = blockSD.decodePVInfo('pv:my,name')
        assert pvinfo["guid"] == 'my,name'


# VG size 10 GB
def test_meta_size_enough_free_space(monkeypatch):
    monkeypatch.setattr(lvm, 'getVG', lambda x: fake_vg(
        extent_size=str(128 * constants.MEGAB),
        extent_count='77',
        free=str(512 * constants.MEGAB)))
    meta_size = blockSD.BlockStorageDomain.metaSize('sd-uuid')
    assert meta_size == 512


# VG size 10 GB
def test_meta_size_vg_too_small(monkeypatch):
    # Creating a VG with size=10GB and 512MB - 1 byte of free space
    # Should raise an exception - VG too small
    monkeypatch.setattr(lvm, 'getVG', lambda x: fake_vg(
        extent_size=(128 * constants.MEGAB),
        extent_count='77',
        free=str((512 - 1) * constants.MEGAB)))
    with pytest.raises(se.VolumeGroupSizeError):
        blockSD.BlockStorageDomain.metaSize('sd-uuid')


# VG size 128.002 TB
def test_meta_size_min_val(monkeypatch):
    monkeypatch.setattr(lvm, 'getVG', lambda x: fake_vg(
        extent_size=(128 * constants.MEGAB),
        extent_count='1048576',
        free=str(512 * constants.MEGAB)))
    meta_size = blockSD.BlockStorageDomain.metaSize('sd-uuid')
    assert meta_size == 512


# VG size is 128.003 TB
def test_meta_size_max_val(monkeypatch):
    monkeypatch.setattr(lvm, 'getVG', lambda x: fake_vg(
        extent_size=(128 * constants.MEGAB),
        extent_count='1048577',
        free=str(1024 * constants.MEGAB)))
    meta_size = blockSD.BlockStorageDomain.metaSize('sd-uuid')
    assert meta_size == 513
