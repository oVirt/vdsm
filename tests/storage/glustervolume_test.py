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
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA  02110-1301 USA
#
# Refer to the README and COPYING files for full details of the license
#

from __future__ import absolute_import

from contextlib import contextmanager

from monkeypatch import MonkeyPatchScope
from testlib import VdsmTestCase
from storage.storagefakelib import FakeStorageDomainCache
from vdsm.gluster import exception
from vdsm.storage import glusterVolume


class FakeSD(object):
    def getRealPath(self):
        return "host.example.com:/volume"


class FakeSuperVdsm(object):
    def __init__(self, gluster_volume_info=None):
        self._gluster_volume_info = gluster_volume_info

    def getProxy(self):
        return self

    def glusterVolumeInfo(self, volname, volFileserver):
        if self._gluster_volume_info is not None:
            return self._gluster_volume_info
        else:
            raise exception.GlusterException


class GlusterVolume(glusterVolume.GlusterVolume):
    def __init__(self):
        pass  # Allow creating a volume object in a test

    @property
    def sdUUID(self):
        return "sd_id"

    def getVolumePath(self):
        return "/rhev/data-center/mnt/glusterSD/host.example.com:_volume/" \
               "sd_id/images/img_id/vol_id"


class TestVolumeInfo(VdsmTestCase):
    @contextmanager
    def make_env(self, gluster_volume_info):
        sdcache = FakeStorageDomainCache()
        sdcache.domains['sd_id'] = FakeSD()

        svdsm = FakeSuperVdsm(gluster_volume_info)

        with MonkeyPatchScope([(glusterVolume, 'sdCache', sdcache),
                               (glusterVolume, 'svdsm', svdsm)]):
            yield

    def test_no_data(self):
        expected = {
            "type": "network",
            "path": "volume/sd_id/images/img_id/vol_id",
            "protocol": "gluster",
            "hosts": [
                {
                    "name": "host.example.com",
                    "transport": "tcp",
                    "port": "0"
                }
            ]
        }
        self.check(None, expected)

    def test_parse_data(self):
        gluster_volume_info = {
            "volume": {
                "bricks": [
                    "host1.example.com:/rhgs/volume",
                    "host2.example.com:/rhgs/volume",
                    "host3.example.com:/rhgs/volume"
                ],
                "transportType": ["TCP"]
            }
        }

        expected = {
            "type": "network",
            "path": "volume/sd_id/images/img_id/vol_id",
            "protocol": "gluster",
            "hosts": [
                {
                    "name": "host.example.com",
                    "transport": "tcp",
                    "port": "0"
                },
                {
                    "name": "host1.example.com",
                    "transport": "tcp",
                    "port": "0"
                },
                {
                    "name": "host2.example.com",
                    "transport": "tcp",
                    "port": "0"
                },
                {
                    "name": "host3.example.com",
                    "transport": "tcp",
                    "port": "0"
                }
            ]
        }

        self.check(gluster_volume_info, expected)

    def test_unique_hosts(self):
        # brick, retrieved from mount path, should be
        # excluded from list of bricks, retrieved
        # using call to gluster.
        gluster_volume_info = {
            "volume": {
                "bricks": [
                    "host.example.com:/rhgs/volume",
                    "host2.example.com:/rhgs/volume",
                    "host3.example.com:/rhgs/volume"
                ],
                "transportType": ["TCP"]
            }
        }

        expected = {
            "type": "network",
            "path": "volume/sd_id/images/img_id/vol_id",
            "protocol": "gluster",
            "hosts": [
                {
                    "name": "host.example.com",
                    "transport": "tcp",
                    "port": "0"
                },
                {
                    "name": "host2.example.com",
                    "transport": "tcp",
                    "port": "0"
                },
                {
                    "name": "host3.example.com",
                    "transport": "tcp",
                    "port": "0"
                }
            ]
        }

        self.check(gluster_volume_info, expected)

    def test_rdma(self):
        gluster_volume_info = {
            "volume": {
                "bricks": [
                    "host.example.com:/rhgs/volume",
                    "host2.example.com:/rhgs/volume",
                    "host3.example.com:/rhgs/volume"
                ],
                "transportType": ["RDMA"]
            }
        }

        expected = {
            "type": "network",
            "path": "volume/sd_id/images/img_id/vol_id",
            "protocol": "gluster",
            "hosts": [
                {
                    "name": "host.example.com",
                    "transport": "rdma",
                    "port": "0"
                },
                {
                    "name": "host2.example.com",
                    "transport": "rdma",
                    "port": "0"
                },
                {
                    "name": "host3.example.com",
                    "transport": "rdma",
                    "port": "0"
                }
            ]
        }

        self.check(gluster_volume_info, expected)

    def check(self, gluster_volume_info, expected):
        with self.make_env(gluster_volume_info):
            gluster_volume_info = GlusterVolume().getVmVolumeInfo()
        self.assertEqual(gluster_volume_info, expected)
