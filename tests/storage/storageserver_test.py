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

import os
import uuid
import random

import pytest

from vdsm.storage import iscsi
from vdsm.storage import sd
from vdsm.storage import storageServer
from vdsm.storage.storageServer import GlusterFSConnection
from vdsm.storage.storageServer import IscsiConnection
from vdsm.storage.storageServer import MountConnection

from vdsm.gluster import cli as gluster_cli
from vdsm.gluster import exception as ge

from . marks import requires_root


class FakeSupervdsm(object):

    def getProxy(self):
        return self


class FakeMount(object):
    def __init__(self, fs_spec, fs_file):
        self.fs_spec = fs_spec
        self.fs_file = fs_file


class TestIscsiConnectionMismatch:

    def test_no_args(self):
        s = str(IscsiConnection.Mismatch("error 1"))
        assert s == "error 1"

    def test_format(self):
        s = str(IscsiConnection.Mismatch("error %d with %r", 1, "text"))
        assert s == "error 1 with 'text'"

    def test_format_mismatches_list(self):
        errors = [IscsiConnection.Mismatch("error 1"),
                  IscsiConnection.Mismatch("error 2")]
        expected = "%s" % str(["error 1", "error 2"])
        assert str(errors) == expected


class TestMountConnection:

    @pytest.mark.parametrize("spec, fs_spec, fs_file", [
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
        assert con._mount.fs_spec == fs_spec
        assert con._mount.fs_file == fs_file


class TestMountConnectionEquality:

    def test_eq_equal(self):
        c1 = MountConnection("id", "server:/path", "vfstype", "options")
        c2 = MountConnection("id", "server:/path", "vfstype", "options")
        assert c1 == c2

    def test_eq_subclass(self):
        class Subclass(MountConnection):
            pass
        c1 = MountConnection("id", "server:/path", "vfstype", "options")
        c2 = Subclass("id", "server:/path", "vfstype", "options")
        assert c1 != c2

    @pytest.mark.parametrize("i1,i2,s1,s2,t1,t2,o1,o2", [
        ("id", "id", "server:/path1", "server:/path2", "t", "t", "o", "o"),
        ("id", "id", "server:/path", "server:/path", "t1", "t2", "o", "o"),
        ("id", "id", "server:/path", "server:/path", "t", "t", "o1", "o2"),
        ("id1", "id2", "server:/path", "server:/path", "t", "t", "o", "o"),
    ])
    def test_eq_different(self, i1, i2, s1, s2, t1, t2, o1, o2):
        c1 = MountConnection(i1, s1, t1, o1)
        c2 = MountConnection(i2, s2, t2, o2)
        assert not c1 == c2

    def test_ne_equal(self):
        c1 = MountConnection("id", "server:/path", "vfstype", "options")
        c2 = MountConnection("id", "server:/path", "vfstype", "options")
        assert not c1 != c2


class TestMountConnectionHash:

    def test_equal_same_hash(self):
        c1 = MountConnection("id", "server:/path", "vfstype", "options")
        c2 = MountConnection("id", "server:/path", "vfstype", "options")
        assert hash(c1) == hash(c2)

    def test_subclass_different_hash(self):
        class Subclass(MountConnection):
            pass
        c1 = MountConnection("id", "server:/path", "vfstype", "options")
        c2 = Subclass("id", "server:/path", "vfstype", "options")
        assert hash(c1) != hash(c2)

    @pytest.mark.parametrize("i1,i2,s1,s2,t1,t2,o1,o2", [
        ("id", "id", "server:/path1", "server:/path2", "t", "t", "o", "o"),
        ("id", "id", "server:/path", "server:/path", "t1", "t2", "o", "o"),
        ("id1", "id", "server:/path", "server:/path", "t", "t", "o1", "o2"),
        ("id2", "id", "server:/path", "server:/path", "t", "t", "o", "o"),
    ])
    def test_not_equal_different_hash(self, i1, i2, s1, s2, t1, t2, o1, o2):
        c1 = MountConnection(i1, s1, t1, o1)
        c2 = MountConnection(i2, s2, t2, o2)
        assert hash(c1) != hash(c2)


class TestGlusterFSConnection:

    def test_mountpoint(self):
        mount_con = GlusterFSConnection("id",
                                        "server:/volume",
                                        mountClass=FakeMount)
        assert mount_con._mount.fs_spec == "server:/volume"
        assert mount_con._mount.fs_file == "/tmp/glusterSD/server:_volume"

    def test_gluster_replica3_mount_options(self, monkeypatch):
        monkeypatch.setattr(storageServer, 'supervdsm', FakeSupervdsm())
        monkeypatch.setattr(gluster_cli, 'exists', lambda: True)

        def glusterVolumeInfo(volname=None, volfileServer=None):
            assert volname == "music"
            assert volfileServer == "192.168.122.1"
            return {'music': {'brickCount': '3',
                              'bricks': ['192.168.122.1:/tmp/music',
                                         '192.168.122.2:/tmp/music',
                                         '192.168.122.3:/tmp/music']}}

        storageServer.supervdsm.glusterVolumeInfo = glusterVolumeInfo

        gluster = GlusterFSConnection(id="id", spec="192.168.122.1:/music")
        expected_options = "backup-volfile-servers=192.168.122.2:192.168.122.3"
        assert gluster.options == expected_options

    def test_server_not_in_volinfo(self, monkeypatch):
        """
        This test simulates a use case where gluster server provided in the
        path doesn't appear in the volume info.
        """
        monkeypatch.setattr(storageServer, 'supervdsm', FakeSupervdsm())
        monkeypatch.setattr(gluster_cli, 'exists', lambda: True)

        def glusterVolumeInfo(volname=None, volfileServer=None):
            return {'music': {'brickCount': '3',
                              'bricks': ['192.168.122.5:/tmp/music',
                                         '192.168.122.2:/tmp/music',
                                         '192.168.122.3:/tmp/music']}}

        storageServer.supervdsm.glusterVolumeInfo = glusterVolumeInfo

        gluster = GlusterFSConnection(id="id", spec="gluster-server:/music")
        expected_backup_servers = \
            "backup-volfile-servers=192.168.122.5:192.168.122.2:192.168.122.3"
        assert gluster.options == expected_backup_servers

    def test_duplicate_servers_in_volinfo(self, monkeypatch):
        """
        This test verifies that servers list contains no duplicates.
        """
        monkeypatch.setattr(storageServer, 'supervdsm', FakeSupervdsm())
        monkeypatch.setattr(gluster_cli, 'exists', lambda: True)

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
        assert gluster.options == expected_backup_servers

    def test_gluster_and_user_provided_mount_options(self, monkeypatch):
        monkeypatch.setattr(storageServer, 'supervdsm', FakeSupervdsm())
        monkeypatch.setattr(gluster_cli, 'exists', lambda: True)

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
        assert gluster.options == expected_options

    def test_gluster_replica1_mount_options(self, monkeypatch):
        monkeypatch.setattr(storageServer, 'supervdsm', FakeSupervdsm())

        def glusterVolumeInfo(volname=None, volfileServer=None):
            assert volname == "music"
            assert volfileServer == "192.168.122.1"
            return {'music': {'brickCount': '1',
                              'bricks': ['192.168.122.1:/tmp/music']}}

        storageServer.supervdsm.glusterVolumeInfo = glusterVolumeInfo

        gluster = GlusterFSConnection(id="id", spec="192.168.122.1:/music")
        assert gluster.options == ""

    def test_user_provided_gluster_mount_options(self):

        def glusterVolumeInfo(volname=None, volfileServer=None):
            return None

        user_options = "backup-volfile-servers=server1:server2"
        gluster = GlusterFSConnection(id="id", spec="192.168.122.1:/music",
                                      options=user_options)
        assert gluster.options == user_options

    @pytest.mark.parametrize("replica_count", [['1'], ['2'], ['3'], ['4']])
    def test_allowed_gluster_replica_count(self, monkeypatch, replica_count):
        monkeypatch.setattr(storageServer, 'supervdsm', FakeSupervdsm())
        monkeypatch.setattr(
            GlusterFSConnection, 'ALLOWED_REPLICA_COUNTS', ('1', '3'))

        def glusterVolumeInfo(volumeName=None, remoteServer=None):
            return {'music': {'replicaCount': replica_count,
                              'volumeType': 'REPLICATE'}}

        storageServer.supervdsm.glusterVolumeInfo = glusterVolumeInfo

        gluster = GlusterFSConnection(id="id", spec="192.168.122.1:/music")
        gluster.validate()

    def test_glusterfs_cli_missing(self, monkeypatch):
        monkeypatch.setattr(gluster_cli, 'exists', lambda: False)

        gluster = GlusterFSConnection(id="id", spec="192.168.122.1:/music")
        assert gluster.options == ""


class TestGlusterFSNotAccessibleConnection:

    def glusterVolumeInfo(self, volumeName=None, remoteServer=None):
        raise ge.GlusterCmdExecFailedException()

    def test_validate(self, monkeypatch):
        monkeypatch.setattr(storageServer, 'supervdsm', FakeSupervdsm())
        monkeypatch.setattr(gluster_cli, 'exists', lambda: True)

        storageServer.supervdsm.glusterVolumeInfo = self.glusterVolumeInfo

        gluster = GlusterFSConnection(id="id", spec="192.168.122.1:/music")
        gluster.validate()

    @pytest.mark.parametrize("userMountOptions", [
        '',
        'backup-volfile-servers=server1:server2',
    ])
    def test_mount_options(self, monkeypatch, userMountOptions):
        monkeypatch.setattr(storageServer, 'supervdsm', FakeSupervdsm())
        monkeypatch.setattr(gluster_cli, 'exists', lambda: True)

        storageServer.supervdsm.glusterVolumeInfo = self.glusterVolumeInfo

        gluster = GlusterFSConnection(id="id", spec="192.168.122.1:/music",
                                      options=userMountOptions)
        assert gluster.options == userMountOptions


class TestIscsiConnection:

    class FakeIscsi():

        def __init__(self):
            self.connections = []

        def loginToIscsiNode(self, iface, target):
            self.connections.append(target)

    @pytest.fixture
    def fake_iscsi(self, monkeypatch):
        fake_iscsi = self.FakeIscsi()
        monkeypatch.setattr(
            iscsi, 'loginToIscsiNode', fake_iscsi.loginToIscsiNode)
        return fake_iscsi

    @pytest.fixture
    def fake_connection(self):
        def _return_connection(valid=True):
            connection = [
                {'password': 'password',
                 'port': '3260',
                 'iqn': 'iqn.2016-01.com.ovirt:444',
                 'connection':
                    f'fd8f:1391:3a82:200::c0a8:{random.randint(0, 16**4):x}',
                 'ipv6_enabled': 'true',
                 'id': str(uuid.uuid4()),
                 'user': 'username',
                 'tpgt': '1'}]
            if not valid:
                # Break connection dictionary by removing a key.
                del(connection[0]['iqn'])
            return connection

        return _return_connection

    @pytest.fixture
    def dm_device(self, monkeypatch, tmpdir):
        """
        Create a dm device in a temporary non-root folder.
        Device mapper fixtures do not work properly in containters.
        """
        device_name = str(uuid.uuid4())
        dm_dir = os.path.join(tmpdir, device_name)
        os.mkdir(dm_dir)
        monkeypatch.setattr(storageServer, 'DMPATH_PREFIX', tmpdir)
        return device_name

    def test_prepare_connection_without_initiator_name(self, fake_connection):
        con_def = fake_connection()

        con_class, cons = storageServer._prepare_connections(
            sd.ISCSI_DOMAIN, con_def)
        con = cons[0]

        # Check we get right connection class.
        assert con_class == IscsiConnection

        # Connection class has to be same type as actual connection object.
        assert con_class == type(con)

        # Unset keys raise KeyError
        with pytest.raises(KeyError):
            con.iface.initiatorName

    @requires_root
    @pytest.mark.root
    def test_connect(self, fake_iscsi, fake_connection, dm_device, caplog):
        connections = fake_connection()

        conn, status = storageServer.connect(
            sd.ISCSI_DOMAIN, connections, [dm_device])[0]

        assert status == 0
        assert len(fake_iscsi.connections) == len(connections)
        iscsi_portal = fake_iscsi.connections[0].portal
        assert iscsi_portal.hostname == connections[0]["connection"]
        assert iscsi_portal.port == int(connections[0]["port"])
        assert conn.id == connections[0]["id"]
        assert "Timeout reached" not in caplog.text

    def test_failed_connection(self, fake_iscsi, fake_connection, dm_device):
        connections = fake_connection(valid=False)

        _, status = storageServer.connect(
            sd.ISCSI_DOMAIN, connections, [dm_device])[0]

        assert status != 0
        assert len(fake_iscsi.connections) == 0

    @requires_root
    @pytest.mark.root
    def test_invalid_device(self, fake_iscsi, fake_connection, caplog):
        connections = fake_connection()

        conn, status = storageServer.connect(
            sd.ISCSI_DOMAIN, connections, ['dm-invalid'])[0]

        assert status == 0
        assert len(fake_iscsi.connections) == len(connections)
        iscsi_portal = fake_iscsi.connections[0].portal
        assert iscsi_portal.hostname == connections[0]["connection"]
        assert iscsi_portal.port == int(connections[0]["port"])
        assert conn.id == connections[0]["id"]
        assert "Timeout reached for dm-invalid device" in caplog.text
