#
# Copyright 2016 Red Hat, Inc.
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

from monkeypatch import MonkeyPatchScope
from storagefakelib import FakeStorageDomainCache
from storagetestlib import FakeSD
from testlib import expandPermutations, permutations
from testlib import recorded
from testlib import VdsmTestCase

from vdsm.storage import constants as sc

from storage import resourceManager as rm
from storage import sd
from storage import volume

HOST_ID = 1


class FakeSDManifest(object):
    @recorded
    def acquireVolumeLease(self, hostId, imgUUID, volUUID):
        pass

    @recorded
    def releaseVolumeLease(self, imgUUID, volUUID):
        pass


@expandPermutations
class VolumeLeaseTest(VdsmTestCase):

    def test_properties(self):
        a = volume.VolumeLease(HOST_ID, 'dom', 'img', 'vol')
        self.assertEqual(sd.getNamespace(sc.VOLUME_LEASE_NAMESPACE, 'dom'),
                         a.ns)
        self.assertEqual('vol', a.name)
        self.assertEqual(rm.LockType.exclusive, a.mode)

    @permutations((
        (('domA', 'img', 'vol'), ('domB', 'img', 'vol')),
        (('dom', 'img', 'volA'), ('dom', 'img', 'volB')),
    ))
    def test_less_than(self, a, b):
        b = volume.VolumeLease(HOST_ID, *b)
        a = volume.VolumeLease(HOST_ID, *a)
        self.assertLess(a, b)

    def test_equality(self):
        a = volume.VolumeLease(HOST_ID, 'dom', 'img', 'vol')
        b = volume.VolumeLease(HOST_ID, 'dom', 'img', 'vol')
        self.assertEqual(a, b)

    def test_equality_different_image(self):
        a = volume.VolumeLease(HOST_ID, 'dom', 'img1', 'vol')
        b = volume.VolumeLease(HOST_ID, 'dom', 'img2', 'vol')
        self.assertEqual(a, b)

    def test_equality_different_host_id(self):
        a = volume.VolumeLease(0, 'dom', 'img', 'vol')
        b = volume.VolumeLease(1, 'dom', 'img', 'vol')
        self.assertEqual(a, b)

    def test_acquire_release(self):
        sdcache = FakeStorageDomainCache()
        manifest = FakeSDManifest()
        sdcache.domains['dom'] = FakeSD(manifest)
        expected = [('acquireVolumeLease', (HOST_ID, 'img', 'vol'), {}),
                    ('releaseVolumeLease', ('img', 'vol'), {})]
        with MonkeyPatchScope([(volume, 'sdCache', sdcache)]):
            lock = volume.VolumeLease(HOST_ID, 'dom', 'img', 'vol')
            lock.acquire()
            self.assertEqual(expected[:1], manifest.__calls__)
            lock.release()
            self.assertEqual(expected, manifest.__calls__)
