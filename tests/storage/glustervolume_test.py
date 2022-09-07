# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import
from __future__ import division

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
