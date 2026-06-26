# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from vdsm.storage import sd
from vdsm.storage import storageServer

# Module paths for monkeypatching
_NVME_MOD = "vdsm.storage.nvme.is_connected"
_UDEV_MOD = "vdsm.common.udevadm.settle"


class TestNvmeofConnectionParameters:

    def test_minimal_params(self):
        params = storageServer.NvmeofConnectionParameters(
            "test-id", "nqn.test", "tcp", "192.168.1.100", "4420",
            None, None)
        assert params.id == "test-id"
        assert params.nqn == "nqn.test"
        assert params.transport == "tcp"
        assert params.traddr == "192.168.1.100"
        assert params.trsvcid == "4420"
        assert params.host_nqn is None
        assert params.dhchap_key is None

    def test_full_params(self):
        params = storageServer.NvmeofConnectionParameters(
            "test-id", "nqn.test", "tcp", "192.168.1.100", "4420",
            "nqn.host", "secret123")
        assert params.host_nqn == "nqn.host"
        assert params.dhchap_key == "secret123"


class TestNvmeofConnection:

    def test_create_connection(self):
        con = storageServer.NvmeofConnection(
            "test-id", "nqn.test", "tcp", "192.168.1.100", "4420")
        assert con.id == "test-id"
        assert con.nqn == "nqn.test"
        assert con.transport == "tcp"
        assert con.traddr == "192.168.1.100"
        assert con.trsvcid == "4420"

    def test_default_transport(self):
        con = storageServer.NvmeofConnection(
            "test-id", "nqn.test", traddr="192.168.1.100")
        assert con.transport == "tcp"
        assert con.trsvcid == "4420"

    def test_explicit_transport_and_port(self):
        con = storageServer.NvmeofConnection(
            "test-id", "nqn.test", transport="rdma",
            traddr="192.168.1.100", trsvcid="8000")
        assert con.transport == "rdma"
        assert con.trsvcid == "8000"

    def test_equality(self):
        con1 = storageServer.NvmeofConnection(
            "id1", "nqn.test", "tcp", "192.168.1.100", "4420")
        con2 = storageServer.NvmeofConnection(
            "id1", "nqn.test", "tcp", "192.168.1.100", "4420")
        assert con1 == con2

    def test_inequality_different_id(self):
        con1 = storageServer.NvmeofConnection(
            "id1", "nqn.test", "tcp", "192.168.1.100", "4420")
        con2 = storageServer.NvmeofConnection(
            "id2", "nqn.test", "tcp", "192.168.1.100", "4420")
        assert con1 != con2

    def test_inequality_different_nqn(self):
        con1 = storageServer.NvmeofConnection(
            "id1", "nqn.test", "tcp", "192.168.1.100", "4420")
        con2 = storageServer.NvmeofConnection(
            "id1", "nqn.other", "tcp", "192.168.1.100", "4420")
        assert con1 != con2

    def test_inequality_different_traddr(self):
        con1 = storageServer.NvmeofConnection(
            "id1", "nqn.test", "tcp", "192.168.1.100", "4420")
        con2 = storageServer.NvmeofConnection(
            "id1", "nqn.test", "tcp", "192.168.1.200", "4420")
        assert con1 != con2

    def test_hash_consistency(self):
        con1 = storageServer.NvmeofConnection(
            "id1", "nqn.test", "tcp", "192.168.1.100", "4420")
        con2 = storageServer.NvmeofConnection(
            "id1", "nqn.test", "tcp", "192.168.1.100", "4420")
        assert hash(con1) == hash(con2)

    def test_hash_different_values(self):
        con1 = storageServer.NvmeofConnection(
            "id1", "nqn.test", "tcp", "192.168.1.100", "4420")
        con2 = storageServer.NvmeofConnection(
            "id2", "nqn.test", "tcp", "192.168.1.100", "4420")
        assert hash(con1) != hash(con2)

    def test_repr(self):
        con = storageServer.NvmeofConnection(
            "test-id", "nqn.test", "tcp", "192.168.1.100", "4420")
        r = repr(con)
        assert "NvmeofConnection" in r
        assert "test-id" in r
        assert "nqn.test" in r
        assert "192.168.1.100" in r

    def test_wrong_type_equality(self):
        con = storageServer.NvmeofConnection(
            "id1", "nqn.test", "tcp", "192.168.1.100", "4420")
        assert not con.__eq__("not-an-nvmeof-connection")

    def test_is_connected_calls_nvme_module(self, monkeypatch):
        called_with = []

        def fake_is_connected(nqn):
            called_with.append(nqn)
            return True

        monkeypatch.setattr(_NVME_MOD, fake_is_connected)
        con = storageServer.NvmeofConnection(
            "id1", "nqn.test", "tcp", "192.168.1.100", "4420")
        assert con.isConnected()
        assert called_with == ["nqn.test"]

    def test_is_connected_false(self, monkeypatch):
        monkeypatch.setattr(_NVME_MOD, lambda nqn: False)
        con = storageServer.NvmeofConnection(
            "id1", "nqn.test", "tcp", "192.168.1.100", "4420")
        assert not con.isConnected()

    def test_connect_calls_nvme_module(self, monkeypatch):
        calls = []

        def fake_is_connected(nqn):
            return False

        def fake_connect(nqn, traddr, trsvcid, transport,
                         host_nqn, dhchap_key):
            calls.append((nqn, traddr, trsvcid, transport,
                          host_nqn, dhchap_key))

        monkeypatch.setattr(_NVME_MOD, fake_is_connected)
        monkeypatch.setattr("vdsm.storage.nvme.connect", fake_connect)
        monkeypatch.setattr(_UDEV_MOD, lambda t: None)

        con = storageServer.NvmeofConnection(
            "id1", "nqn.test", "tcp", "192.168.1.100", "4420")
        con.connect()
        assert len(calls) == 1
        assert calls[0][0] == "nqn.test"
        assert calls[0][1] == "192.168.1.100"

    def test_connect_already_connected_skips(self, monkeypatch):
        calls = []

        def fake_is_connected(nqn):
            return True

        def fake_connect(*a, **kw):
            calls.append(a)

        monkeypatch.setattr(_NVME_MOD, fake_is_connected)
        monkeypatch.setattr("vdsm.storage.nvme.connect", fake_connect)

        con = storageServer.NvmeofConnection(
            "id1", "nqn.test", "tcp", "192.168.1.100", "4420")
        con.connect()
        assert len(calls) == 0

    def test_disconnect_calls_nvme_module(self, monkeypatch):
        calls = []

        def fake_is_connected(nqn):
            return True

        def fake_disconnect(nqn):
            calls.append(nqn)

        monkeypatch.setattr(_NVME_MOD, fake_is_connected)
        monkeypatch.setattr("vdsm.storage.nvme.disconnect", fake_disconnect)

        con = storageServer.NvmeofConnection(
            "id1", "nqn.test", "tcp", "192.168.1.100", "4420")
        con.disconnect()
        assert calls == ["nqn.test"]

    def test_disconnect_not_connected_skips(self, monkeypatch):
        calls = []

        def fake_is_connected(nqn):
            return False

        def fake_disconnect(nqn):
            calls.append(nqn)

        monkeypatch.setattr(_NVME_MOD, fake_is_connected)
        monkeypatch.setattr("vdsm.storage.nvme.disconnect", fake_disconnect)

        con = storageServer.NvmeofConnection(
            "id1", "nqn.test", "tcp", "192.168.1.100", "4420")
        con.disconnect()
        assert len(calls) == 0


class TestNvmeofConnectionFactory:

    def test_type_registered(self):
        types = storageServer.ConnectionFactory.registeredConnectionTypes
        assert "nvmeof" in types
        assert types["nvmeof"] == storageServer.NvmeofConnection

    def test_create_connection(self):
        params = storageServer.NvmeofConnectionParameters(
            "test-id", "nqn.test", "tcp", "192.168.1.100", "4420",
            None, None)
        con_info = storageServer.ConnectionInfo("nvmeof", params)
        con = storageServer.ConnectionFactory.createConnection(con_info)
        assert isinstance(con, storageServer.NvmeofConnection)
        assert con.id == "test-id"


class TestNvmeofConnectionDict2ConnectionInfo:

    def test_minimal_dict(self, monkeypatch):
        con_dict = {
            "id": "test-id",
            "nqn": "nqn.test",
            "connection": "192.168.1.100",
        }
        con_info = storageServer._connectionDict2ConnectionInfo(
            sd.NVMEOF_DOMAIN, con_dict)
        assert con_info.type == "nvmeof"
        assert con_info.params.id == "test-id"
        assert con_info.params.nqn == "nqn.test"
        assert con_info.params.traddr == "192.168.1.100"
        assert con_info.params.transport == "tcp"
        assert con_info.params.trsvcid == "4420"
        assert con_info.params.host_nqn is None
        assert con_info.params.dhchap_key is None

    def test_full_dict(self, monkeypatch):
        con_dict = {
            "id": "test-id",
            "nqn": "nqn.test",
            "connection": "192.168.1.100",
            "transport": "rdma",
            "port": "8000",
            "host_nqn": "nqn.host",
            "dhchap_key": "secret123",
        }
        con_info = storageServer._connectionDict2ConnectionInfo(
            sd.NVMEOF_DOMAIN, con_dict)
        assert con_info.type == "nvmeof"
        assert con_info.params.transport == "rdma"
        assert con_info.params.trsvcid == "8000"
        assert con_info.params.host_nqn == "nqn.host"
        assert con_info.params.dhchap_key == "secret123"


class TestNvmeofDomainType:

    def test_nvmeof_domain_value(self):
        assert sd.NVMEOF_DOMAIN == 12

    def test_nvmeof_in_block_domain_types(self):
        assert sd.NVMEOF_DOMAIN in sd.BLOCK_DOMAIN_TYPES

    def test_nvmeof_in_domain_types_dict(self):
        assert sd.NVMEOF_DOMAIN in sd.DOMAIN_TYPES
        assert sd.DOMAIN_TYPES[sd.NVMEOF_DOMAIN] == 'NVMEOF'

    def test_nvmeof_in_con_type_map(self):
        assert sd.NVMEOF_DOMAIN in storageServer.CON_TYPE_ID_2_CON_TYPE
        assert (storageServer.CON_TYPE_ID_2_CON_TYPE[sd.NVMEOF_DOMAIN]
                == 'nvmeof')
