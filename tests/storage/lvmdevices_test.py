# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import
from __future__ import division

import os

import pytest

from vdsm.storage import lvmdevices


@pytest.fixture
def fake_devices_file(tmpdir, monkeypatch):
    """
    Redirect _get_devices_file_path() to a tmpdir-rooted location so
    _create_system_devices() exercises real filesystem ops without
    touching the host.
    """
    devices_dir = os.path.join(str(tmpdir), "lvm", "devices")
    devices_file = os.path.join(devices_dir, "system.devices")
    monkeypatch.setattr(
        lvmdevices, "_get_devices_file_path", lambda: devices_file)
    return devices_file


@pytest.fixture
def stub_imports(monkeypatch):
    """
    Capture the VG names passed to _run_vgimportdevices instead of
    invoking the real lvm binary.
    """
    imported = []
    monkeypatch.setattr(
        lvmdevices, "_run_vgimportdevices", lambda vg: imported.append(vg))
    return imported


def _stub_visible_vgs(monkeypatch, vgs):
    monkeypatch.setattr(
        lvmdevices, "_list_all_visible_vgs", lambda: list(vgs))


def test_create_no_vgs_anywhere_writes_empty_header(
        fake_devices_file, stub_imports, monkeypatch):
    # Host has no VGs at all (lvm sees nothing, vdsm has no mounts).
    # PR #324 invariant: file must exist when use_devicesfile=1 flips
    # on, so an empty header is dropped.
    _stub_visible_vgs(monkeypatch, [])

    lvmdevices._create_system_devices(set())

    assert stub_imports == []
    assert os.path.exists(fake_devices_file)
    with open(fake_devices_file) as f:
        header = f.read()
    assert header.startswith("# Created by Vdsm")


def test_create_imports_externally_visible_vg_when_file_missing(
        fake_devices_file, stub_imports, monkeypatch):
    # OL8 / pre-RHEL-9-default scenario: LINSTOR has already created
    # drbdpool while use_devicesfile was off, so the VG is on disk but
    # not in the (missing) file. vdsm runs with no mounted VGs of its
    # own (vgs=set()). Without this patch, vdsm would write an empty
    # header and drbdpool would disappear from lvm's view.
    _stub_visible_vgs(monkeypatch, ["drbdpool"])

    lvmdevices._create_system_devices(set())

    # External VG was imported; no header was written (vgimportdevices
    # creates the file as a side effect of the real call).
    assert stub_imports == ["drbdpool"]


def test_create_combines_external_and_vdsm_vgs(
        fake_devices_file, stub_imports, monkeypatch):
    # Host has drbdpool on disk and vg_root is the OS root VG that
    # find_lvm_mounts will pass via the vgs argument. Both should end
    # up in the file. drbdpool is imported first (from the
    # pre-existing-VG list) before the mount-backing VG.
    _stub_visible_vgs(monkeypatch, ["drbdpool"])

    lvmdevices._create_system_devices({"vg_root"})

    assert stub_imports == ["drbdpool", "vg_root"]


def test_create_preserves_populated_existing_file(
        fake_devices_file, stub_imports, monkeypatch):
    # Re-running config-lvm-filter on a host whose file already has
    # entries (e.g. installed by a previous vdsm run, or by lvm's
    # own auto-import on RHEL 9+ where vgcreate populates the file)
    # must not re-list all VGs -- the existing entries are the
    # source of truth. Only the vgs-argument VGs are added (idempotent).
    os.makedirs(os.path.dirname(fake_devices_file), exist_ok=True)
    with open(fake_devices_file, "w") as f:
        f.write("# pre-existing populated file\n"
                "VERSION=1.1.2\n"
                "IDTYPE=devname IDNAME=/dev/vdb DEVNAME=/dev/vdb PVID=...\n")
    visible = []
    monkeypatch.setattr(
        lvmdevices, "_list_all_visible_vgs",
        lambda: visible.append("called") or [])

    lvmdevices._create_system_devices({"vg_root"})

    # VG-listing path was skipped (existing populated file is authoritative).
    assert visible == []
    assert stub_imports == ["vg_root"]


def test_create_treats_empty_header_only_file_as_unpopulated(
        fake_devices_file, stub_imports, monkeypatch):
    # Edge case: a previous vdsm run wrote just the header. Treat as
    # unpopulated and list externally-visible VGs.
    os.makedirs(os.path.dirname(fake_devices_file), exist_ok=True)
    with open(fake_devices_file, "w"):
        pass  # zero-byte file
    _stub_visible_vgs(monkeypatch, ["drbdpool"])

    lvmdevices._create_system_devices({"vg_root"})

    assert stub_imports == ["drbdpool", "vg_root"]


def test_list_all_visible_vgs_filters_ovirt_sd_tag(monkeypatch):
    # Stale oVirt SD VGs (tag RHAT_storage_domain) must not be
    # imported back into the devices file by the VG-listing path --
    # the engine no longer references them, but sanlock would still
    # try to acquire leases on a VG that's visible to lvm.
    # find_lvm_mounts already skips this tag for the same reason.
    fake_out = (
        "  drbdpool|\n"
        "  0d504179-2606-4e62-87ee-0e5502dc00da|"
        "MDT_TYPE=ISCSI,RHAT_storage_domain\n"
        "  vg_root|\n"
    ).encode("utf-8")

    class FakeProc:
        returncode = 0
    monkeypatch.setattr(
        lvmdevices.commands, "start", lambda *a, **kw: FakeProc())
    monkeypatch.setattr(
        lvmdevices.commands, "communicate", lambda p: (fake_out, b""))

    assert lvmdevices._list_all_visible_vgs() == ["drbdpool", "vg_root"]
