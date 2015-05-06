#
# Copyright 2015 Red Hat, Inc.
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

from monkeypatch import MonkeyPatch
from testlib import VdsmTestCase
from storage.storageServer import GlusterFSConnection
from storage.storageServer import IscsiConnection
from storage.storageServer import MountConnection
from storage import storageServer


class FakeSupervdsm(object):

    def getProxy(self):
        return self


class FakeMount(object):
    def __init__(self, fs_spec, fs_file):
        self.fs_spec = fs_spec
        self.fs_file = fs_file


class IscsiConnectionMismatchTests(VdsmTestCase):

    def test_no_args(self):
        s = str(IscsiConnection.Mismatch("error 1"))
        self.assertEqual(s, "error 1")

    def test_format(self):
        s = str(IscsiConnection.Mismatch("error %d with %r", 1, "text"))
        self.assertEqual(s, "error 1 with 'text'")

    def test_format_mismatches_list(self):
        errors = [IscsiConnection.Mismatch("error 1"),
                  IscsiConnection.Mismatch("error 2")]
        expected = "%s" % ["error 1", "error 2"]
        self.assertEqual(str(errors), expected)


class MountConnectionTests(VdsmTestCase):

    def test_mountpoint(self):
        mount_con = MountConnection("dummy-spec", mountClass=FakeMount)
        self.assertEquals(mount_con._mount.fs_spec, "dummy-spec")
        self.assertEquals(mount_con._mount.fs_file, "/tmp/dummy-spec")


class GlusterFSConnectionTests(VdsmTestCase):

    def test_mountpoint(self):
        mount_con = GlusterFSConnection("server:/volume", mountClass=FakeMount)
        self.assertEquals(mount_con._mount.fs_spec,
                          "server:/volume")
        self.assertEquals(mount_con._mount.fs_file,
                          "/tmp/glusterSD/server:_volume")

    @MonkeyPatch(storageServer, 'supervdsm', FakeSupervdsm())
    def test_gluster_replica3_mount_options(self):
        def glusterVolumeInfo(volname=None, volfileServer=None):
            self.assertEqual(volname, "music")
            self.assertEqual(volfileServer, "192.168.122.1")
            return {'music': {'brickCount': '3',
                              'bricks': ['192.168.122.1:/tmp/music',
                                         '192.168.122.2:/tmp/music',
                                         '192.168.122.3:/tmp/music']}}

        storageServer.supervdsm.glusterVolumeInfo = glusterVolumeInfo

        gluster = GlusterFSConnection(spec="192.168.122.1:/music")
        self.assertEquals(gluster.options,
                          "backup-volfile-servers=192.168.122.2:192.168.122.3")

    @MonkeyPatch(storageServer, 'supervdsm', FakeSupervdsm())
    def test_gluster_and_user_provided_mount_options(self):
        def glusterVolumeInfo(volname=None, volfileServer=None):
            return {'music': {'brickCount': '3',
                              'bricks': ['192.168.122.1:/tmp/music',
                                         '192.168.122.2:/tmp/music',
                                         '192.168.122.3:/tmp/music']}}

        storageServer.supervdsm.glusterVolumeInfo = glusterVolumeInfo

        gluster = GlusterFSConnection(spec="192.168.122.1:/music",
                                      options="option1=val1")
        expected_options = \
            "option1=val1,backup-volfile-servers=192.168.122.2:192.168.122.3"
        self.assertEquals(gluster.options, expected_options)

    @MonkeyPatch(storageServer, 'supervdsm', FakeSupervdsm())
    def test_gluster_replica1_mount_options(self):
        def glusterVolumeInfo(volname=None, volfileServer=None):
            self.assertEqual(volname, "music")
            self.assertEqual(volfileServer, "192.168.122.1")
            return {'music': {'brickCount': '1',
                              'bricks': ['192.168.122.1:/tmp/music']}}

        storageServer.supervdsm.glusterVolumeInfo = glusterVolumeInfo

        gluster = GlusterFSConnection(spec="192.168.122.1:/music")
        self.assertEquals(gluster.options, "")

    def test_user_provided_gluster_mount_options(self):
        user_options = "backup-volfile-servers=server1:server2"
        gluster = GlusterFSConnection(spec="192.168.122.1:/music",
                                      options=user_options)
        self.assertEquals(gluster.options, user_options)
