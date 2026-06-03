# SPDX-FileCopyrightText: oVirt Developers
# SPDX-License-Identifier: GPL-2.0-or-later

import os
from unittest import mock

import pytest

from vdsm.storage import lvmdevices


@pytest.fixture
def fake_devices_file(tmpdir, monkeypatch):
    """Point _get_devices_file_path() at a tmpdir file."""
    devices_dir = os.path.join(str(tmpdir), "lvm", "devices")
    devices_file = os.path.join(devices_dir, "system.devices")
    monkeypatch.setattr(
        lvmdevices, "_get_devices_file_path", lambda: devices_file
    )
    return devices_file


@pytest.fixture
def stub_imports(monkeypatch):
    """Capture VGs passed to _run_vgimportdevices."""
    imported = []
    monkeypatch.setattr(
        lvmdevices, "_run_vgimportdevices", lambda vg: imported.append(vg)
    )
    return imported


def _stub_visible_vgs(monkeypatch, vgs):
    monkeypatch.setattr(lvmdevices, "_list_all_visible_vgs", lambda: list(vgs))


def test_create_no_vgs_anywhere_writes_empty_header(
    fake_devices_file, stub_imports, monkeypatch
):
    # No VGs anywhere: a header file must still exist so lvm does not
    # scan every device under use_devicesfile=1.
    _stub_visible_vgs(monkeypatch, [])

    lvmdevices._create_system_devices(set())

    assert stub_imports == []
    assert os.path.exists(fake_devices_file)
    with open(fake_devices_file) as f:
        header = f.read()
    assert header.startswith("# Created by Vdsm")


def test_create_imports_externally_visible_vg_when_file_missing(
    fake_devices_file, stub_imports, monkeypatch
):
    # External VG on disk but not in the missing file must be imported,
    # not dropped.
    _stub_visible_vgs(monkeypatch, ["external_vg"])

    lvmdevices._create_system_devices(set())

    assert stub_imports == ["external_vg"]


def test_create_combines_external_and_vdsm_vgs(
    fake_devices_file, stub_imports, monkeypatch
):
    # A visible external VG plus a VG from the vgs argument.
    _stub_visible_vgs(monkeypatch, ["external_vg"])

    lvmdevices._create_system_devices({"vg_root"})

    assert stub_imports == ["external_vg", "vg_root"]


def test_create_dedups_vg_in_both_lists(
    fake_devices_file, stub_imports, monkeypatch
):
    # A VG in both lists is imported once.
    _stub_visible_vgs(monkeypatch, ["external_vg"])

    lvmdevices._create_system_devices({"external_vg", "vg_root"})

    assert stub_imports == ["external_vg", "vg_root"]


def test_create_preserves_populated_existing_file(
    fake_devices_file, stub_imports, monkeypatch
):
    # A file that already records devices is authoritative: do not
    # re-list, only add the VGs from the vgs argument.
    os.makedirs(os.path.dirname(fake_devices_file), exist_ok=True)
    with open(fake_devices_file, "w") as f:
        f.write(
            "# pre-existing populated file\n"
            "VERSION=1.1.2\n"
            "IDTYPE=devname IDNAME=/dev/vdb DEVNAME=/dev/vdb PVID=...\n"
        )
    listing = mock.Mock(return_value=[])
    monkeypatch.setattr(lvmdevices, "_list_all_visible_vgs", listing)

    lvmdevices._create_system_devices({"vg_root"})

    listing.assert_not_called()
    assert stub_imports == ["vg_root"]
    with open(fake_devices_file) as f:
        assert "IDTYPE=devname IDNAME=/dev/vdb" in f.read()


def test_create_treats_header_only_file_as_unpopulated(
    fake_devices_file, stub_imports, monkeypatch
):
    # A header-only file has size > 0 but records no devices, so it is
    # treated as unpopulated.
    os.makedirs(os.path.dirname(fake_devices_file), exist_ok=True)
    with open(fake_devices_file, "w") as f:
        f.write("# Created by Vdsm pid 1234 at Mon Jun 23 12:00:00 2026\n")
    _stub_visible_vgs(monkeypatch, ["external_vg"])

    lvmdevices._create_system_devices({"vg_root"})

    assert stub_imports == ["external_vg", "vg_root"]


def test_devices_file_has_entries_only_counts_device_entries(tmpdir):
    # Only IDTYPE= device lines count. Comments and bare lvm metadata
    # (VERSION=, written even when the file has no devices) do not.
    devices_file = os.path.join(str(tmpdir), "system.devices")
    with open(devices_file, "w") as f:
        f.write("# LVM uses devices listed in this file.\n")
        f.write("VERSION=1.1.2\n")
    assert lvmdevices._devices_file_has_entries(devices_file) is False

    with open(devices_file, "a") as f:
        f.write("IDTYPE=devname IDNAME=/dev/vdb DEVNAME=/dev/vdb PVID=x\n")
    assert lvmdevices._devices_file_has_entries(devices_file) is True


def test_devices_file_has_entries_missing_file(tmpdir):
    missing = os.path.join(str(tmpdir), "nope", "system.devices")
    assert lvmdevices._devices_file_has_entries(missing) is False


def test_list_all_visible_vgs_filters_ovirt_sd_tag(monkeypatch):
    # VGs tagged RHAT_storage_domain are filtered out, like
    # find_lvm_mounts.
    fake_out = (
        "  external_vg|\n"
        "  0d504179-2606-4e62-87ee-0e5502dc00da|"
        "MDT_TYPE=ISCSI,RHAT_storage_domain\n"
        "  vg_root|\n"
    ).encode("utf-8")

    monkeypatch.setattr(
        lvmdevices.commands, "start", lambda *a, **kw: mock.Mock(returncode=0)
    )
    monkeypatch.setattr(
        lvmdevices.commands, "communicate", lambda p: (fake_out, b"")
    )

    assert lvmdevices._list_all_visible_vgs() == ["external_vg", "vg_root"]
