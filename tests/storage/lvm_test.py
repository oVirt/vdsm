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
import time
import uuid

import pytest

from vdsm.common import commands
from vdsm.common import concurrent
from vdsm.common import constants
from vdsm.common.units import MiB, GiB

from vdsm.storage import constants as sc
from vdsm.storage import exception as se
from vdsm.storage import fsutils
from vdsm.storage import lvm

import testing

from . marks import requires_root


EXPECTED_CFG_DEVICES = (
    'devices { '
    ' preferred_names=["^/dev/mapper/"] '
    ' ignore_suspended_devices=1 '
    ' write_cache_state=0 '
    ' disable_after_error_count=3   '
    ' hints="none" '
    ' obtain_device_list_from_udev=0 '
    '} '
    'global { '
    ' prioritise_write_locks=1 '
    ' wait_for_locks=1 '
    ' use_lvmpolld=1 '
    '} '
    'backup { '
    ' retain_min=50 '
    ' retain_days=0 '
    '}'
)


@pytest.fixture
def use_filter(monkeypatch):
    monkeypatch.setattr(lvm, "USE_DEVICES", False)


@pytest.fixture
def use_devices(monkeypatch):
    monkeypatch.setattr(lvm, "USE_DEVICES", True)


# TODO: replace the filter tests with cmd tests.


def test_build_filter():
    devices = ("/dev/mapper/a", "/dev/mapper/b")
    expected = '["a|^/dev/mapper/a$|^/dev/mapper/b$|", "r|.*|"]'
    assert expected == lvm._buildFilter(lvm._prepare_device_set(devices))


def test_build_filter_quoting():
    devices = (r"\x20\x24\x7c\x22\x28",)
    expected = r'["a|^\\x20\\x24\\x7c\\x22\\x28$|", "r|.*|"]'
    assert expected == lvm._buildFilter(lvm._prepare_device_set(devices))


def test_build_filter_no_devices():
    # This special case is possible on a system without any multipath device.
    # LVM commands will succeed, returning no info.
    expected = '["r|.*|"]'
    assert expected == lvm._buildFilter(())


def test_build_filter_with_user_devices(monkeypatch):
    monkeypatch.setattr(lvm, "USER_DEV_LIST", ("/dev/b",))
    expected = '["a|^/dev/a$|^/dev/b$|^/dev/c$|", "r|.*|"]'
    actual = lvm._buildFilter(lvm._prepare_device_set(("/dev/a", "/dev/c")))
    assert expected == actual


def test_build_config_with_filter(fake_devices, use_filter):
    fake_runner = FakeRunner()
    lc = lvm.LVMCache(fake_runner)
    lc.run_command(["lvs"])
    cmd = fake_runner.calls[0]

    expected = (
        'devices { '
        ' preferred_names=["^/dev/mapper/"] '
        ' ignore_suspended_devices=1 '
        ' write_cache_state=0 '
        ' disable_after_error_count=3 '
        ' filter=["a|^/dev/mapper/a$|^/dev/mapper/b$|", "r|.*|"] '
        ' hints="none" '
        ' obtain_device_list_from_udev=0 '
        '} '
        'global { '
        ' prioritise_write_locks=1 '
        ' wait_for_locks=1 '
        ' use_lvmpolld=1 '
        '} '
        'backup { '
        ' retain_min=50 '
        ' retain_days=0 '
        '}'
    )
    assert cmd[3] == expected


def test_build_config_with_devices(fake_devices, use_devices):
    fake_runner = FakeRunner()
    lc = lvm.LVMCache(fake_runner)
    lc.run_command(["lvs"])
    cmd = fake_runner.calls[0]

    assert cmd[5] == EXPECTED_CFG_DEVICES


@pytest.fixture
def fake_devices(monkeypatch):
    devices = ["/dev/mapper/a", "/dev/mapper/b"]

    # Initial devices for LVMCache tests.
    monkeypatch.setattr(
        lvm.multipath,
        "getMPDevNamesIter",
        lambda: tuple(devices))

    return devices


def build_config(devices, use_lvmpolld="1"):
    return lvm._buildConfig(
        dev_filter=lvm._buildFilter(lvm._prepare_device_set(devices)),
        use_lvmpolld=use_lvmpolld)


def test_build_command_long_filter(fake_devices, use_filter):
    # If the devices are not specified, include all devices reported by
    # multipath.
    fake_runner = FakeRunner()
    lc = lvm.LVMCache(fake_runner)
    lc.run_command(["lvs", "-o", "+tags"])
    cmd = fake_runner.calls[0]

    assert cmd == [
        constants.EXT_LVM,
        "lvs",
        "--config",
        build_config(fake_devices),
        "-o", "+tags",
    ]


def test_rebuild_filter_after_invaliation(fake_devices, use_filter):
    # Check that adding a device and invalidating the filter rebuilds the
    # config with the correct filter.
    fake_runner = FakeRunner()
    lc = lvm.LVMCache(fake_runner)
    fake_devices.append("/dev/mapper/c")
    lc.invalidate_devices()

    lc.run_command(["lvs"])
    cmd = fake_runner.calls[0]

    assert cmd[3] == build_config(fake_devices)


class FakeRunner(lvm.LVMRunner):
    """
    Simulate a command failing multiple times before suceeding.

    By default, the first call will succeed, returning the given rc, out, and
    err.

    If retries is set, requires retires extra failing calls to succeed.

    To validate the call, inspect the calls instance variable.
    """

    def __init__(self, rc=0, out=b"", err=b"", retries=0, delay=0.0):
        self.rc = rc
        self.out = out
        self.err = err
        self.retries = retries
        self.delay = delay
        self.calls = []

    def _run_command(self, cmd):
        self.calls.append(cmd)

        if self.delay:
            time.sleep(self.delay)

        if self.retries > 0:
            self.retries -= 1
            return 1, b"", b"fake error"

        return self.rc, self.out, self.err


def test_cmd_success(fake_devices, use_filter):
    fake_runner = FakeRunner()
    lc = lvm.LVMCache(fake_runner)
    lc.run_command(["lvs", "-o", "+tags"])

    assert len(fake_runner.calls) == 1

    cmd = fake_runner.calls[0]
    assert cmd == [
        constants.EXT_LVM,
        "lvs",
        "--config",
        build_config(fake_devices),
        "-o", "+tags",
    ]


@pytest.mark.parametrize("devices, expected", [
    (("/dev/mapper/a",), "/dev/mapper/a"),
    (("/dev/mapper/a", "/dev/mapper/b"), "/dev/mapper/a,/dev/mapper/b"),
    ((r"\x20\x24\x7c\x22\x28",), r"\\x20\\x24\\x7c\\x22\\x28"),
])
def test_cmd_with_devices(use_devices, devices, expected):
    fake_runner = FakeRunner()
    lc = lvm.LVMCache(fake_runner)
    lc.run_command(["lvs", "-o", "+tags"], devices=devices)

    assert len(fake_runner.calls) == 1

    cmd = fake_runner.calls[0]
    expected_cmd = [
        constants.EXT_LVM, "lvs",
        "--devices", expected,
        "--config", EXPECTED_CFG_DEVICES,
        "-o", "+tags",
    ]

    assert cmd == expected_cmd


def test_cmd_error(fake_devices):
    fake_runner = FakeRunner()
    lc = lvm.LVMCache(fake_runner)

    # Require 2 calls to succeed.
    fake_runner.retries = 1

    # Since the filter is correct, the error should be propagated to the caller
    # after the first call.
    with pytest.raises(se.LVMCommandError):
        lc.run_command(["lvs", "-o", "+tags"])

    assert len(fake_runner.calls) == 1


def test_changevgtags_failure_cache(monkeypatch, fake_devices):
    fake_runner = FakeRunner(rc=5)
    lc = lvm.LVMCache(fake_runner)

    monkeypatch.setattr(lvm, "_lvminfo", lc)

    # Create fake devices.
    fake_pv = make_pv(pv_name="/dev/mapper/pv", vg_name="vg")
    fake_vg = make_vg(pvs=[fake_pv.name], vg_name="vg")
    fake_lv = make_lv(lv_name="lv", pvs=[fake_pv.name], vg_name=fake_vg.name)

    # Assign fake PV, VG, LV to cache.
    lc._pvs = {fake_pv.name: fake_pv}
    lc._vgs = {fake_vg.name: fake_vg}
    lc._lvs = {(fake_vg.name, fake_lv.name): fake_lv}

    with pytest.raises(se.LVMCommandError):
        lvm.changeVGTags(fake_vg.name)

    # Verify that vgs are invalidated after changeVGTags() failed.
    assert lvm._lvminfo._vgs[fake_vg.name].is_stale()


def test_changevgtags_success_cache(monkeypatch, fake_devices):
    fake_runner = FakeRunner()
    lc = lvm.LVMCache(fake_runner)

    monkeypatch.setattr(lvm, "_lvminfo", lc)

    # Create fake devices.
    fake_pv = make_pv(pv_name="/dev/mapper/pv", vg_name="vg")
    fake_vg = make_vg(pvs=[fake_pv.name], vg_name="vg")
    fake_lv = make_lv(lv_name="lv", pvs=[fake_pv.name], vg_name=fake_vg.name)

    # Assign fake PV, VG, LV to cache.
    lc._pvs = {fake_pv.name: fake_pv}
    lc._vgs = {fake_vg.name: fake_vg}
    lc._lvs = {(fake_vg.name, fake_lv.name): fake_lv}

    lvm.changeVGTags(fake_vg.name)

    # Verify that vgs are invalidated after changeVGTags() succeeded.
    assert lvm._lvminfo._vgs[fake_vg.name].is_stale()


def test_chkvg_failure_cache(monkeypatch, fake_devices):
    fake_runner = FakeRunner(rc=5)
    lc = lvm.LVMCache(fake_runner)

    monkeypatch.setattr(lvm, "_lvminfo", lc)

    # Create fake devices.
    fake_pv = make_pv(pv_name="/dev/mapper/pv", vg_name="vg")
    fake_vg = make_vg(pvs=[fake_pv.name], vg_name="vg")
    fake_lv = make_lv(lv_name="lv", pvs=[fake_pv.name], vg_name=fake_vg.name)

    # Assign fake PV, VG, LV to cache.
    lc._pvs = {fake_pv.name: fake_pv}
    lc._vgs = {fake_vg.name: fake_vg}
    lc._lvs = {(fake_vg.name, fake_lv.name): fake_lv}

    with pytest.raises(se.LVMCommandError):
        lvm.chkVG(fake_vg.name)

    # Verify that lvs and vgs are invalidated after chkVG() failed.
    assert lvm._lvminfo._lvs[(fake_vg.name, fake_lv.name)].is_stale()
    assert lvm._lvminfo._vgs[fake_vg.name].is_stale()


def test_chkvg_success_cache(monkeypatch, fake_devices):
    fake_runner = FakeRunner()
    lc = lvm.LVMCache(fake_runner)

    monkeypatch.setattr(lvm, "_lvminfo", lc)

    # Create fake devices.
    fake_pv = make_pv(pv_name="/dev/mapper/pv", vg_name="vg")
    fake_vg = make_vg(pvs=[fake_pv.name], vg_name="vg")
    fake_lv = make_lv(lv_name="lv", pvs=[fake_pv.name], vg_name=fake_vg.name)

    # Assign fake PV, VG, LV to cache.
    lc._pvs = {fake_pv.name: fake_pv}
    lc._vgs = {fake_vg.name: fake_vg}
    lc._lvs = {(fake_vg.name, fake_lv.name): fake_lv}

    lvm.chkVG(fake_vg.name)

    # Verify that lvs and vgs are not invalidated after chkVG() succeeded.
    assert not lvm._lvminfo._lvs[(fake_vg.name, fake_lv.name)].is_stale()
    assert not lvm._lvminfo._vgs[fake_vg.name].is_stale()


def test_refreshlvs_failure_cache(monkeypatch, fake_devices):
    fake_runner = FakeRunner(rc=5)
    lc = lvm.LVMCache(fake_runner)

    monkeypatch.setattr(lvm, "_lvminfo", lc)

    # Create fake devices.
    fake_pv = make_pv(pv_name="/dev/mapper/pv", vg_name="vg")
    fake_vg = make_vg(pvs=[fake_pv.name], vg_name="vg")
    fake_lv = make_lv(lv_name="lv", pvs=[fake_pv.name], vg_name=fake_vg.name)

    # Assign fake PV, VG, LV to cache.
    lc._pvs = {fake_pv.name: fake_pv}
    lc._vgs = {fake_vg.name: fake_vg}
    lc._lvs = {(fake_vg.name, fake_lv.name): fake_lv}

    with pytest.raises(se.LVMCommandError):
        lvm.refreshLVs(fake_vg.name, [fake_lv.name])

    # Verify that lvs are invalidated after refreshLVs() failed.
    assert lvm._lvminfo._lvs[(fake_vg.name, fake_lv.name)].is_stale()


def test_refreshlvs_success_cache(monkeypatch, fake_devices):
    fake_runner = FakeRunner()
    lc = lvm.LVMCache(fake_runner)

    monkeypatch.setattr(lvm, "_lvminfo", lc)

    # Create fake devices.
    fake_pv = make_pv(pv_name="/dev/mapper/pv", vg_name="vg")
    fake_vg = make_vg(pvs=[fake_pv.name], vg_name="vg")
    fake_lv = make_lv(lv_name="lv", pvs=[fake_pv.name], vg_name=fake_vg.name)

    # Assign fake PV, VG, LV to cache.
    lc._pvs = {fake_pv.name: fake_pv}
    lc._vgs = {fake_vg.name: fake_vg}
    lc._lvs = {(fake_vg.name, fake_lv.name): fake_lv}

    lvm.refreshLVs(fake_vg.name, [fake_lv.name])

    # Verify that lvs are invalidated after refreshLVs() succeeded.
    assert lvm._lvminfo._lvs[(fake_vg.name, fake_lv.name)].is_stale()


def test_extendlv_failure_cache(monkeypatch, fake_devices):
    fake_runner = FakeRunner(rc=5)
    lc = lvm.LVMCache(fake_runner)

    monkeypatch.setattr(lvm, "_lvminfo", lc)

    # Create fake devices.
    fake_pv = make_pv(pv_name="/dev/mapper/pv", vg_name="vg")
    fake_vg = make_vg(pvs=[fake_pv.name], vg_name="vg")
    fake_lv = make_lv(lv_name="lv", pvs=[fake_pv.name], vg_name=fake_vg.name)

    # Assign fake PV, VG, LV to cache.
    lc._pvs = {fake_pv.name: fake_pv}
    lc._vgs = {fake_vg.name: fake_vg}
    lc._lvs = {(fake_vg.name, fake_lv.name): fake_lv}

    # Do not attempt to use real devices.
    monkeypatch.setattr(lvm, "getLV", lambda x, y: fake_lv)
    monkeypatch.setattr(lvm, "getVG", lambda x: fake_vg)

    with pytest.raises(se.LogicalVolumeExtendError):
        lvm.extendLV(fake_vg.name, fake_lv.name, 100)

    # Verify that lvs and vgs are invalidated after extendLV() failed.
    assert lvm._lvminfo._lvs[(fake_vg.name, fake_lv.name)].is_stale()
    assert lvm._lvminfo._vgs[fake_vg.name].is_stale()


def test_reducelv_failure_cache(monkeypatch, fake_devices):
    fake_runner = FakeRunner(rc=5)
    lc = lvm.LVMCache(fake_runner)

    monkeypatch.setattr(lvm, "_lvminfo", lc)

    # Create fake devices.
    fake_pv = make_pv(pv_name="/dev/mapper/pv", vg_name="vg")
    fake_vg = make_vg(pvs=[fake_pv.name], vg_name="vg")
    fake_lv = make_lv(lv_name="lv", pvs=[fake_pv.name], vg_name=fake_vg.name)

    # Fake LV - 16MiB
    fake_lv_unreduced = make_lv(
        vg_name=fake_vg.name,
        lv_name="lv",
        pvs=[fake_pv.name],
        size="16777216")

    # Assign fake PV, VG, LV to cache.
    lc._pvs = {fake_pv.name: fake_pv}
    lc._vgs = {fake_vg.name: fake_vg}
    lc._lvs = {(fake_vg.name, fake_lv.name): fake_lv}

    # Do not attempt to use real devices.
    monkeypatch.setattr(lvm, "getLV", lambda x, y: fake_lv_unreduced)
    monkeypatch.setattr(lvm, "getVG", lambda x: fake_vg)

    with pytest.raises(se.LogicalVolumeExtendError):
        # Attempt to reduce by 8 MiB
        lvm.reduceLV(fake_vg.name, fake_lv.name, 8)

    # Verify that lvs and vgs are not invalidated after reduceLV() failed.
    assert not lvm._lvminfo._lvs[(fake_vg.name, fake_lv.name)].is_stale()
    assert not lvm._lvminfo._vgs[fake_vg.name].is_stale()


def test_removelvs_success_cache(monkeypatch, fake_devices):
    fake_runner = FakeRunner()
    lc = lvm.LVMCache(fake_runner)

    monkeypatch.setattr(lvm, "_lvminfo", lc)

    # Create fake devices.
    fake_pv = make_pv(pv_name="/dev/mapper/pv", vg_name="vg")
    fake_vg = make_vg(pvs=[fake_pv.name], vg_name="vg")
    fake_lv = make_lv(lv_name="lv", pvs=[fake_pv.name], vg_name=fake_vg.name)

    # Assign fake PV, VG, LV to cache.
    lc._pvs = {fake_pv.name: fake_pv}
    lc._vgs = {fake_vg.name: fake_vg}
    lc._lvs = {(fake_vg.name, fake_lv.name): fake_lv}

    lvm.removeLVs(fake_vg.name, [fake_lv.name])

    # Verify that lvs are removed from cache after removeLVs() succeeded.
    assert not lvm._lvminfo._lvs

    # Verify that vgs are invalidated after removeLVs() succeeded.
    assert lvm._lvminfo._vgs[fake_vg.name].is_stale()


def test_removelvs_failure_cache(monkeypatch, fake_devices):
    fake_runner = FakeRunner(rc=5)
    lc = lvm.LVMCache(fake_runner)

    monkeypatch.setattr(lvm, "_lvminfo", lc)

    # Create fake devices.
    fake_pv = make_pv(pv_name="/dev/mapper/pv", vg_name="vg")
    fake_vg = make_vg(pvs=[fake_pv.name], vg_name="vg")
    fake_lv = make_lv(lv_name="lv", pvs=[fake_pv.name], vg_name=fake_vg.name)

    # Assign fake PV, VG, LV to cache.
    lc._pvs = {fake_pv.name: fake_pv}
    lc._vgs = {fake_vg.name: fake_vg}
    lc._lvs = {(fake_vg.name, fake_lv.name): fake_lv}

    with pytest.raises(se.LogicalVolumeRemoveError):
        lvm.removeLVs(fake_vg.name, [fake_lv.name])

    # Verify that lvs are invalidated after removeLVs() failed.
    assert lvm._lvminfo._lvs[(fake_vg.name, fake_lv.name)].is_stale()


def test_createlv_success_cache(monkeypatch, fake_devices):
    fake_runner = FakeRunner()
    lc = lvm.LVMCache(fake_runner)

    monkeypatch.setattr(lvm, "_lvminfo", lc)

    # Create fake devices.
    fake_pv = make_pv(pv_name="/dev/mapper/pv", vg_name="vg")
    fake_vg = make_vg(pvs=[fake_pv.name], vg_name="vg")

    # Create fake control lv in same vg - should not get invalidated.
    fake_control_lv = make_lv(
        lv_name="controllv", pvs=[fake_pv.name], vg_name=fake_vg.name)

    # Assign fake PV, VG, LV to cache.
    lc._pvs = {fake_pv.name: fake_pv}
    lc._vgs = {fake_vg.name: fake_vg}
    lc._lvs = {fake_control_lv.name: fake_control_lv}

    # Call createLV - do not activate as it looks for real lv and would fail.
    lvm.createLV(fake_vg.name, "testlv", "512m", activate=False)

    # Verify vgs and lvs are invalidated after successful createLV().
    assert lvm._lvminfo._vgs[fake_vg.name].is_stale()
    assert lvm._lvminfo._lvs[(fake_vg.name, "testlv")].is_stale()

    # Verify control lv is not invalidated.
    assert not lvm._lvminfo._lvs[fake_control_lv.name].is_stale()


def test_reducevg_success_cache(monkeypatch):
    fake_runner = FakeRunner()
    lc = lvm.LVMCache(fake_runner)

    monkeypatch.setattr(lvm, "_lvminfo", lc)

    # Create fake devices.
    fake_pv1 = make_pv(pv_name="/dev/mapper/pv1", vg_name="vg")
    fake_pv2 = make_pv(pv_name="/dev/mapper/pv2", vg_name="vg")
    fake_vg = make_vg(pvs=[fake_pv1.name, fake_pv2.name], vg_name="vg")

    # Assign fake PV, VG to cache.
    lc._pvs = {fake_pv1.name: fake_pv1, fake_pv2.name: fake_pv2}
    lc._vgs = {fake_vg.name: fake_vg}

    lvm.reduceVG(fake_vg.name, fake_pv2.name)

    # Verify other pvs were not invalidated.
    assert not lvm._lvminfo._pvs[fake_pv1.name].is_stale()

    # Verify that pvs and vgs are invalidated after reduceVG() succeeded.
    assert lvm._lvminfo._pvs[fake_pv2.name].is_stale()
    assert lvm._lvminfo._vgs[fake_vg.name].is_stale()


def test_reducevg_failure_cache(monkeypatch, fake_devices):
    fake_runner = FakeRunner(rc=5)
    lc = lvm.LVMCache(fake_runner)

    monkeypatch.setattr(lvm, "_lvminfo", lc)

    # Create fake devices.
    fake_pv1 = make_pv(pv_name="/dev/mapper/pv1", vg_name="vg")
    fake_pv2 = make_pv(pv_name="/dev/mapper/pv2", vg_name="vg")
    fake_vg = make_vg(pvs=[fake_pv1.name, fake_pv2.name], vg_name="vg")

    # Assign fake PV, VG to cache.
    lc._pvs = {fake_pv1.name: fake_pv1, fake_pv2.name: fake_pv2}
    lc._vgs = {fake_vg.name: fake_vg}

    with pytest.raises(se.VolumeGroupReduceError):
        lvm.reduceVG(fake_vg.name, fake_pv2.name)

    # Verify that pvs and vgs are not invalidated after reduceVG() failed.
    assert not lvm._lvminfo._pvs[fake_pv1.name].is_stale()
    assert not lvm._lvminfo._pvs[fake_pv2.name].is_stale()
    assert not lvm._lvminfo._vgs[fake_vg.name].is_stale()


def test_removevg_failure_cache(monkeypatch, fake_devices):
    fake_runner = FakeRunner(rc=5)
    lc = lvm.LVMCache(fake_runner)

    monkeypatch.setattr(lvm, "_lvminfo", lc)

    # Create fake devices.
    fake_pv1 = make_pv(pv_name="/dev/mapper/pv1", vg_name="vg")
    fake_pv2 = make_pv(pv_name="/dev/mapper/pv2", vg_name="vg")
    fake_vg = make_vg(pvs=[fake_pv1.name, fake_pv2.name], vg_name="vg")

    # Create fake control pv in a different vg - should not get invalidated.
    fake_control_pv = make_pv(
        pv_name="/dev/mapper/controlpv", vg_name="controlvg")
    fake_control_vg = make_vg(pvs=[fake_control_pv.name], vg_name="controlvg")

    # Assign fake PV, VG to cache.
    lc._pvs = {
        fake_pv1.name: fake_pv1,
        fake_pv2.name: fake_pv2,
        fake_control_pv.name: fake_control_pv,
    }
    lc._vgs = {fake_vg.name: fake_vg, fake_control_vg.name: fake_control_vg}

    with pytest.raises(se.VolumeGroupRemoveError):
        lvm.removeVG("vg")

    # Verify that pvs and vgs are invalidated after removeVG() failed.
    assert lvm._lvminfo._pvs[fake_pv1.name].is_stale()
    assert lvm._lvminfo._pvs[fake_pv2.name].is_stale()
    assert lvm._lvminfo._vgs[fake_vg.name].is_stale()

    # Verify control pvs and vgs are not invalidated.
    assert not lvm._lvminfo._pvs[fake_control_pv.name].is_stale()
    assert not lvm._lvminfo._vgs[fake_control_vg.name].is_stale()


def test_deactivatevg_failure_cache(monkeypatch, fake_devices):
    fake_runner = FakeRunner(rc=5, err=b"Fake lvm error")
    lc = lvm.LVMCache(fake_runner)

    monkeypatch.setattr(lvm, "_lvminfo", lc)

    # Create fake devices.
    fake_pv = make_pv(pv_name="/dev/mapper/pv", vg_name="vg")
    fake_vg = make_vg(pvs=[fake_pv.name], vg_name="vg")
    fake_lv = make_lv(lv_name="lv", pvs=[fake_pv.name], vg_name=fake_vg.name)

    # Assign fake PV, VG, LV to cache.
    lc._pvs = {fake_pv.name: fake_pv}
    lc._vgs = {fake_vg.name: fake_vg}
    lc._lvs[(fake_vg.name, fake_lv.name)] = fake_lv

    # Deactivate vg - does not raise.
    lvm.deactivateVG(fake_vg.name)

    # TODO: verify that vg mappings were removed
    # Verify that lvs are invalidated after deactivateVG() failed.
    assert lvm._lvminfo._lvs[(fake_vg.name, fake_lv.name)].is_stale()
    # Verify that getVG raises with a LVM error
    with pytest.raises(se.VolumeGroupDoesNotExist) as e:
        lvm.getVG("non-existing-vg-name")
    # Ensure that the error is captured and printed in the exception.
    assert "Fake lvm error" in str(e.value)
    # Local cache shall remain intact after an LVM error
    assert len(lc._vgs) == 1
    assert len(lc._pvs) == 1
    assert len(lc._lvs) == 1


def test_resizepv_success_cache(monkeypatch):
    fake_runner = FakeRunner()
    lc = lvm.LVMCache(fake_runner)

    monkeypatch.setattr(lvm, "_lvminfo", lc)

    # Create fake pv - should get invalidated.
    fake_pv = make_pv(pv_name="/dev/mapper/pv", vg_name="vg")

    # Create fake control pv - should not get invalidated.
    fake_control_pv1 = make_pv(pv_name="/dev/mapper/controlpv1", vg_name="vg")

    # Assign both pvs to same vg.
    fake_vg = make_vg(
        pvs=[fake_pv.name, fake_control_pv1.name], vg_name="vg")

    # Create fake control pv in a different vg - should not get invalidated.
    fake_control_pv2 = make_pv(
        pv_name="/dev/mapper/controlpv2", vg_name="controlvg")
    fake_control_vg = make_vg(
        vg_name="controlvg", pvs=[fake_control_pv2.name])

    # Assign fake PV, VG to cache.
    lc._pvs = {
        fake_pv.name: fake_pv,
        fake_control_pv1.name: fake_control_pv1,
        fake_control_pv2.name: fake_control_pv2,
    }
    lc._vgs = {fake_vg.name: fake_vg, fake_control_vg.name: fake_control_vg}

    # Call resizePV.
    lvm.resizePV(fake_vg.name, fake_pv.name)

    # Verify pvs and vgs are invalidated after successful resizePV().
    assert lvm._lvminfo._pvs[fake_pv.name].is_stale()
    assert lvm._lvminfo._vgs[fake_vg.name].is_stale()

    # Verify control pvs and vgs are not invalidated.
    assert not lvm._lvminfo._pvs[fake_control_pv1.name].is_stale()
    assert not lvm._lvminfo._pvs[fake_control_pv2.name].is_stale()
    assert not lvm._lvminfo._vgs[fake_control_vg.name].is_stale()


def test_resizepv_failure_cache(monkeypatch, fake_devices):
    fake_runner = FakeRunner(rc=5)
    lc = lvm.LVMCache(fake_runner)

    monkeypatch.setattr(lvm, "_lvminfo", lc)

    # Create fake devices.
    fake_pv = make_pv(pv_name="/dev/mapper/pv", vg_name="vg")
    fake_vg = make_vg(pvs=[fake_pv.name], vg_name="vg")

    # Assign fake PV, VG to cache.
    lc._pvs = {fake_pv.name: fake_pv}
    lc._vgs = {fake_vg.name: fake_vg}

    # Check correct exception is raised.
    with pytest.raises(se.CouldNotResizePhysicalVolume):
        lvm.resizePV(fake_vg.name, fake_pv.name)

    # Verify pvs and vgs are not invalidated after failed resizePV().
    assert not lvm._lvminfo._pvs[fake_pv.name].is_stale()
    assert not lvm._lvminfo._vgs[fake_vg.name].is_stale()


def test_cmd_retry_filter_stale(fake_devices, use_filter):
    # Make a call to load the cache.
    initial_devices = fake_devices[:]
    fake_runner = FakeRunner()
    lc = lvm.LVMCache(fake_runner)
    lc.run_command(["fake"])
    del fake_runner.calls[:]

    # Add a new device to the system. This will makes the cached filter stale,
    # so the command will be retried with a new filter.
    fake_devices.append("/dev/mapper/c")

    # Require 2 calls to succeed.
    fake_runner.retries = 1

    lc.run_command(["fake"])

    assert len(fake_runner.calls) == 2

    # The first call used the stale cache filter.
    cmd = fake_runner.calls[0]
    assert cmd == [
        constants.EXT_LVM,
        "fake",
        "--config",
        build_config(initial_devices),
    ]

    # The seocnd call used a wider filter.
    cmd = fake_runner.calls[1]
    assert cmd == [
        constants.EXT_LVM,
        "fake",
        "--config",
        build_config(fake_devices)
    ]


def test_suppress_warnings(fake_devices):
    fake_runner = FakeRunner()
    fake_runner.err = b"""\
  before
  WARNING: This metadata update is NOT backed up.
  WARNING: Combining activation change with other commands is not advised.
  WARNING: Activation disabled. No device-mapper interaction will be attempted.
  Configuration setting "global/event_activation" unknown.
  WARNING: ignoring metadata seqno 1566 on /dev/mapper/3600a098038304437415d4b6a59684474 for seqno 1567 on /dev/mapper/3600a098038304437415d4b6a59684474 for VG Bug."
  WARNING: Inconsistent metadata found for VG Bug."
  after"""  # NOQA: E501 (potentially long line)

    lc = lvm.LVMCache(fake_runner)
    fake_runner.rc = 1
    with pytest.raises(se.LVMCommandError) as e:
        lc.run_command(["fake"])
    assert e.value.rc == 1
    assert e.value.err == [
        u"  before",
        (u"  WARNING: Combining activation change with other commands is "
         "not advised."),
        u"  Configuration setting \"global/event_activation\" unknown.",
        u"  after"
    ]


def test_suppress_multiple_lvm_warnings(fake_devices):
    fake_runner = FakeRunner()
    fake_runner.err = b"""\
  before
  WARNING: This metadata update is NOT backed up.
  WARNING: This metadata update is NOT backed up.
  WARNING: This metadata update is NOT backed up.
  after"""

    lc = lvm.LVMCache(fake_runner)
    fake_runner.rc = 1
    with pytest.raises(se.LVMCommandError) as e:
        lc.run_command(["fake"])
    assert e.value.rc == 1
    assert e.value.err == [u"  before", u"  after"]


def test_pv_move_cmd(fake_devices, monkeypatch, use_filter):
    fake_runner = FakeRunner()
    lc = lvm.LVMCache(fake_runner)

    # Don't invalidate PVs in cache because we use cache only without real PVs.
    lc._invalidatepvs = lambda pvNames: None

    monkeypatch.setattr(lvm, "_lvminfo", lc)

    # Prepare fake PVs.
    fake_pv1 = lvm.PV(
        uuid='id',
        name='/dev/mapper/a',
        size='123',
        vg_name="vg",
        vg_uuid='id',
        pe_start='123',
        pe_count='5',
        pe_alloc_count='1',
        mda_count='1',
        dev_size='123',
        mda_used_count='1',
        guid='a')

    fake_pv2 = lvm.PV(
        uuid='id',
        name='/dev/mapper/b',
        size='123',
        vg_name="vg",
        vg_uuid='id',
        pe_start='123',
        pe_count='5',
        pe_alloc_count='1',
        mda_count='1',
        dev_size='123',
        mda_used_count='1',
        guid='b')

    # Assign fake PVs to cache.
    lc._pvs = {"/dev/mapper/a": fake_pv1, "/dev/mapper/b": fake_pv2}

    # Run pvmove command.
    lvm.movePV("vg", "a", "b")

    # Check the pvmove command had lvmpolld disabled.
    cmd = fake_runner.calls[0]
    assert cmd == [
        constants.EXT_LVM,
        "pvmove",
        "--config",
        build_config(fake_devices, use_lvmpolld="0"),
        *fake_devices
    ]


class Workers(object):

    def __init__(self):
        self.threads = []

    def start_thread(self, func, *args):
        t = concurrent.thread(func, args=args)
        t.start()
        self.threads.append(t)

    def join(self):
        for t in self.threads:
            t.join()


@pytest.fixture
def workers():
    workers = Workers()
    try:
        yield workers
    finally:
        workers.join()


def test_command_concurrency(fake_devices, workers):
    # Test concurrent commands to reveal locking issues.
    fake_runner = FakeRunner()
    lc = lvm.LVMCache(fake_runner)

    fake_runner.delay = 0.2
    count = 50
    start = time.time()
    try:
        for i in range(count):
            workers.start_thread(lc.run_command, ["fake", i])
    finally:
        workers.join()

    elapsed = time.time() - start
    assert len(fake_runner.calls) == count

    # This takes about 1 second on my idle laptop. Add more time to avoid
    # failures on overloaded slave.
    assert elapsed < fake_runner.delay * count / lc.MAX_COMMANDS + 1.0


@requires_root
@pytest.mark.root
def test_vg_create_remove_single_device(tmp_storage):
    dev_size = 10 * GiB
    dev = tmp_storage.create_device(dev_size)
    vg_name = str(uuid.uuid4())

    lvm.createVG(vg_name, [dev], "initial-tag", 128)

    clear_stats()
    vg = lvm.getVG(vg_name)
    check_stats(hits=0, misses=1)

    assert vg.name == vg_name
    assert vg.pv_name == (dev,)
    assert vg.tags == ("initial-tag",)
    assert int(vg.extent_size) == 128 * MiB

    clear_stats()
    pv = lvm.getPV(dev)
    check_stats(hits=0, misses=1)

    # Call getPV again to see we also get cache hit.
    lvm.getPV(dev)
    check_stats(hits=1, misses=1)

    assert pv.name == dev
    assert pv.vg_name == vg_name
    assert int(pv.dev_size) == dev_size
    assert int(pv.mda_count) == 2
    assert int(pv.mda_used_count) == 2

    lvm.removeVG(vg_name)

    # We remove the VG
    clear_stats()
    with pytest.raises(se.VolumeGroupDoesNotExist) as e:
        lvm.getVG(vg_name)
    check_stats(hits=0, misses=1)

    assert vg_name in str(e.value)

    # But keep the PVs, not sure why.
    clear_stats()
    pv = lvm.getPV(dev)
    check_stats(hits=0, misses=1)

    assert pv.name == dev
    assert pv.vg_name == ""


@requires_root
@pytest.mark.root
def test_vg_create_multiple_devices(tmp_storage):
    dev_size = 10 * GiB
    dev1 = tmp_storage.create_device(dev_size)
    dev2 = tmp_storage.create_device(dev_size)
    dev3 = tmp_storage.create_device(dev_size)
    vg_name = str(uuid.uuid4())

    # TODO: should work also in read-only mode.
    lvm.createVG(vg_name, [dev1, dev2, dev3], "initial-tag", 128)

    vg = lvm.getVG(vg_name)
    assert vg.name == vg_name
    assert sorted(vg.pv_name) == sorted((dev1, dev2, dev3))

    # The first pv (metadata pv) will have the 2 used metadata areas.
    clear_stats()
    pv = lvm.getPV(dev1)
    check_stats(hits=0, misses=1)

    assert pv.name == dev1
    assert pv.vg_name == vg_name
    assert int(pv.dev_size) == dev_size
    assert int(pv.mda_count) == 2
    assert int(pv.mda_used_count) == 2

    # The rest of the pvs will have 2 unused metadata areas.
    for dev in dev2, dev3:
        clear_stats()
        pv = lvm.getPV(dev)
        check_stats(hits=0, misses=1)

        assert pv.name == dev
        assert pv.vg_name == vg_name
        assert int(pv.dev_size) == dev_size
        assert int(pv.mda_count) == 2
        assert int(pv.mda_used_count) == 0

    # TODO: should work also in read-only mode.
    lvm.removeVG(vg_name)

    # We remove the VG
    clear_stats()
    with pytest.raises(se.VolumeGroupDoesNotExist) as e:
        lvm.getVG(vg_name)
    check_stats(hits=0, misses=1)

    assert vg_name in str(e.value)

    # But keep the PVs, not sure why.
    for dev in dev1, dev2, dev3:
        clear_stats()
        pv = lvm.getPV(dev)
        check_stats(hits=0, misses=1)

        assert pv.name == dev
        assert pv.vg_name == ""


@requires_root
@pytest.mark.root
def test_vg_remove_by_uuid(tmp_storage):
    dev_size = 10 * GiB

    dev1 = tmp_storage.create_device(dev_size)
    dev2 = tmp_storage.create_device(dev_size)
    vg1_name = str(uuid.uuid4())
    vg2_name = str(uuid.uuid4())

    lvm.createVG(vg1_name, [dev1], "initial-tag", 128)
    lvm.createVG(vg2_name, [dev2], "initial-tag", 128)

    vg = lvm.getVG(vg1_name)

    clear_stats()
    lvm.removeVGbyUUID(vg.uuid)
    check_stats(hits=0, misses=1)

    # Ensure we have removed the matching VG.
    with pytest.raises(se.VolumeGroupDoesNotExist):
        lvm.getVGbyUUID(vg.uuid)

    with pytest.raises(se.VolumeGroupDoesNotExist):
        lvm.getVG(vg1_name)

    assert len(lvm.getAllVGs()) == 1

    # The other VG is still available.
    vg2 = lvm.getVG(vg2_name)
    assert vg2.name == vg2_name


def test_vg_invalid_output(monkeypatch, fake_devices):
    fake_runner = FakeRunner(out=b"Fake lvm output")
    lc = lvm.LVMCache(fake_runner)
    lc._stalevg = False

    # Create fake devices.
    fake_pv = make_pv(pv_name=fake_devices[0], vg_name="vg")
    fake_vg = make_vg(pvs=[fake_pv.name], vg_name="vg")

    # Assign fake PV, VG to cache.
    lc._pvs = {fake_pv.name: fake_pv}
    lc._vgs = {fake_vg.name: fake_vg}

    monkeypatch.setattr(lvm, "_lvminfo", lc)

    # TODO: is this the best way to handle this unlikely error
    # An error with an LVM invalid output shall keep the local cache intact
    with pytest.raises(lvm.InvalidOutputLine) as e:
        lvm.getVG("vg-name")
    assert "Fake lvm output" in str(e.value)
    assert len(lc._vgs) == 1
    assert len(lc._pvs) == 1


@pytest.fixture
def stale_pv(tmp_storage):
    dev_size = 1 * 1024**3

    good_pv_name = tmp_storage.create_device(dev_size)
    stale_pv_name = tmp_storage.create_device(dev_size)
    vg_name = str(uuid.uuid4())

    # Create VG with 2 PVs.
    lvm.createVG(vg_name, [good_pv_name, stale_pv_name], "initial-tag", 128)

    # Reload the cache.
    pvs = sorted(pv.name for pv in lvm.getAllPVs())
    assert pvs == sorted([good_pv_name, stale_pv_name])

    devices = ",".join([good_pv_name, stale_pv_name])
    # Simulate removal of the second PV on another host, leaving stale PV in
    # the cache.
    commands.run([
        "vgreduce",
        "--devices", devices,
        vg_name,
        stale_pv_name,
    ])
    commands.run([
        "pvremove",
        "--devices", devices,
        stale_pv_name,
    ])

    # We still report both devies.
    pvs = sorted(pv.name for pv in lvm.getAllPVs())
    assert pvs == sorted([good_pv_name, stale_pv_name])

    return vg_name, good_pv_name, stale_pv_name


@requires_root
@pytest.mark.root
def test_pv_stale_reload_one_stale(stale_pv):
    vg_name, good_pv_name, stale_pv_name = stale_pv

    # Invalidate VG and its PVs.
    lvm.invalidateVG(vg_name, invalidatePVs=True)

    # The good pv is still in the cache.
    pv = lvm.getPV(good_pv_name)
    assert pv.name == good_pv_name

    # Reloading the stale pv marks it as Unreadable.
    pv = lvm.getPV(stale_pv_name)
    assert pv == lvm.Unreadable(stale_pv_name)


@requires_root
@pytest.mark.root
def test_pv_stale_reload_invalidated(stale_pv):
    vg_name, good_pv_name, stale_pv_name = stale_pv

    # Invalidate the good pv.
    lvm._lvminfo._invalidatepvs(good_pv_name)
    # Reloading the good pv returns it as valid.
    pv = lvm.getPV(good_pv_name)
    assert pv.name == good_pv_name

    # Invalidate the stale pv.
    lvm._lvminfo._invalidatepvs(stale_pv_name)
    # Reloading the stale pv returns it as Unreadable.
    pv = lvm.getPV(stale_pv_name)
    assert pv == lvm.Unreadable(stale_pv_name)


@requires_root
@pytest.mark.root
def test_pv_stale_reload_one_clear(stale_pv):
    vg_name, good_pv_name, stale_pv_name = stale_pv

    # Drop all cache.
    lvm.invalidateCache()

    # The good pv is still in the cache.
    pv = lvm.getPV(good_pv_name)
    assert pv.name == good_pv_name

    # The stale pv shuld be removed.
    with pytest.raises(se.InaccessiblePhysDev):
        lvm.getPV(stale_pv_name)


@requires_root
@pytest.mark.root
def test_pv_stale_reload_all_stale(stale_pv):
    vg_name, good_pv_name, stale_pv_name = stale_pv

    # Invalidate VG and its PVs.
    lvm.invalidateVG(vg_name, invalidatePVs=True)

    # Reloading all PVs will return them as Unreadable due to missing
    # stale PV.
    assert set(lvm.getAllPVs()) == {
        lvm.Unreadable(good_pv_name),
        lvm.Unreadable(stale_pv_name)
    }


@requires_root
@pytest.mark.root
def test_pv_stale_reload_all_clear(stale_pv):
    vg_name, good_pv_name, stale_pv_name = stale_pv

    # Drop all cache.
    lvm.invalidateCache()

    # Report only the good pv.
    pv_names = [pv.name for pv in lvm.getAllPVs()]
    assert pv_names == [good_pv_name]


@requires_root
@pytest.mark.root
def test_vg_extend_reduce(tmp_storage):
    dev_size = 10 * GiB
    dev1 = tmp_storage.create_device(dev_size)
    dev2 = tmp_storage.create_device(dev_size)
    dev3 = tmp_storage.create_device(dev_size)
    vg_name = str(uuid.uuid4())

    lvm.createVG(vg_name, [dev1], "initial-tag", 128)

    clear_stats()
    vg = lvm.getVG(vg_name)
    check_stats(hits=0, misses=1)

    # Call getVG() again will get cache hit.
    lvm.getVG(vg_name)
    check_stats(hits=1, misses=1)

    assert vg.pv_name == (dev1,)

    lvm.extendVG(vg_name, [dev2, dev3], force=False)

    clear_stats()
    vg = lvm.getVG(vg_name)
    # Calling getVG() after extendVG() does not use the cache.
    # This happens because extendVG() invalidates the VG.
    check_stats(hits=0, misses=1)

    assert sorted(vg.pv_name) == sorted((dev1, dev2, dev3))

    clear_stats()
    # The first pv (metadata pv) will have the 2 used metadata areas.
    pv = lvm.getPV(dev1)
    check_stats(hits=0, misses=1)

    assert pv.name == dev1
    assert pv.vg_name == vg_name
    assert int(pv.dev_size) == dev_size
    assert int(pv.mda_count) == 2
    assert int(pv.mda_used_count) == 2

    # The rest of the pvs will have 2 unused metadata areas.
    for dev in dev2, dev3:
        clear_stats()
        pv = lvm.getPV(dev)
        check_stats(hits=0, misses=1)

        assert pv.name == dev
        assert pv.vg_name == vg_name
        assert int(pv.dev_size) == dev_size
        assert int(pv.mda_count) == 2
        assert int(pv.mda_used_count) == 0

    lvm.reduceVG(vg_name, dev2)
    clear_stats()
    vg = lvm.getVG(vg_name)
    # Calling getVG() after reduceVG() does not use the cache.
    # This happens because reduceVG() invalidates the VG.
    check_stats(hits=0, misses=1)

    assert sorted(vg.pv_name) == sorted((dev1, dev3))

    lvm.removeVG(vg_name)

    clear_stats()
    with pytest.raises(se.VolumeGroupDoesNotExist):
        lvm.getVG(vg_name)
    check_stats(hits=0, misses=1)


@requires_root
@pytest.mark.root
def test_vg_add_delete_tags(tmp_storage):
    dev_size = 10 * GiB
    dev = tmp_storage.create_device(dev_size)
    vg_name = str(uuid.uuid4())

    lvm.createVG(vg_name, [dev], "initial-tag", 128)

    lvm.changeVGTags(
        vg_name,
        delTags=("initial-tag",),
        addTags=("new-tag-1", "new-tag-2"))

    lvm.changeVGTags(
        vg_name,
        delTags=["initial-tag"],
        addTags=["new-tag-1", "new-tag-2"])

    clear_stats()
    vg = lvm.getVG(vg_name)
    check_stats(hits=0, misses=1)

    assert sorted(vg.tags) == ["new-tag-1", "new-tag-2"]


@requires_root
@pytest.mark.root
def test_vg_check(tmp_storage):
    dev_size = 10 * GiB
    dev1 = tmp_storage.create_device(dev_size)
    dev2 = tmp_storage.create_device(dev_size)
    vg_name = str(uuid.uuid4())

    lvm.createVG(vg_name, [dev1, dev2], "initial-tag", 128)

    lvm.chkVG(vg_name)


@requires_root
@pytest.mark.root
def test_vg_invalidate(tmp_storage):
    dev_size = 1 * GiB

    dev1 = tmp_storage.create_device(dev_size)
    dev2 = tmp_storage.create_device(dev_size)
    vg1_name = str(uuid.uuid4())
    vg2_name = str(uuid.uuid4())

    lvm.createVG(vg1_name, [dev1], "initial-tag", 128)
    lvm.createLV(vg1_name, "lv1", 128, activate=False)

    lvm.createVG(vg2_name, [dev2], "initial-tag", 128)
    lvm.createLV(vg2_name, "lv2", 128, activate=False)

    # Reload cache.

    pv1 = lvm.getPV(dev1)
    vg1 = lvm.getVG(vg1_name)
    lv1 = lvm.getAllLVs(vg1_name)[0]

    pv2 = lvm.getPV(dev2)
    vg2 = lvm.getVG(vg2_name)
    lv2 = lvm.getAllLVs(vg2_name)[0]

    assert lvm._lvminfo._pvs == {dev1: pv1, dev2: pv2}
    assert lvm._lvminfo._vgs == {vg1_name: vg1, vg2_name: vg2}
    assert lvm._lvminfo._lvs == {
        (vg1_name, "lv1"): lv1,
        (vg2_name, "lv2"): lv2,
    }

    # Invalidate VG including LVs.
    lvm.invalidateVG(vg1_name, invalidateLVs=False)

    assert lvm._lvminfo._pvs == {dev1: pv1, dev2: pv2}
    assert lvm._lvminfo._vgs == {
        vg1_name: lvm.Stale(vg1_name),
        vg2_name: vg2,
    }
    assert lvm._lvminfo._lvs == {
        (vg1_name, "lv1"): lv1,
        (vg2_name, "lv2"): lv2,
    }

    # getVGs() always reloads the cache.
    clear_stats()
    lvm.getVGs([vg1_name, vg2_name])
    check_stats(hits=0, misses=1)

    assert lvm._lvminfo._vgs == {vg1_name: vg1, vg2_name: vg2}


@requires_root
@pytest.mark.root
def test_vg_invalidate_lvs(tmp_storage):
    dev_size = 1 * GiB
    dev = tmp_storage.create_device(dev_size)
    vg_name = str(uuid.uuid4())

    lvm.createVG(vg_name, [dev], "initial-tag", 128)
    lvm.createLV(vg_name, "lv1", 128, activate=False)

    # Reload cache.
    pv = lvm.getPV(dev)
    vg = lvm.getVG(vg_name)

    clear_stats()
    lv = lvm.getAllLVs(vg_name)[0]
    check_stats(hits=0, misses=1)

    # Accessing LVs always access storage.
    # TODO: Use cache if VG did not change.
    lvm.getAllLVs(vg_name)
    check_stats(hits=0, misses=2)

    assert lvm._lvminfo._pvs == {dev: pv}
    assert lvm._lvminfo._vgs == {vg_name: vg}
    assert lvm._lvminfo._lvs == {(vg_name, "lv1"): lv}

    # Invalidate VG including LVs.
    lvm.invalidateVG(vg_name)

    assert lvm._lvminfo._pvs == {dev: pv}
    assert lvm._lvminfo._vgs == {vg_name: lvm.Stale(vg_name)}
    assert lvm._lvminfo._lvs == {(vg_name, "lv1"): lvm.Stale("lv1")}

    # Accessing LVs always access storage.
    # TODO: Use cache if VG did not change.
    clear_stats()
    lvm.getAllLVs(vg_name)
    check_stats(hits=0, misses=1)


@requires_root
@pytest.mark.root
def test_vg_invalidate_lvs_pvs(tmp_storage):
    dev_size = 1 * GiB
    dev = tmp_storage.create_device(dev_size)
    vg_name = str(uuid.uuid4())

    lvm.createVG(vg_name, [dev], "initial-tag", 128)
    lvm.createLV(vg_name, "lv1", 128, activate=False)

    # Reload cache.
    pv = lvm.getPV(dev)
    vg = lvm.getVG(vg_name)
    lv = lvm.getAllLVs(vg_name)[0]

    assert lvm._lvminfo._pvs == {dev: pv}

    clear_stats()
    lvm._lvminfo.getPvs(vg_name)
    # getPVs() first finds the VG using getVG(), so there is a cache hit.
    # No stale PVs for the VG so getPVs() will have another cache hit.
    check_stats(hits=2, misses=0)

    assert lvm._lvminfo._vgs == {vg_name: vg}
    assert lvm._lvminfo._lvs == {(vg_name, "lv1"): lv}

    # Invalidate VG including LVs and PVs.
    lvm.invalidateVG(vg_name, invalidatePVs=True)

    assert lvm._lvminfo._vgs == {vg_name: lvm.Stale(vg_name)}
    assert lvm._lvminfo._pvs == {dev: lvm.Stale(dev)}

    clear_stats()
    lvm._lvminfo.getPvs(vg_name)
    # getPVs() will not find the invalidated VG in cache, so there is a miss.
    # There are stale PVs for the VG so getPVs() will have another cache miss.
    check_stats(hits=0, misses=2)

    assert lvm._lvminfo._lvs == {(vg_name, "lv1"): lvm.Stale("lv1")}


@requires_root
@pytest.mark.root
def test_lv_create_remove(tmp_storage):
    dev_size = 10 * GiB
    dev1 = tmp_storage.create_device(dev_size)
    dev2 = tmp_storage.create_device(dev_size)
    vg_name = str(uuid.uuid4())
    lv_any = "lv-on-any-device"
    lv_specific = "lv-on-device-2"

    lvm.createVG(vg_name, [dev1, dev2], "initial-tag", 128)

    # Create the first LV on any device.
    lvm.createLV(vg_name, lv_any, 1024)

    clear_stats()
    lv = lvm.getLV(vg_name, lv_any)
    check_stats(hits=0, misses=1)

    # Call getLV() again will have cache hit.
    lvm.getLV(vg_name, lv_any)
    check_stats(hits=1, misses=1)

    assert lv.name == lv_any
    assert lv.vg_name == vg_name
    assert int(lv.size) == GiB
    assert lv.tags == ()
    assert lv.writeable
    assert not lv.opened
    assert lv.active

    # LV typically created on dev1.
    device, extent = lvm.getFirstExt(vg_name, lv_any)
    assert device in dev1, dev2
    assert extent == "0"

    # Create the second LV on dev2.
    lvm.createLV(vg_name, lv_specific, 1024, device=dev2)

    device, extent = lvm.getFirstExt(vg_name, lv_specific)
    assert device == dev2

    # Remove both LVs.
    lvm.removeLVs(vg_name, [lv_any, lv_specific])

    for lv_name in (lv_any, lv_specific):
        clear_stats()
        with pytest.raises(se.LogicalVolumeDoesNotExistError):
            lvm.getLV(vg_name, lv_name)
        check_stats(hits=0, misses=1)


@requires_root
@pytest.mark.root
def test_lv_create_zero(tmp_storage):
    dev_size = 10 * GiB
    dev = tmp_storage.create_device(dev_size)
    vg_name = str(uuid.uuid4())

    lvm.createVG(vg_name, [dev], "initial-tag", 128)

    # Create first LV with a filesystem and remove it.
    lvm.createLV(vg_name, "lv1", 1024)
    lv1 = lvm.getLV(vg_name, "lv1")
    commands.run(["mkfs.xfs", lvm.lvPath(vg_name, "lv1")])
    lvm.removeLVs(vg_name, ["lv1"])

    # Create second lv, using same extents. This fails if lvm is using
    # --wipesignature y.
    lvm.createLV(vg_name, "lv2", 1024)
    lv2 = lvm.getLV(vg_name, "lv2")

    # Check that lv1 and lv2 uses the same extent.
    assert lv1.devices == lv2.devices

    # Check that first 4k of lv2 are zeroed.
    with open(lvm.lvPath(vg_name, "lv2"), "rb") as f:
        assert f.read(4096) == b"\0" * 4096


@requires_root
@pytest.mark.root
def test_lv_add_delete_tags(tmp_storage):
    dev_size = 10 * GiB
    dev = tmp_storage.create_device(dev_size)
    vg_name = str(uuid.uuid4())
    lv1_name = str(uuid.uuid4())
    lv2_name = str(uuid.uuid4())

    lvm.createVG(vg_name, [dev], "initial-tag", 128)

    lvm.createLV(vg_name, lv1_name, 1024, activate=False)
    lvm.createLV(vg_name, lv2_name, 1024, activate=False)

    lvm.changeLVsTags(
        vg_name,
        (lv1_name, lv2_name),
        delTags=("initial-tag",),
        addTags=("new-tag-1", "new-tag-2"))

    lv1 = lvm.getLV(vg_name, lv1_name)
    lv2 = lvm.getLV(vg_name, lv2_name)
    assert sorted(lv1.tags) == ["new-tag-1", "new-tag-2"]
    assert sorted(lv2.tags) == ["new-tag-1", "new-tag-2"]


@pytest.fixture
def stale_vg(tmp_storage):
    dev_size = 1 * 1024**3
    dev1 = tmp_storage.create_device(dev_size)
    dev2 = tmp_storage.create_device(dev_size)

    good_vg_name = str(uuid.uuid4())
    stale_vg_name = str(uuid.uuid4())

    # Create 1 VGs
    lvm.createVG(good_vg_name, [dev1], "initial-tag", 128)
    lvm.createVG(stale_vg_name, [dev2], "initial-tag", 128)

    # Reload the cache.
    vgs = sorted(vg.name for vg in lvm.getAllVGs())
    assert vgs == sorted([good_vg_name, stale_vg_name])

    # Simulate removal of the second VG on another host, leaving stale VG in
    # the cache.
    commands.run([
        "vgremove",
        "--devices", dev2,
        stale_vg_name,
    ])

    # We still report both vgs.
    vgs = sorted(vg.name for vg in lvm.getAllVGs())
    assert vgs == sorted([good_vg_name, stale_vg_name])

    return good_vg_name, stale_vg_name


@requires_root
@pytest.mark.root
def test_vg_stale_reload_one_stale(stale_vg):
    good_vg_name, stale_vg_name = stale_vg

    # Invalidate vgs.
    lvm.invalidateVG(good_vg_name)
    lvm.invalidateVG(stale_vg_name)

    # The good vg is still in the cache.
    vg = lvm.getVG(good_vg_name)
    assert vg.name == good_vg_name

    # The stale vg was removed.
    with pytest.raises(se.VolumeGroupDoesNotExist):
        lvm.getVG(stale_vg_name)


@requires_root
@pytest.mark.root
def test_vg_stale_reload_one_clear(stale_vg):
    good_vg_name, stale_vg_name = stale_vg

    # Drop all cache.
    lvm.invalidateCache()

    # The good vg is still in the cache.
    vg = lvm.getVG(good_vg_name)
    assert vg.name == good_vg_name

    # The stale vg was removed.
    with pytest.raises(se.VolumeGroupDoesNotExist):
        lvm.getVG(stale_vg_name)


@requires_root
@pytest.mark.root
def test_vg_stale_reload_all_stale(stale_vg):
    good_vg_name, stale_vg_name = stale_vg

    # Invalidate vgs.
    lvm.invalidateVG(good_vg_name)
    lvm.invalidateVG(stale_vg_name)

    clear_stats()
    # Report only the good vg.
    vgs = [vg.name for vg in lvm.getAllVGs()]
    assert vgs == [good_vg_name]
    check_stats(hits=0, misses=1)

    # Second call for getAllVGs() will add cache hit.
    lvm.getAllVGs()
    check_stats(hits=1, misses=1)


@requires_root
@pytest.mark.root
def test_vg_stale_reload_all_clear(stale_vg):
    good_vg_name, stale_vg_name = stale_vg

    # Drop all cache.
    lvm.invalidateCache()

    clear_stats()
    # Report only the good vg.
    vgs = [vg.name for vg in lvm.getAllVGs()]
    assert vgs == [good_vg_name]
    check_stats(hits=0, misses=1)

    # Second call for getAllVGs() will add cache hit.
    lvm.getAllVGs()
    check_stats(hits=1, misses=1)


@requires_root
@pytest.mark.root
def test_lv_activate_deactivate(tmp_storage):
    dev_size = 10 * GiB
    dev = tmp_storage.create_device(dev_size)
    vg_name = str(uuid.uuid4())
    lv_name = str(uuid.uuid4())

    lvm.createVG(vg_name, [dev], "initial-tag", 128)
    lvm.createLV(vg_name, lv_name, 1024, activate=False)

    lv = lvm.getLV(vg_name, lv_name)
    assert not lv.active

    # Activate the inactive lv.
    lvm.activateLVs(vg_name, [lv_name])
    lv = lvm.getLV(vg_name, lv_name)
    assert lv.active

    # Deactivate the active lv.
    lvm.deactivateLVs(vg_name, [lv_name])
    lv = lvm.getLV(vg_name, lv_name)
    assert not lv.active


@requires_root
@pytest.mark.root
def test_lv_deactivate_in_use(tmp_storage):
    # TODO: This test does not work in active hosts.
    dev_size = 1 * GiB
    dev = tmp_storage.create_device(dev_size)
    vg_name = str(uuid.uuid4())
    lv_name = "in_use_lv"
    lvm.createVG(vg_name, [dev], "initial-tag", 128)
    lvm.createLV(vg_name, lv_name, 128)

    lv = lvm.getLV(vg_name, lv_name)
    assert lv.active

    with open(lvm.lvPath(vg_name, lv_name)):
        lvm.deactivateLVs(vg_name, [lv_name])
        lv = lvm.getLV(vg_name, lv_name)
        # Opened LV is in use and should not get deactivated.
        assert lv.active

    lvm.deactivateLVs(vg_name, [lv_name])
    lv = lvm.getLV(vg_name, lv_name)
    assert not lv.active


@pytest.mark.parametrize("in_use, error", [
    (True, ["Logical volume XYZ in use."]),
    (True, ["other warning", "Logical volume XYZ in use.", "other warning"]),
    (False, ["other warning"]),
    (False, [])
])
def test_in_use_exception(fake_devices, in_use, error):
    exc = se.LVMCommandError("cmd", 1, "out", error)
    assert exc.lv_in_use() == in_use


@requires_root
@pytest.mark.root
def test_lv_extend_reduce(tmp_storage):
    dev_size = 10 * GiB
    dev = tmp_storage.create_device(dev_size)
    vg_name = str(uuid.uuid4())
    lv_name = str(uuid.uuid4())

    lvm.createVG(vg_name, [dev], "initial-tag", 128)

    lvm.createLV(vg_name, lv_name, 1024)

    lvm.extendLV(vg_name, lv_name, 2048)

    lv = lvm.getLV(vg_name, lv_name)
    assert int(lv.size) == 2 * GiB

    # Extending LV to same does nothing.

    lvm.extendLV(vg_name, lv_name, 2048)

    lvm.invalidateVG(vg_name)
    lv = lvm.getLV(vg_name, lv_name)
    assert int(lv.size) == 2 * GiB

    # Extending LV to smaller size does nothing.

    lvm.extendLV(vg_name, lv_name, 1024)

    lvm.invalidateVG(vg_name)
    lv = lvm.getLV(vg_name, lv_name)
    assert int(lv.size) == 2 * GiB

    # Reducing active LV requires force.
    lvm.reduceLV(vg_name, lv_name, 1024, force=True)
    lv = lvm.getLV(vg_name, lv_name)
    assert int(lv.size) == 1 * GiB


@requires_root
@pytest.mark.root
def test_lv_extend_with_refresh(tmp_storage):
    dev_size = 10 * GiB
    dev = tmp_storage.create_device(dev_size)
    vg_name = str(uuid.uuid4())
    lv_name = str(uuid.uuid4())
    lvm.createVG(vg_name, [dev], "initial-tag", 128)
    lvm.createLV(vg_name, lv_name, 1024)

    lvm.extendLV(vg_name, lv_name, 2048, refresh=True)

    # LV extension should be visible immediately for the system without
    # refreshing LV.
    assert fsutils.size(lvm.lvPath(vg_name, lv_name)) == 2 * GiB


@requires_root
@pytest.mark.root
def test_lv_extend_without_refresh(tmp_storage):
    dev_size = 10 * GiB
    dev = tmp_storage.create_device(dev_size)
    vg_name = str(uuid.uuid4())
    lv_name = str(uuid.uuid4())
    lvm.createVG(vg_name, [dev], "initial-tag", 128)
    lvm.createLV(vg_name, lv_name, 1024)

    lvm.extendLV(vg_name, lv_name, 2048, refresh=False)

    lv_path = lvm.lvPath(vg_name, lv_name)
    # LV extension should not be visible to the system at this point and device
    # should has old size.
    assert fsutils.size(lv_path) == GiB

    lvm.refreshLVs(vg_name, [lv_name])

    # After LVM refresh, extension should be visible to the system.
    assert fsutils.size(lv_path) == 2 * GiB


@requires_root
@pytest.mark.root
def test_lv_refresh(tmp_storage):
    dev_size = 10 * GiB
    dev = tmp_storage.create_device(dev_size)
    vg_name = str(uuid.uuid4())
    lv_name = str(uuid.uuid4())
    lv_fullname = "{}/{}".format(vg_name, lv_name)

    lvm.createVG(vg_name, [dev], "initial-tag", 128)

    lvm.createLV(vg_name, lv_name, 1024)

    # Simulate extending the LV on the SPM.
    commands.run([
        "lvextend",
        "--devices", dev,
        "-L+1g",
        lv_fullname
    ])

    # Refreshing LV invalidates the cache to pick up changes from storage.
    lvm.refreshLVs(vg_name, [lv_name])
    lv = lvm.getLV(vg_name, lv_name)
    assert int(lv.size) == 2 * GiB

    # Simulate extending the LV on the SPM.
    commands.run([
        "lvextend",
        "--devices", dev,
        "-L+1g",
        lv_fullname
    ])

    # Activate active LV refreshes it.
    lvm.activateLVs(vg_name, [lv_name])
    lv = lvm.getLV(vg_name, lv_name)
    assert int(lv.size) == 3 * GiB


@requires_root
@pytest.mark.root
def test_bootstrap(tmp_storage):
    dev_size = 10 * GiB

    dev1 = tmp_storage.create_device(dev_size)
    vg1_name = str(uuid.uuid4())
    lvm.createVG(vg1_name, [dev1], "initial-tag", 128)

    dev2 = tmp_storage.create_device(dev_size)
    vg2_name = str(uuid.uuid4())
    lvm.createVG(vg2_name, [dev2], "initial-tag", 128)

    vgs = (vg1_name, vg2_name)

    for vg_name in vgs:
        # Create active lvs.
        for lv_name in ("skip", "prepared", "opened", "unused"):
            lvm.createLV(vg_name, lv_name, 1024)

        # Create links to prepared lvs.
        img_dir = os.path.join(sc.P_VDSM_STORAGE, vg_name, "img")
        os.makedirs(img_dir)
        os.symlink(
            lvm.lvPath(vg_name, "prepared"),
            os.path.join(img_dir, "prepared"))

    # Open some lvs during bootstrap.
    vg1_opened = lvm.lvPath(vg1_name, "opened")
    vg2_opened = lvm.lvPath(vg2_name, "opened")
    with open(vg1_opened), open(vg2_opened):

        lvm.bootstrap(skiplvs=["skip"])

        # Lvs in skiplvs, prepared lvs, and opened lvs should be active.
        for vg_name in vgs:
            for lv_name in ("skip", "prepared", "opened"):
                lv = lvm.getLV(vg_name, lv_name)
                assert lv.active

        # Unused lvs should not be active.
        for vg_name in vgs:
            lv = lvm.getLV(vg_name, "unused")
            assert not lv.active


@requires_root
@pytest.mark.root
@pytest.mark.skipif(
    testing.on_ovirt_ci() or testing.on_travis_ci(),
    reason="dm-mirror kernel module missing - pvmove fails")
def test_pv_move(tmp_storage):
    dev_size = 1 * GiB
    dev1 = tmp_storage.create_device(dev_size)
    dev2 = tmp_storage.create_device(dev_size)
    vg_name = str(uuid.uuid4())
    lv_name = str(uuid.uuid4())
    data = uuid.uuid4()

    lvm.createVG(vg_name, [dev1, dev2], "initial-tag", 128)
    lvm.createLV(vg_name, lv_name, 512, device=dev1)
    lv_path = lvm.lvPath(vg_name, lv_name)

    # Write test data to LV.
    with open(lv_path, "wb") as f:
        f.write(data.bytes)

    # Deactivate LV to ensure data is written to storage.
    lvm.deactivateLVs(vg_name, [lv_name])

    # Run pvmove to migrate data to second device.
    lvm.movePV(vg_name, dev1, [dev2])

    # Remove now unused PV from the volume group.
    lvm.reduceVG(vg_name, dev1)

    # Activate LV so it can be opened for reading.
    lvm.activateLVs(vg_name, [lv_name])

    # Check data presence on LV which now uses the second device.
    with open(lv_path, "rb") as f:
        assert f.read(len(data.bytes)) == data.bytes

    # Check pv moved to new device and previous device is not used.
    lv = lvm.getLV(vg_name, lv_name)
    assert dev2 in lv.devices
    assert dev1 not in lv.devices


@pytest.fixture
def stale_lv(tmp_storage):
    dev_size = 1 * 1024**3
    dev = tmp_storage.create_device(dev_size)

    vg_name = str(uuid.uuid4())
    good_lv_name = "good"
    stale_lv_name = "stale"

    # Create VG with 2 lvs.
    lvm.createVG(vg_name, [dev], "initial-tag", 128)
    for lv_name in (good_lv_name, stale_lv_name):
        lvm.createLV(vg_name, lv_name, 128, activate=False)

    # Reload the cache.
    good_lv = lvm.getLV(vg_name, good_lv_name)
    stale_lv = lvm.getLV(vg_name, stale_lv_name)

    # Simulate removal of the second LV on another host, leaving stale LV in
    # the cache.
    commands.run([
        "lvremove",
        "--devices", dev,
        f"{vg_name}/{stale_lv_name}",
    ])

    # The cache still keeps both lvs.
    assert lvm._lvminfo._lvs == {
        (vg_name, good_lv_name): good_lv,
        (vg_name, stale_lv_name): stale_lv,
    }

    return vg_name, good_lv_name, stale_lv_name


@requires_root
@pytest.mark.root
def test_lv_stale_cache_one(stale_lv):
    vg_name, good_lv_name, stale_lv_name = stale_lv

    # Until cache is invalidated, return lvs from cache.

    good_lv = lvm.getLV(vg_name, good_lv_name)
    assert good_lv.name == good_lv_name

    stale_lv = lvm.getLV(vg_name, stale_lv_name)
    assert stale_lv.name == stale_lv_name


@requires_root
@pytest.mark.root
def test_lv_stale_cache_all(stale_lv):
    vg_name, good_lv_name, stale_lv_name = stale_lv

    # LVs always skip the cache.
    # TODO: Use cache if VG did not change.

    lv_names = {lv.name for lv in lvm.getAllLVs(vg_name)}
    assert good_lv_name in lv_names
    assert stale_lv_name not in lv_names


@requires_root
@pytest.mark.root
def test_lv_stale_reload_one_stale(stale_lv):
    vg_name, good_lv_name, stale_lv_name = stale_lv

    # Invalidate all lvs in single vg.
    lvm.invalidateVG(vg_name, invalidateLVs=True)

    # The good lv is still in the cache.
    lv = lvm.getLV(vg_name, good_lv_name)
    assert lv.name == good_lv_name

    # The stale lv should be removed.
    with pytest.raises(se.LogicalVolumeDoesNotExistError):
        lvm.getLV(vg_name, stale_lv_name)


@requires_root
@pytest.mark.root
def test_lv_stale_reload_one_clear(stale_lv):
    vg_name, good_lv_name, stale_lv_name = stale_lv

    # Drop all cache.
    lvm.invalidateCache()

    # The good lv is still in the cache.
    lv = lvm.getLV(vg_name, good_lv_name)
    assert lv.name == good_lv_name

    # The stale lv should be removed.
    with pytest.raises(se.LogicalVolumeDoesNotExistError):
        lvm.getLV(vg_name, stale_lv_name)


@requires_root
@pytest.mark.root
def test_lv_stale_reload_all_stale(stale_lv):
    vg_name, good_lv_name, stale_lv_name = stale_lv

    # Invalidate all lvs in single vg.
    lvm.invalidateVG(vg_name, invalidateLVs=True)

    # Only the good lv is reported.
    lvs = [lv.name for lv in lvm.getAllLVs(vg_name)]
    assert lvs == [good_lv_name]


@requires_root
@pytest.mark.root
def test_lv_stale_reload_all_clear(stale_lv):
    vg_name, good_lv_name, stale_lv_name = stale_lv

    # Drop all cache.
    lvm.invalidateCache()

    # Only the good lv is reported.
    lvs = [lv.name for lv in lvm.getAllLVs(vg_name)]
    assert lvs == [good_lv_name]


def test_lv_reload_error_one(fake_devices):
    fake_runner = FakeRunner(rc=5, err=b"Fake lvm error")
    lc = lvm.LVMCache(fake_runner)

    pv1 = make_pv(pv_name="/dev/mapper/pv1", vg_name="vg-name")
    other_lv = make_lv(lv_name="other-lv", pvs=[pv1.name], vg_name="vg-name")
    lc._lvs = {("vg-name", "other-lv"): other_lv}

    # Should raise, but currently return None.
    assert lc.getLv("vg-name", "lv-name") is None

    # Other lv is not affected since it was not a stale.
    assert lc._lvs == {("vg-name", "other-lv"): other_lv}


def test_lv_reload_error_one_stale(fake_devices):
    fake_runner = FakeRunner(rc=5, err=b"Fake lvm error")
    lc = lvm.LVMCache(fake_runner)
    lc._lvs = {
        ("vg-name", "lv-name"): lvm.Stale("lv-name"),
        ("vg-name", "other-lv"): lvm.Stale("other-lv"),
    }
    lv = lc.getLv("vg-name", "lv-name")

    # Mark lv as unreadable. Because we always reload all lvs, the other lvs is
    # also marked as unreadable.
    assert lc._lvs == {
        ("vg-name", "lv-name"): lvm.Unreadable("lv-name"),
        ("vg-name", "other-lv"): lvm.Unreadable("other-lv"),
    }

    # Report the unreadbale lv.
    assert lv.name == "lv-name"
    assert isinstance(lv, lvm.Unreadable)


def test_lv_reload_error_one_stale_other_vg(fake_devices):
    fake_runner = FakeRunner(rc=5, err=b"Fake lvm error")
    lc = lvm.LVMCache(fake_runner)
    lc._lvs = {
        ("vg-name", "lv-name"): lvm.Stale("lv-name"),
        ("other-vg", "other-lv"): lvm.Stale("other-lv"),
    }
    lc.getLv("vg-name", "lv-name")

    # Should not affect other vg lvs.
    other_lv = lc._lvs[("other-vg", "other-lv")]
    assert not isinstance(other_lv, lvm.Unreadable)


def test_lv_reload_error_all(fake_devices):
    fake_runner = FakeRunner(rc=5, err=b"Fake lvm error")
    lc = lvm.LVMCache(fake_runner)
    assert lc.getAllLvs("vg-name") == []


def test_lv_reload_error_all_other_vg(fake_devices):
    fake_runner = FakeRunner(rc=5, err=b"Fake lvm error")
    lc = lvm.LVMCache(fake_runner)
    lc._lvs = {("vg-name", "lv-name"): lvm.Stale("lv-name")}
    lvs = lc.getAllLvs("vg-name")

    # Mark lv as unreadable.
    assert lc._lvs == {("vg-name", "lv-name"): lvm.Unreadable("lv-name")}

    # Currnetly we don't report stales or unreadables lvs. This is not
    # consistent with getLv(vg_name, lv_name).
    assert lvs == []


def test_lv_reload_error_all_stale_other_vgs(fake_devices):
    fake_runner = FakeRunner(rc=5, err=b"Fake lvm error")
    lc = lvm.LVMCache(fake_runner)
    lc._lvs = {
        ("vg-name", "lv-name"): lvm.Stale("lv-name"),
        ("other-vg", "other-lv"): lvm.Stale("other-lv"),
    }
    lc.getAllLvs("vg-name")

    # Should not affect other vg lvs.
    other_lv = lc._lvs[("other-vg", "other-lv")]
    assert not isinstance(other_lv, lvm.Unreadable)


def test_lv_reload_fresh_vg(fake_devices):
    fake_runner = FakeRunner()
    lc = lvm.LVMCache(fake_runner, cache_lvs=True)
    pv1 = make_pv(pv_name="/dev/mapper/pv1", vg_name="vg1")
    lv1 = make_lv(lv_name="lv1", pvs=[pv1.name], vg_name="vg1")

    # vg1's lvs are fresh, vg2's lvs were invalidated.
    lc._freshlv = {"vg1", "vg2"}
    lc._lvs = {
        ("vg1", "lv1"): lv1,
        ("vg2", "lv2"): lvm.Stale("lv2"),
    }

    assert not lc._lvs_needs_reload("vg1")
    assert lc._lvs_needs_reload("vg2")

    # getAllLvs for vg1 should use cache without reload lvs.
    assert lc.getAllLvs("vg1") == [lv1]
    assert not lc._lvs_needs_reload("vg1")
    assert lc._lvs_needs_reload("vg2")

    # getAllLvs for vg2 should reload lvs.
    assert lc.getAllLvs("vg2") == []
    assert not lc._lvs_needs_reload("vg1")
    assert not lc._lvs_needs_reload("vg2")


def test_lv_reload_for_stale_vg(fake_devices):
    fake_runner = FakeRunner()
    lc = lvm.LVMCache(fake_runner, cache_lvs=True)

    assert lc._lvs_needs_reload("vg")

    # getAllLvs call should call reload lvs.
    lc.getAllLvs("vg")
    assert not lc._lvs_needs_reload("vg")


@requires_root
@pytest.mark.root
def test_retry_with_wider_filter(tmp_storage):
    # Force reload of the cache. The system does not know about any device at
    # this point.
    clear_stats()
    lvm.getAllPVs()
    check_stats(hits=0, misses=1)

    # Create a device - this device in not the lvm cached filter yet.
    dev = tmp_storage.create_device(10 * GiB)

    # We run vgcreate with explicit devices argument, so the filter is correct
    # and it succeeds.
    vg_name = str(uuid.uuid4())
    lvm.createVG(vg_name, [dev], "initial-tag", 128)

    # Calling getAllPVs() have cache miss since createVG invalidates the PVs.
    clear_stats()
    lvm.getAllPVs()
    check_stats(hits=0, misses=1)

    # Second call for getAllPVs() adds cache hit since the new PV was reloaded.
    lvm.getAllPVs()
    check_stats(hits=1, misses=1)

    # The cached filter is stale at this point, and so is the vg metadata in
    # the cache. Running "vgs vg-name" fails because of the stale filter, so we
    # invalidate the filter and run it again.
    vg = lvm.getVG(vg_name)
    assert vg.pv_name == (dev,)


@requires_root
@pytest.mark.root
def test_get_lvs_after_sd_refresh(tmp_storage):
    dev_size = 1 * GiB
    dev1 = tmp_storage.create_device(dev_size)
    dev2 = tmp_storage.create_device(dev_size)
    vg1_name = str(uuid.uuid4())
    vg2_name = str(uuid.uuid4())

    # Create two VGs and LVs per each.
    lvm.createVG(vg1_name, [dev1], "initial-tag", 128)
    lvm.createVG(vg2_name, [dev2], "initial-tag", 128)

    lvm.createLV(vg1_name, "lv1", 128, activate=False)
    lvm.createLV(vg2_name, "lv2", 128, activate=False)

    # Make sure that LVs are in LVM cache for both VGs.
    lv1 = lvm.getAllLVs(vg1_name)[0]
    lv2 = lvm.getAllLVs(vg2_name)[0]

    # Simulate refresh SD.
    lvm.invalidateCache()

    # Reload lvs for vg1.
    assert lvm.getAllLVs(vg1_name) == [lv1]

    # Reload lvs for vg2.
    assert lvm.getAllLVs(vg2_name) == [lv2]


def test_normalize_args():
    assert lvm.normalize_args(u"arg") == [u"arg"]
    assert lvm.normalize_args("arg") == [u"arg"]

    assert lvm.normalize_args(("arg1", "arg2")) == (u"arg1", u"arg2")
    assert lvm.normalize_args((u"arg1", u"arg2")) == (u"arg1", u"arg2")
    assert lvm.normalize_args(["arg1", "arg2"]) == [u"arg1", u"arg2"]
    assert lvm.normalize_args([u"arg1", u"arg2"]) == [u"arg1", u"arg2"]

    assert list(lvm.normalize_args(iter(("arg1", "arg2")))) == [
        u"arg1", u"arg2"]
    assert list(lvm.normalize_args(iter((u"arg1", u"arg2")))) == [
        u"arg1", u"arg2"]
    assert list(lvm.normalize_args(iter(["arg1", "arg2"]))) == [
        u"arg1", u"arg2"]
    assert list(lvm.normalize_args(iter([u"arg1", u"arg2"]))) == [
        u"arg1", u"arg2"]


@pytest.mark.parametrize("input,expected", [
    (
        'Physical volume "/dev/mapper/36001404" successfully created',
        ["/dev/mapper/36001404"]
    ),
    (
        """
        Physical volume "/dev/mapper/36001404" successfully created
        Physical volume "/dev/mapper/36001405" successfully created""",
        ["/dev/mapper/36001404", "/dev/mapper/36001405"]
    ),
    (
        "Run 'pvcreate --help' for more information",
        []
    )
])
def test_pv_name_reg_exp(input, expected):
    assert lvm.re_pvName.findall(input) == expected


def make_lv(lv_name, vg_name, pvs, size="128"):
    return lvm.LV.fromlvm(
        "uuid",
        lv_name,
        vg_name,
        "-wi-------",
        size,
        "0",
        pvs,
        "IU_image-uid,PU_00000000,MD_1",
    )


def make_pv(pv_name, vg_name):
    return lvm.PV.fromlvm(
        "uuid",
        pv_name,
        "508660023296",
        vg_name,
        "vg_uuid",
        "1048576",
        "121274",
        "93305",
        "1",
        "508661071872",
        "1",
    )


def make_vg(vg_name, pvs):
    return lvm.VG.fromlvm(
        "uuid",
        vg_name,
        "wz--n-",
        "508660023296",
        "117310488576",
        "4194304",
        "121274",
        "27969",
        "",
        "1044480",
        "519168",
        "3",
        "1",
        pvs,
    )


def clear_stats():
    lvm.clear_stats()


def check_stats(hits, misses):
    stats = lvm.cache_stats()
    assert stats["hits"] == hits
    assert stats["misses"] == misses
