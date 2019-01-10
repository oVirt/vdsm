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

import pytest

from vdsm.common import constants
from vdsm.storage import lvm


def test_build_filter():
    devices = ("/dev/mapper/a", "/dev/mapper/b")
    expected = 'filter=["a|^/dev/mapper/a$|^/dev/mapper/b$|", "r|.*|"]'
    assert expected == lvm._buildFilter(devices)


def test_build_filter_quoting():
    devices = (r"\x20\x24\x7c\x22\x28",)
    expected = r'filter=["a|^\\x20\\x24\\x7c\\x22\\x28$|", "r|.*|"]'
    assert expected == lvm._buildFilter(devices)


def test_build_filter_no_devices():
    # This special case is possible on a system without any multipath device.
    # LVM commands will succeed, returning no info.
    expected = 'filter=["r|.*|"]'
    assert expected == lvm._buildFilter(())


def test_build_filter_with_user_devices(monkeypatch):
    monkeypatch.setattr(lvm, "USER_DEV_LIST", ("/dev/b",))
    expected_filter = 'filter=["a|^/dev/a$|^/dev/b$|^/dev/c$|", "r|.*|"]'
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
        'filter=["a|^/dev/a$|^/dev/b$|", "r|.*|"]')


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
        lvm._buildConfig(lvm._buildFilter(fake_devices)),
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
    assert cmd[3] == lvm._buildConfig(lvm._buildFilter(fake_devices))
