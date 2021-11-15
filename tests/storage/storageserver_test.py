#
# Copyright 2015-2016 Red Hat, Inc.
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
from __future__ import division

import pytest

from monkeypatch import MonkeyPatch
from testlib import permutations, expandPermutations
from testlib import VdsmTestCase
from vdsm.storage import sd
from vdsm.storage import storageServer
from vdsm.storage.storageServer import GlusterFSConnection
from vdsm.storage.storageServer import IscsiConnection
from vdsm.storage.storageServer import MountConnection

from vdsm.gluster import cli as gluster_cli
from vdsm.gluster import exception as ge


class FakeSupervdsm(object):

    def getProxy(self):
        return self


class FakeMount(object):
    def __init__(self, fs_spec, fs_file):
        self.fs_spec = fs_spec
        self.fs_file = fs_file


class TestIscsiConnectionMismatch(VdsmTestCase):

    def test_no_args(self):
        s = str(IscsiConnection.Mismatch("error 1"))
        self.assertEqual(s, "error 1")

    def test_format(self):
        s = str(IscsiConnection.Mismatch("error %d with %r", 1, "text"))
        self.assertEqual(s, "error 1 with 'text'")

    def test_format_mismatches_list(self):
        errors = [IscsiConnection.Mismatch("error 1"),
                  IscsiConnection.Mismatch("error 2")]
        expected = "%s" % str(["error 1", "error 2"])
        self.assertEqual(str(errors), expected)


@expandPermutations
class TestMountConnection(VdsmTestCase):

    @permutations([
        # spec, fs_spec, fs_file
        ("server:/a/", "server:/a", "/tmp/server:_a"),
        ("server:/a//", "server:/a", "/tmp/server:_a"),
        ("server:/a/b", "server:/a/b", "/tmp/server:_a_b"),
        ("server:/a//b", "server:/a/b", "/tmp/server:_a_b"),
        ("server:/a/b_c", "server:/a/b_c", "/tmp/server:_a_b__c"),
        ("server:/", "server:/", "/tmp/server:_"),
        ("server:6789:/path", "server:6789:/path", "/tmp/server:6789:_path"),
        ("server:6789:/", "server:6789:/", "/tmp/server:6789:_"),
    ])
    def test_normalize_local_path(self, spec, fs_spec, fs_file):
        con = MountConnection("id", spec, mountClass=FakeMount)
        self.assertEqual(con._mount.fs_spec, fs_spec)
        self.assertEqual(con._mount.fs_file, fs_file)


@expandPermutations
class TestMountConnectionEquality(VdsmTestCase):

    def test_eq_equal(self):
        c1 = MountConnection("id", "server:/path", "vfstype", "options")
        c2 = MountConnection("id", "server:/path", "vfstype", "options")
        self.assertTrue(c1 == c2, "%s should equal %s" % (c1, c2))

    def test_eq_subclass(self):
        class Subclass(MountConnection):
            pass
        c1 = MountConnection("id", "server:/path", "vfstype", "options")
        c2 = Subclass("id", "server:/path", "vfstype", "options")
        self.assertFalse(c1 == c2, "%s should not equal %s" % (c1, c2))

    @permutations([
        ("id", "id", "server:/path1", "server:/path2", "t", "t", "o", "o"),
        ("id", "id", "server:/path", "server:/path", "t1", "t2", "o", "o"),
        ("id", "id", "server:/path", "server:/path", "t", "t", "o1", "o2"),
        ("id1", "id2", "server:/path", "server:/path", "t", "t", "o", "o"),
    ])
    def test_eq_different(self, i1, i2, s1, s2, t1, t2, o1, o2):
        c1 = MountConnection(i1, s1, t1, o1)
        c2 = MountConnection(i2, s2, t2, o2)
        self.assertFalse(c1 == c2, "%s should not equal %s" % (c1, c2))

    def test_ne_equal(self):
        c1 = MountConnection("id", "server:/path", "vfstype", "options")
        c2 = MountConnection("id", "server:/path", "vfstype", "options")
        self.assertFalse(c1 != c2, "%s should equal %s" % (c1, c2))


@expandPermutations
class TestMountConnectionHash(VdsmTestCase):

    def test_equal_same_hash(self):
        c1 = MountConnection("id", "server:/path", "vfstype", "options")
        c2 = MountConnection("id", "server:/path", "vfstype", "options")
        self.assertEqual(hash(c1), hash(c2))

    def test_subclass_different_hash(self):
        class Subclass(MountConnection):
            pass
        c1 = MountConnection("id", "server:/path", "vfstype", "options")
        c2 = Subclass("id", "server:/path", "vfstype", "options")
        self.assertNotEqual(hash(c1), hash(c2))

    @permutations([
        ("id", "id", "server:/path1", "server:/path2", "t", "t", "o", "o"),
        ("id", "id", "server:/path", "server:/path", "t1", "t2", "o", "o"),
        ("id1", "id", "server:/path", "server:/path", "t", "t", "o1", "o2"),
        ("id2", "id", "server:/path", "server:/path", "t", "t", "o", "o"),
    ])
    def test_not_equal_different_hash(self, i1, i2, s1, s2, t1, t2, o1, o2):
        c1 = MountConnection(i1, s1, t1, o1)
        c2 = MountConnection(i2, s2, t2, o2)
        self.assertNotEqual(hash(c1), hash(c2))


@expandPermutations
class TestGlusterFSConnection(VdsmTestCase):

    def test_mountpoint(self):
        mount_con = GlusterFSConnection("id",
                                        "server:/volume",
                                        mountClass=FakeMount)
        self.assertEqual(mount_con._mount.fs_spec,
                         "server:/volume")
        self.assertEqual(mount_con._mount.fs_file,
                         "/tmp/glusterSD/server:_volume")

    @MonkeyPatch(gluster_cli, 'exists', lambda: True)
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

        gluster = GlusterFSConnection(id="id", spec="192.168.122.1:/music")
        self.assertEqual(gluster.options,
                         "backup-volfile-servers=192.168.122.2:192.168.122.3")

    @MonkeyPatch(gluster_cli, 'exists', lambda: True)
    @MonkeyPatch(storageServer, 'supervdsm', FakeSupervdsm())
    def test_server_not_in_volinfo(self):
        """
        This test simulates a use case where gluster server provided in the
        path doesn't appear in the volume info.
        """
        def glusterVolumeInfo(volname=None, volfileServer=None):
            return {'music': {'brickCount': '3',
                              'bricks': ['192.168.122.5:/tmp/music',
                                         '192.168.122.2:/tmp/music',
                                         '192.168.122.3:/tmp/music']}}

        storageServer.supervdsm.glusterVolumeInfo = glusterVolumeInfo

        gluster = GlusterFSConnection(id="id", spec="gluster-server:/music")
        expected_backup_servers = \
            "backup-volfile-servers=192.168.122.5:192.168.122.2:192.168.122.3"
        self.assertEqual(gluster.options, expected_backup_servers)

    @MonkeyPatch(gluster_cli, 'exists', lambda: True)
    @MonkeyPatch(storageServer, 'supervdsm', FakeSupervdsm())
    def test_duplicate_servers_in_volinfo(self):
        """
        This test verifies that servers list contains no duplicates.
        """
        def glusterVolumeInfo(volname=None, volfileServer=None):
            return {'music': {'brickCount': '3',
                              'bricks': ['192.168.122.5:/tmp/music',
                                         '192.168.122.2:/tmp/music',
                                         '192.168.122.2:/tmp/music',
                                         '192.168.122.5:/tmp/music',
                                         '192.168.122.3:/tmp/music']}}

        storageServer.supervdsm.glusterVolumeInfo = glusterVolumeInfo

        gluster = GlusterFSConnection(id="id", spec="gluster-server:/music")
        expected_backup_servers = \
            "backup-volfile-servers=192.168.122.5:192.168.122.2:192.168.122.3"
        self.assertEqual(gluster.options, expected_backup_servers)

    @MonkeyPatch(gluster_cli, 'exists', lambda: True)
    @MonkeyPatch(storageServer, 'supervdsm', FakeSupervdsm())
    def test_gluster_and_user_provided_mount_options(self):
        def glusterVolumeInfo(volname=None, volfileServer=None):
            return {'music': {'brickCount': '3',
                              'bricks': ['192.168.122.1:/tmp/music',
                                         '192.168.122.2:/tmp/music',
                                         '192.168.122.3:/tmp/music']}}

        storageServer.supervdsm.glusterVolumeInfo = glusterVolumeInfo

        gluster = GlusterFSConnection(id="id", spec="192.168.122.1:/music",
                                      options="option1=val1")
        expected_options = \
            "option1=val1,backup-volfile-servers=192.168.122.2:192.168.122.3"
        self.assertEqual(gluster.options, expected_options)

    @MonkeyPatch(storageServer, 'supervdsm', FakeSupervdsm())
    def test_gluster_replica1_mount_options(self):
        def glusterVolumeInfo(volname=None, volfileServer=None):
            self.assertEqual(volname, "music")
            self.assertEqual(volfileServer, "192.168.122.1")
            return {'music': {'brickCount': '1',
                              'bricks': ['192.168.122.1:/tmp/music']}}

        storageServer.supervdsm.glusterVolumeInfo = glusterVolumeInfo

        gluster = GlusterFSConnection(id="id", spec="192.168.122.1:/music")
        self.assertEqual(gluster.options, "")

    def test_user_provided_gluster_mount_options(self):

        def glusterVolumeInfo(volname=None, volfileServer=None):
            return None

        user_options = "backup-volfile-servers=server1:server2"
        gluster = GlusterFSConnection(id="id", spec="192.168.122.1:/music",
                                      options=user_options)
        self.assertEqual(gluster.options, user_options)

    @MonkeyPatch(storageServer, 'supervdsm', FakeSupervdsm())
    @MonkeyPatch(GlusterFSConnection, 'ALLOWED_REPLICA_COUNTS', ('1', '3'))
    @permutations([['1'], ['2'], ['3'], ['4']])
    def test_allowed_gluster_replica_count(self, replica_count):

        def glusterVolumeInfo(volumeName=None, remoteServer=None):
            return {'music': {'replicaCount': replica_count,
                              'volumeType': 'REPLICATE'}}

        storageServer.supervdsm.glusterVolumeInfo = glusterVolumeInfo

        gluster = GlusterFSConnection(id="id", spec="192.168.122.1:/music")
        gluster.validate()

    @MonkeyPatch(gluster_cli, 'exists', lambda: False)
    def test_glusterfs_cli_missing(self):
        gluster = GlusterFSConnection(id="id", spec="192.168.122.1:/music")
        self.assertEqual(gluster.options, "")


@expandPermutations
class TestGlusterFSNotAccessibleConnection(VdsmTestCase):

    def glusterVolumeInfo(self, volumeName=None, remoteServer=None):
        raise ge.GlusterCmdExecFailedException()

    @MonkeyPatch(storageServer, 'supervdsm', FakeSupervdsm())
    @MonkeyPatch(gluster_cli, 'exists', lambda: True)
    def test_validate(self):
        storageServer.supervdsm.glusterVolumeInfo = self.glusterVolumeInfo

        gluster = GlusterFSConnection(id="id", spec="192.168.122.1:/music")
        gluster.validate()

    @MonkeyPatch(storageServer, 'supervdsm', FakeSupervdsm())
    @MonkeyPatch(gluster_cli, 'exists', lambda: True)
    @permutations([[''], ['backup-volfile-servers=server1:server2']])
    def test_mount_options(self, userMountOptions):
        storageServer.supervdsm.glusterVolumeInfo = self.glusterVolumeInfo

        gluster = GlusterFSConnection(id="id", spec="192.168.122.1:/music",
                                      options=userMountOptions)
        self.assertEqual(gluster.options, userMountOptions)


def test_prepare_connection_without_initiator_name():
    con_def = [{
        "password": "password",
        "port": "3260",
        "iqn": "iqn.2016-01.com.ovirt:444",
        "connection": "192.168.1.2",
        "ipv6_enabled": "false",
        "id": "994a711a-60f3-411a-aca2-0b60f01e8b8c",
        "user": "",
        "tpgt": "1",
    }]
    conn = storageServer._prepare_connections(sd.ISCSI_DOMAIN, con_def)

    # Unset keys raise KeyError
    with pytest.raises(KeyError):
        conn[0].iface.initiatorName
