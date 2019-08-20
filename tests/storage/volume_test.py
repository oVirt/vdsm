#
# Copyright 2016-2018 Red Hat, Inc.
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

import os
import pytest

from storage.storagefakelib import FakeStorageDomainCache

from storage.storagetestlib import (
    FakeSD,
    fake_volume
)

from testlib import recorded

from vdsm.storage import constants as sc
from vdsm.storage import exception as se
from vdsm.storage import resourceManager as rm
from vdsm.storage import volume


HOST_ID = 1
MB = 1048576


class FakeSDManifest(object):
    @recorded
    def acquireVolumeLease(self, hostId, imgUUID, volUUID):
        pass

    @recorded
    def releaseVolumeLease(self, imgUUID, volUUID):
        pass


class TestVolumeLease:

    def test_properties(self):
        a = volume.VolumeLease(HOST_ID, 'dom', 'img', 'vol')
        assert rm.getNamespace(sc.VOLUME_LEASE_NAMESPACE, 'dom') == a.ns
        assert 'vol' == a.name
        assert rm.EXCLUSIVE == a.mode

    @pytest.mark.parametrize("a, b", [
        (('domA', 'img', 'vol'), ('domB', 'img', 'vol')),
        (('dom', 'img', 'volA'), ('dom', 'img', 'volB'))
    ])
    def test_less_than(self, a, b):
        b = volume.VolumeLease(HOST_ID, *b)
        a = volume.VolumeLease(HOST_ID, *a)
        assert a < b

    def test_equality(self):
        a = volume.VolumeLease(HOST_ID, 'dom', 'img', 'vol')
        b = volume.VolumeLease(HOST_ID, 'dom', 'img', 'vol')
        assert a == b

    def test_equality_different_image(self):
        a = volume.VolumeLease(HOST_ID, 'dom', 'img1', 'vol')
        b = volume.VolumeLease(HOST_ID, 'dom', 'img2', 'vol')
        assert a == b

    def test_equality_different_host_id(self):
        a = volume.VolumeLease(0, 'dom', 'img', 'vol')
        b = volume.VolumeLease(1, 'dom', 'img', 'vol')
        assert a == b

    def test_acquire_release(self, monkeypatch):
        sdcache = FakeStorageDomainCache()
        manifest = FakeSDManifest()
        sdcache.domains['dom'] = FakeSD(manifest)
        expected = [('acquireVolumeLease', (HOST_ID, 'img', 'vol'), {}),
                    ('releaseVolumeLease', ('img', 'vol'), {})]
        monkeypatch.setattr(volume, 'sdCache', sdcache)
        lock = volume.VolumeLease(HOST_ID, 'dom', 'img', 'vol')
        lock.acquire()
        assert expected[:1] == manifest.__calls__
        lock.release()
        assert expected == manifest.__calls__

    def test_repr(self):
        lock = volume.VolumeLease(HOST_ID, 'dom', 'img', 'vol')
        lock_string = str(lock)
        assert "VolumeLease" in lock_string
        assert "ns=04_lease_dom" in lock_string
        assert "name=vol" in lock_string
        assert "mode=exclusive" in lock_string
        assert "%x" % id(lock) in lock_string


class TestVolumeManifest:

    @pytest.fixture
    def vol(self):
        with fake_volume('file') as vol:
            yield vol

    def test_operation(self, vol):
        vol.setMetadata = CountedInstanceMethod(vol.setMetadata)
        assert sc.LEGAL_VOL == vol.getLegality()
        with vol.operation():
            assert sc.ILLEGAL_VOL == vol.getLegality()
            assert 1 == vol.setMetadata.nr_calls
        assert sc.LEGAL_VOL == vol.getLegality()
        assert 2 == vol.setMetadata.nr_calls

    def test_operation_fail_inside_context(self, vol):
        assert sc.LEGAL_VOL == vol.getLegality()
        with pytest.raises(ValueError):
            with vol.operation():
                raise ValueError()
        assert sc.ILLEGAL_VOL == vol.getLegality()

    @pytest.mark.parametrize("orig_gen, info_gen", [(None, 0), (100, 100)])
    def test_get_info_generation_id(self, vol, orig_gen, info_gen):
        vol.getLeaseStatus = lambda: {}
        if orig_gen is not None:
            vol.setMetaParam(sc.GENERATION, orig_gen)
        assert info_gen == vol.getInfo()['generation']

    def test_operation_valid_generation(self, vol):
        generation = 100
        vol.setMetaParam(sc.GENERATION, generation)
        with vol.operation(generation):
            pass
        assert generation + 1 == vol.getMetaParam(sc.GENERATION)

    @pytest.mark.parametrize("actual_generation, requested_generation", [
        (100, 99), (100, 101)
    ])
    def test_operation_invalid_generation_raises(self, vol, actual_generation,
                                                 requested_generation):
        vol.setMetaParam(sc.GENERATION, actual_generation)
        with pytest.raises(se.GenerationMismatch):
            with vol.operation(requested_generation):
                pass
        assert actual_generation == vol.getMetaParam(sc.GENERATION)

    @pytest.mark.parametrize("first_gen, next_gen", [
        (sc.MAX_GENERATION, 0),
        (sc.MAX_GENERATION - 1, sc.MAX_GENERATION)
    ])
    def test_generation_wrapping(self, vol, first_gen, next_gen):
        vol.setMetaParam(sc.GENERATION, first_gen)
        with vol.operation(first_gen):
            pass
        assert next_gen == vol.getMetaParam(sc.GENERATION)

    def test_operation_on_illegal_volume(self, vol):
        # This volume was illegal before the operation
        vol.setMetaParam(sc.LEGALITY, sc.ILLEGAL_VOL)
        vol.setMetaParam(sc.GENERATION, 0)
        with vol.operation(requested_gen=0, set_illegal=False):
            # It should remain illegal during the operation
            assert sc.ILLEGAL_VOL == vol.getMetaParam(sc.LEGALITY)
            pass
        assert 1 == vol.getMetaParam(sc.GENERATION)
        # It should remain illegal after the operation
        assert sc.ILLEGAL_VOL == vol.getMetaParam(sc.LEGALITY)

    def test_operation_modifying_metadata(self, vol):
        with vol.operation(requested_gen=0, set_illegal=False):
            vol.setMetaParam(sc.DESCRIPTION, "description")
        # Metadata changes inside the context should not be overriden by
        # wirting the new generation.
        assert "description" == vol.getMetaParam(sc.DESCRIPTION)


class TestVolumeSize:

    @pytest.fixture(params=[sc.RAW_FORMAT, sc.COW_FORMAT])
    def vol(self, request):
        with fake_volume("file", size=MB, format=request.param) as vol:
            yield vol

    def test_get_info_size(self, monkeypatch, vol):
        # Avoid calling sanlock during tests.

        def getVolumeLease(img_id, vol_id):
            return None, None, None

        sd = volume.sdCache.produce_manifest(vol.sdUUID)
        sd.getVolumeLease = getVolumeLease

        info = vol.getInfo()
        assert info["capacity"] == str(MB)

        st = os.stat(vol.getVolumePath())
        assert info["apparentsize"] == str(st.st_size)
        assert info["truesize"] == str(st.st_blocks * 512)


class CountedInstanceMethod(object):
    def __init__(self, method):
        self._method = method
        self.nr_calls = 0

    def __call__(self, *args, **kwargs):
        self.nr_calls += 1
        return self._method(*args, **kwargs)
