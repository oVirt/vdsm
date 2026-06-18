# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

import json
import os

import pytest

from vdsm.common import cmdutils
from vdsm.storage import nvme

_MODULE = "vdsm.storage.nvme"


class TestNvmeConnect:

    def test_connect_basic(self, monkeypatch):
        args = []

        def fake_run(cmd):
            args.append(cmd)
            return b""

        monkeypatch.setattr(_MODULE + ".commands", "run", fake_run)
        nvme.connect("nqn.test", "192.168.1.100")
        assert args[0] == [
            nvme._NVME.cmd, "connect",
            "-n", "nqn.test",
            "-t", "tcp",
            "-a", "192.168.1.100",
            "-s", "4420",
        ]

    def test_connect_with_host_nqn(self, monkeypatch):
        args = []

        def fake_run(cmd):
            args.append(cmd)
            return b""

        monkeypatch.setattr(_MODULE + ".commands", "run", fake_run)
        nvme.connect("nqn.test", "192.168.1.100",
                     host_nqn="nqn.host")
        assert args[0] == [
            nvme._NVME.cmd, "connect",
            "-n", "nqn.test",
            "-t", "tcp",
            "-a", "192.168.1.100",
            "-s", "4420",
            "-w", "nqn.host",
        ]

    def test_connect_with_dhchap_key(self, monkeypatch):
        args = []

        def fake_run(cmd):
            args.append(cmd)
            return b""

        monkeypatch.setattr(_MODULE + ".commands", "run", fake_run)
        nvme.connect("nqn.test", "192.168.1.100",
                     dhchap_key="secret123")
        assert "-k" in args[0]
        assert "secret123" in args[0]

    def test_connect_non_default_port(self, monkeypatch):
        args = []

        def fake_run(cmd):
            args.append(cmd)
            return b""

        monkeypatch.setattr(_MODULE + ".commands", "run", fake_run)
        nvme.connect("nqn.test", "192.168.1.100", trsvcid="8000")
        assert "-s" in args[0]
        assert "8000" in args[0]

    def test_connect_failure_raises_exception(self, monkeypatch):
        def fake_run(cmd):
            raise cmdutils.Error(["nvme"], 1, b"", b"error")

        monkeypatch.setattr(_MODULE + ".commands", "run", fake_run)
        with pytest.raises(nvme.NvmeConnectionError):
            nvme.connect("nqn.test", "192.168.1.100")

    def test_connect_auth_failure_raises_auth_exception(self, monkeypatch):
        def fake_run(cmd):
            raise cmdutils.Error(
                ["nvme"], 1, b"", b"authentication error")

        monkeypatch.setattr(_MODULE + ".commands", "run", fake_run)
        with pytest.raises(nvme.NvmeAuthenticationError):
            nvme.connect("nqn.test", "192.168.1.100")


class TestNvmeDisconnect:

    def test_disconnect_basic(self, monkeypatch):
        args = []

        def fake_run(cmd):
            args.append(cmd)
            return b""

        monkeypatch.setattr(_MODULE + ".commands", "run", fake_run)
        nvme.disconnect("nqn.test")
        assert args[0] == [
            nvme._NVME.cmd, "disconnect", "-n", "nqn.test",
        ]

    def test_disconnect_failure_raises_exception(self, monkeypatch):
        def fake_run(cmd):
            raise cmdutils.Error(["nvme"], 1, b"", b"error")

        monkeypatch.setattr(_MODULE + ".commands", "run", fake_run)
        with pytest.raises(nvme.NvmeDisconnectionError):
            nvme.disconnect("nqn.test")


class TestNvmeList:

    def test_list_controllers(self, monkeypatch):
        fake_data = {
            "Devices": [
                {
                    "DevicePath": "/dev/nvme0",
                    "Firmware": "1.0",
                    "ModelNumber": "Test NVMe Controller",
                    "SerialNumber": "SN123",
                    "UsedBytes": 0,
                    "NamespaceSize": 1073741824,
                    "PhysicalSize": 1073741824,
                    "SectorSize": 512,
                }
            ]
        }

        def fake_run(cmd):
            return json.dumps(fake_data).encode("utf-8")

        monkeypatch.setattr(_MODULE + ".commands", "run", fake_run)
        result = nvme.list_controllers()
        assert len(result) == 1
        assert result[0]["device"] == "/dev/nvme0"
        assert result[0]["model"] == "Test NVMe Controller"
        assert result[0]["serial"] == "SN123"

    def test_list_controllers_no_devices(self, monkeypatch):
        def fake_run(cmd):
            return json.dumps({"Devices": []}).encode("utf-8")

        monkeypatch.setattr(_MODULE + ".commands", "run", fake_run)
        result = nvme.list_controllers()
        assert result == []


class TestNvmeHostNqn:

    def test_get_host_nqn(self, tmpdir):
        hostnqn_path = os.path.join(tmpdir, "hostnqn")
        with open(hostnqn_path, "w") as f:
            f.write("nqn.2014-08.org.nvmexpress:uuid:test\n")

        monkeypatch = pytest.MonkeyPatch()

        def _fake_open(*a, **kw):
            path = hostnqn_path if a[0] == "/etc/nvme/hostnqn" else a[0]
            return open(path, *a[1:], **kw)

        monkeypatch.setattr("builtins.open", _fake_open)
        monkeypatch.delattr(_MODULE, "open")
        result = nvme.get_host_nqn()
        monkeypatch.undo()
        assert result == "nqn.2014-08.org.nvmexpress:uuid:test"

    def test_get_host_nqn_not_found(self):
        result = nvme.get_host_nqn()
        assert result is None


class TestNvmeConnectedState:

    def test_is_connected_false(self, monkeypatch):
        monkeypatch.setattr(
            _MODULE + ".get_connected_nqns",
            lambda: [])
        assert not nvme.is_connected("nqn.test")

    def test_is_connected_true(self, monkeypatch):
        monkeypatch.setattr(
            _MODULE + ".get_connected_nqns",
            lambda: [("nqn.test", "192.168.1.100", "4420", "tcp")])
        assert nvme.is_connected("nqn.test")

    def test_get_connected_nqns_empty(self, monkeypatch):
        monkeypatch.setattr(_MODULE + ".glob", "glob", lambda p: [])
        result = nvme.get_connected_nqns()
        assert result == []


class TestNvmeDeviceHelpers:

    def test_dev_is_nvme_true(self, monkeypatch):
        fake_path = ("/sys/devices/pci0000:00/0000:00:01.0/"
                     "nvme/nvme0/nvme0n1")
        monkeypatch.setattr(
            _MODULE + ".os.path.realpath",
            lambda p: fake_path)
        monkeypatch.setattr(
            _MODULE + ".os.path.exists",
            lambda p: True)
        assert nvme.dev_is_nvme("nvme0n1")

    def test_dev_is_nvme_false(self, monkeypatch):
        fake_path = ("/sys/devices/pci0000:00/0000:00:01.0/"
                     "host0/target0:0:0/0:0:0:0")
        monkeypatch.setattr(
            _MODULE + ".os.path.realpath",
            lambda p: fake_path)
        monkeypatch.setattr(
            _MODULE + ".os.path.exists",
            lambda p: True)
        assert not nvme.dev_is_nvme("sda")

    def test_parse_address_full(self):
        addr = "traddr=192.168.1.100,trsvcid=4420"
        traddr, trsvcid = nvme._parse_address(addr)
        assert traddr == "192.168.1.100"
        assert trsvcid == "4420"

    def test_parse_address_bare(self):
        traddr, trsvcid = nvme._parse_address("192.168.1.100")
        assert traddr == "192.168.1.100"

    def test_parse_address_none(self):
        traddr, trsvcid = nvme._parse_address(None)
        assert traddr is None
        assert trsvcid is None

    def test_device_to_subsys(self):
        result = nvme._device_to_subsys("nvme0n1")
        expected = os.path.join(nvme.SYS_NVME_SUBSYS, "nvme-subsys0")
        assert result == expected

    def test_device_to_controller(self):
        result = nvme._device_to_controller("nvme0n1")
        expected = os.path.join(nvme.SYS_NVME, "nvme0")
        assert result == expected


class TestNvmeNativeMultipath:

    def test_is_native_multipath_enabled_false(self, monkeypatch):
        monkeypatch.setattr(
            "builtins.open",
            lambda *a, **kw: _raise_on_open(a[0]))
        monkeypatch.setattr("os.path.exists", lambda p: False)
        assert not nvme.is_native_multipath_enabled()

    def test_is_native_multipath_disabled(self, monkeypatch):
        from unittest.mock import mock_open
        m = mock_open(read_data="N\n")
        monkeypatch.setattr(
            "builtins.open", m)
        monkeypatch.setattr(
            "os.path.exists",
            lambda p: p == "/sys/module/nvme_core/parameters/multipath")
        assert not nvme.is_native_multipath_enabled()

    def test_device_is_nvmeof_tcp(self, monkeypatch):
        monkeypatch.setattr(
            _MODULE + "._read_sysfs_attr",
            lambda p: "tcp" if p.endswith("/transport") else None)
        assert nvme._device_is_nvmeof("nvme0n1")

    def test_device_is_nvmeof_pci(self, monkeypatch):
        monkeypatch.setattr(
            _MODULE + "._read_sysfs_attr",
            lambda p: "pci" if p.endswith("/transport") else None)
        assert not nvme._device_is_nvmeof("nvme0n1")

    def test_namespace_held_by_dm_true(self, monkeypatch):
        def fake_listdir(path):
            if path.endswith("/holders"):
                return ["dm-0"]
            return []
        monkeypatch.setattr("os.listdir", fake_listdir)
        monkeypatch.setattr("os.path.isdir", lambda p: True)
        assert nvme._namespace_held_by_dm("nvme0n1")

    def test_namespace_held_by_dm_false(self, monkeypatch):
        def fake_listdir(path):
            if path.endswith("/holders"):
                return []
            raise OSError()
        monkeypatch.setattr("os.listdir", fake_listdir)
        monkeypatch.setattr("os.path.isdir", lambda p: True)
        assert not nvme._namespace_held_by_dm("nvme0n1")

    def test_get_subsystem_controllers(self, monkeypatch):
        fake_subsys = "/sys/class/nvme-subsystem/nvme-subsys0"
        monkeypatch.setattr(
            _MODULE + "._device_to_subsys",
            lambda dev: fake_subsys)

        def fake_listdir(path):
            if path == fake_subsys:
                return ["nvme0", "nvme1", "nvme-subsys0"]
            return []

        def fake_isdir(path):
            return True

        def fake_islink(path):
            return "nvme0" in path or "nvme1" in path

        monkeypatch.setattr("os.listdir", fake_listdir)
        monkeypatch.setattr("os.path.isdir", fake_isdir)
        monkeypatch.setattr("os.path.islink", fake_islink)

        attrs = {
            "/sys/class/nvme-subsystem/nvme-subsys0/nvme0/address":
                "traddr=192.168.1.100,trsvcid=4420",
            "/sys/class/nvme-subsystem/nvme-subsys0/nvme0/transport":
                "tcp",
            "/sys/class/nvme-subsystem/nvme-subsys0/nvme1/address":
                "traddr=192.168.1.101,trsvcid=4420",
            "/sys/class/nvme-subsystem/nvme-subsys0/nvme1/transport":
                "tcp",
        }

        monkeypatch.setattr(
            _MODULE + "._read_sysfs_attr",
            lambda p: attrs.get(p))

        controllers = nvme.get_subsystem_controllers("nvme0n1")
        assert len(controllers) == 2
        assert controllers[0]["ctrl"] == "nvme0"
        assert controllers[0]["traddr"] == "192.168.1.100"
        assert controllers[1]["ctrl"] == "nvme1"
        assert controllers[1]["traddr"] == "192.168.1.101"

    def test_get_native_namespace_details(self, monkeypatch):
        attrs = {
            "/sys/class/nvme/nvme0/serial": "SN12345",
            "/sys/class/nvme/nvme0/model": "Test NVMe Drive",
            "/sys/class/nvme/nvme0/firmware_rev": "1.0",
        }
        monkeypatch.setattr(
            _MODULE + "._read_sysfs_attr",
            lambda p: attrs.get(p))
        result = nvme.get_native_namespace_details("nvme0n1")
        assert result["serial"] == "SN12345"
        assert result["model"] == "Test NVMe Drive"
        assert result["fwrev"] == "1.0"
        assert result["vendor"] == "NVMe"

    def test_get_native_namespaces_empty(self, monkeypatch):
        monkeypatch.setattr("os.listdir", lambda p: [])
        result = nvme.get_native_namespaces()
        assert result == []

    def test_get_native_namespaces_skips_local(self, monkeypatch):
        def fake_listdir(path):
            if path == "/sys/block":
                return ["nvme0n1", "sda", "dm-0"]
            if path.endswith("/holders"):
                return []
            return ["nvme0"]

        monkeypatch.setattr("os.listdir", fake_listdir)
        monkeypatch.setattr("os.path.isdir", lambda p: True)
        monkeypatch.setattr(
            _MODULE + "._namespace_held_by_dm", lambda d: False)
        monkeypatch.setattr(
            _MODULE + "._device_is_nvmeof", lambda d: False)
        result = nvme.get_native_namespaces()
        assert result == []


def _raise_on_open(path):
    raise OSError("No such file")
