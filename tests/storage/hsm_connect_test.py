#
# Copyright 2016-2017 Red Hat, Inc.
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
from __future__ import print_function

import pytest

from storage.storagetestlib import FakeStorageDomainCache

from vdsm.storage import hsm
from vdsm.storage import sd
from vdsm.storage import storageServer
from vdsm.storage import task


class FakeConnectHSM(hsm.HSM):
    def __init__(self):
        self.prefetched_domains = {}
        pass

    def _prefetchDomains(self, domType, conObj):
        return self.prefetched_domains


class FakeConnection(object):
    def __init__(self, conInfo):
        self.conInfo = conInfo
        self.connected = False

    @property
    def id(self):
        return self.conInfo.params.id

    def connect(self):
        if self.id.startswith("failing-"):
            raise Exception("Connection failed")
        self.connected = True

    def disconnect(self):
        self.connected = False


class FakeConnectionFactory(object):
    def __init__(self):
        self.connections = {}

    def createConnection(self, conInfo):
        conn = FakeConnection(conInfo)
        self.connections[conn.id] = conn
        return conn


@pytest.fixture
def fake_hsm(monkeypatch):
    """
    Create fake hsm instance for testing connection verbs.

    Monkeypatch the hsm and storageServer to allow testing the flows without
    attempting real connections.
    """
    monkeypatch.setattr(hsm, 'sdCache', FakeStorageDomainCache())
    monkeypatch.setattr(hsm.vars, 'task', task.Task("fake-task-id"))
    monkeypatch.setattr(storageServer, 'ConnectionFactory',
                        FakeConnectionFactory())
    return FakeConnectHSM()


@pytest.mark.parametrize("conn_type,expected_calls", [
    (sd.NFS_DOMAIN, [('invalidateStorage', (), {})]),
    (sd.POSIXFS_DOMAIN, [('invalidateStorage', (), {})]),
    (sd.GLUSTERFS_DOMAIN, [('invalidateStorage', (), {})]),
    (sd.LOCALFS_DOMAIN, [('invalidateStorage', (), {})]),
    (sd.ISCSI_DOMAIN, [('refreshStorage', (), {}),
                       ('invalidateStorage', (), {})]),
    (sd.FCP_DOMAIN, [('refreshStorage', (), {}),
                     ('invalidateStorage', (), {})]),
])
def test_refresh_storage_once(fake_hsm, conn_type, expected_calls):
    connections = [{'id': '1', 'connection': 'test', 'port': '3660'},
                   {'id': '2', 'connection': 'test2', 'port': '3660'},
                   {'id': '3', 'connection': 'test3', 'port': '3660'}]
    fake_hsm.connectStorageServer(conn_type, 'SPUID', connections)

    calls = getattr(hsm.sdCache, "__calls__", [])
    assert calls == expected_calls


@pytest.mark.parametrize("conn_type,connections", [
    (sd.NFS_DOMAIN,
     [{'id': '1', 'connection': '/my_sd', 'protocol_version': '3'}]),
    (sd.ISCSI_DOMAIN,
     [{'id': '2', 'connection': 'test', 'port': '3660'}]),
    (sd.FCP_DOMAIN,
     [{'id': '3', 'connection': 'test'}]),
    (sd.NFS_DOMAIN,
     [{'id': '4', 'connection': '/my_sd', 'protocol_version': '3'},
      {'id': '5', 'connection': '/my_sd2', 'protocol_version': '3'}]),
    (sd.ISCSI_DOMAIN,
     [{'id': '6', 'connection': 'test', 'port': '3660'},
      {'id': '7', 'connection': 'test2', 'port': '3660'}]),
    (sd.FCP_DOMAIN,
     [{'id': '8', 'connection': 'test'},
      {'id': '9', 'connection': 'test2'}]),
])
def test_connect(fake_hsm, conn_type, connections):
    fake_hsm.connectStorageServer(conn_type, 'SPUID', connections)

    sc = storageServer.ConnectionFactory.connections
    for con in connections:
        assert sc[con["id"]].connected


@pytest.mark.parametrize("conn_type", [
    sd.NFS_DOMAIN, sd.ISCSI_DOMAIN
])
def test_failed_connection(fake_hsm, conn_type):
    connections = [
        {'id': 'success-1', 'connection': '/my_sd', 'protocol_version': '3',
         'iqn': None, 'vfsType': None, 'mountOptions': None,
         'nfsVersion': 'AUTO', 'nfsRetrans': None, 'nfsTimeo': None,
         'iface': None, 'netIfaceName': None, 'port': '3660'},
        {'id': 'failing-1', 'connection': '/my_sd2', 'protocol_version': '3',
         'iqn': None, 'vfsType': None, 'mountOptions': None,
         'nfsVersion': 'AUTO', 'nfsRetrans': None, 'nfsTimeo': None,
         'iface': None, 'netIfaceName': None, 'port': '3660'},
        {'id': 'success-2', 'connection': '/my_sd3', 'protocol_version': '3',
         'iqn': None, 'vfsType': None, 'mountOptions': None,
         'nfsVersion': 'AUTO', 'nfsRetrans': None, 'nfsTimeo': None,
         'iface': None, 'netIfaceName': None, 'port': '3660'}
    ]
    result = fake_hsm.connectStorageServer(conn_type, 'SPUID', connections)
    expected = {
        'statuslist':
            [
                {'status': 0, 'id': 'success-1'},
                {'status': 100, 'id': 'failing-1'},
                {'status': 0, 'id': 'success-2'}
            ]
    }
    assert expected == result
    sc = storageServer.ConnectionFactory.connections
    assert sc["success-1"].connected
    assert sc["success-2"].connected
    assert not sc["failing-1"].connected


def test_cache_update(fake_hsm):
    nfs_find_method = fake_hsm._getSDTypeFindMethod(sd.NFS_DOMAIN)
    fake_hsm.prefetched_domains = {'sd-uuid-1': nfs_find_method}
    connections = [
        {'id': '1', 'connection': '/my_sd', 'protocol_version': '3'}
    ]
    assert hsm.sdCache.knownSDs == {}

    fake_hsm.connectStorageServer(sd.NFS_DOMAIN, 'SPUID', connections)

    sc = storageServer.ConnectionFactory.connections
    assert sc['1'].connected
    assert hsm.sdCache.knownSDs['sd-uuid-1'] == nfs_find_method
