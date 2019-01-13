#
# Copyright 2012-2019 Red Hat, Inc.
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
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA
# 02110-1301  USA
#
# Refer to the README and COPYING files for full details of the license
#

from __future__ import absolute_import
from __future__ import division

import os
import uuid
import re

from contextlib import closing

import six
import pytest

from vdsm.common import constants
from vdsm.storage import exception as se
from vdsm.storage import lvm
from vdsm.storage import multipath

from . import tmpstorage


def on_fedora(version):
    with open("/etc/redhat-release") as f:
        line = f.readline()
    return re.match(r"Fedora release %s" % version, line)


requires_root = pytest.mark.skipif(
    os.geteuid() != 0, reason="requires root")

xfail_python3 = pytest.mark.xfail(
    six.PY3, reason="needs porting to python 3")

skipif_fedora_29 = pytest.mark.skipif(
    on_fedora(29),
    reason="lvm get stuck on Fedora 29")


def test_build_filter():
    devices = ("/dev/mapper/a", "/dev/mapper/b")
    expected = '["a|^/dev/mapper/a$|^/dev/mapper/b$|", "r|.*|"]'
    assert expected == lvm._buildFilter(devices)


def test_build_filter_quoting():
    devices = (r"\x20\x24\x7c\x22\x28",)
    expected = r'["a|^\\x20\\x24\\x7c\\x22\\x28$|", "r|.*|"]'
    assert expected == lvm._buildFilter(devices)


def test_build_filter_no_devices():
    # This special case is possible on a system without any multipath device.
    # LVM commands will succeed, returning no info.
    expected = '["r|.*|"]'
    assert expected == lvm._buildFilter(())


def test_build_filter_with_user_devices(monkeypatch):
    monkeypatch.setattr(lvm, "USER_DEV_LIST", ("/dev/b",))
    expected_filter = '["a|^/dev/a$|^/dev/b$|^/dev/c$|", "r|.*|"]'
    assert expected_filter == lvm._buildFilter(("/dev/a", "/dev/c"))


def test_build_config():
    expected = (
        'devices { '
        ' preferred_names=["^/dev/mapper/"] '
        ' ignore_suspended_devices=1 '
        ' write_cache_state=0 '
        ' disable_after_error_count=3 '
        ' filter=["a|^/dev/a$|^/dev/b$|", "r|.*|"] '
        '} '
        'global { '
        ' locking_type=1 '
        ' prioritise_write_locks=1 '
        ' wait_for_locks=1 '
        ' use_lvmetad=0 '
        '} '
        'backup { '
        ' retain_min=50 '
        ' retain_days=0 '
        '}'
    )
    assert expected == lvm._buildConfig(
        dev_filter='["a|^/dev/a$|^/dev/b$|", "r|.*|"]',
        locking_type="1")


@pytest.fixture
def fake_devices(monkeypatch):
    devices = ["/dev/mapper/a", "/dev/mapper/b"]

    # Initial devices for LVMCache tests.
    monkeypatch.setattr(
        lvm.multipath,
        "getMPDevNamesIter",
        lambda: tuple(devices))

    return devices


def test_build_command_long_filter(fake_devices):
    # If the devices are not specified, include all devices reported by
    # multipath.
    lc = lvm.LVMCache()
    cmd = lc._addExtraCfg(["lvs", "-o", "+tags"])

    assert cmd == [
        constants.EXT_LVM,
        "lvs",
        "--config",
        lvm._buildConfig(
            dev_filter=lvm._buildFilter(fake_devices),
            locking_type="1"),
        "-o", "+tags",
    ]


def test_rebuild_filter_after_invaliation(fake_devices):
    # Check that adding a device and invalidating the filter rebuilds the
    # config with the correct filter.
    lc = lvm.LVMCache()
    lc._addExtraCfg(["lvs"])

    fake_devices.append("/dev/mapper/c")
    lc.invalidateFilter()

    cmd = lc._addExtraCfg(["lvs"])
    assert cmd[3] == lvm._buildConfig(
        dev_filter=lvm._buildFilter(fake_devices),
        locking_type="1")


@pytest.fixture
def temp_storage(monkeypatch, tmpdir):
    storage = tmpstorage.TemporaryStorage(str(tmpdir))

    # Get devices from our temporary storage instead of multipath.
    monkeypatch.setattr(multipath, "getMPDevNamesIter", storage.devices)

    with closing(storage):
        # Don't let other test break us...
        lvm.invalidateCache()
        try:
            yield storage
        finally:
            # and don't break other tests.
            lvm.invalidateCache()


@requires_root
@xfail_python3
@skipif_fedora_29
@pytest.mark.root
def test_vg_create_remove(temp_storage):
    dev_size = 20 * 1024**3
    dev = temp_storage.create_device(dev_size)
    vg_name = str(uuid.uuid4())
    lvm.createVG(vg_name, [dev], "initial-tag", 128)

    vg = lvm.getVG(vg_name)
    assert vg.name == vg_name
    assert vg.pv_name == (dev,)
    assert vg.tags == ("initial-tag",)
    assert int(vg.extent_size) == 128 * 1024**2

    pv = lvm.getPV(dev)
    assert pv.name == dev
    assert pv.vg_name == vg_name
    assert int(pv.dev_size) == dev_size
    assert int(pv.mda_count) == 2
    assert int(pv.mda_used_count) == 2

    lvm.removeVG(vg_name)

    # We remove the VG
    with pytest.raises(se.VolumeGroupDoesNotExist):
        lvm.getVG(vg_name)

    # But keep the PVs, not sure why.
    pv = lvm.getPV(dev)
    assert pv.name == dev
    assert pv.vg_name == ""


@requires_root
@xfail_python3
@skipif_fedora_29
@pytest.mark.root
def test_lv_create_remove(temp_storage):
    dev_size = 20 * 1024**3
    dev = temp_storage.create_device(dev_size)
    vg_name = str(uuid.uuid4())
    lv_name = str(uuid.uuid4())
    lvm.createVG(vg_name, [dev], "initial-tag", 128)
    lvm.createLV(vg_name, lv_name, 1024)

    lv = lvm.getLV(vg_name, lv_name)
    assert lv.name == lv_name
    assert lv.vg_name == vg_name
    assert int(lv.size) == 1024**3
    assert lv.tags == ()
    assert lv.writeable
    assert not lv.opened
    assert lv.active
    assert lv.devices == "%s(0)" % dev

    lvm.removeLVs(vg_name, [lv_name])
    with pytest.raises(se.LogicalVolumeDoesNotExistError):
        lvm.getLV(vg_name, lv_name)
