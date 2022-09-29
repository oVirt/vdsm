# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import
from __future__ import division

import glob
import os
import uuid

import pytest

from vdsm.common import cmdutils
from vdsm.common import commands
from vdsm.constants import EXT_DMSETUP
from vdsm.storage import devicemapper
from vdsm.storage import dmsetup
from vdsm.storage.devicemapper import DMPATH_PREFIX
from vdsm.storage.devicemapper import Error
from vdsm.storage.devicemapper import PathStatus

from . marks import requires_root, broken_on_ci

FAKE_DMSETUP_STATUS = [
    ("360014053d0b83eff3d347c48509fc426", " 0 104857600 multipath 2 0 1 0 3 2 E 0 1 1 67:16 F 4 0 E 0 1 1 65:240 A 84 0 E 0 1 1 66:64 A 39 0"),  # NOQA: E501 (long line)
    ("3600140543cb8d7510d54f058c7b3f7ec", " 0 209715200 multipath 2 0 1 0 3 1 A 0 1 1 65:224 A 0 0 E 0 1 1 65:160 A 0 0 E 0 1 1 66:176 F 1 0"),  # NOQA: E501 (long line)
]

broken_on_ci = broken_on_ci.with_args(
    reason="device mapper doesn't work properly in containers")


class FakeDmSetupStatus(object):

    def __init__(self):
        self.lines = []

    def __call__(self, *args, **kwargs):
        for name, status in self.lines:
            yield name, status


@pytest.fixture
def fake_dmsetup_status(monkeypatch):
    monkeypatch.setattr(dmsetup, "status", FakeDmSetupStatus())


@pytest.fixture
def zero_dm_device():
    """
    Create test device mapper mapping backed by zero target. Zero target is
    used for tests and it acts similarly to /dev/zero - writes are discarded
    and reads return nothing (binary zero). For now, the size of the device
    is fixed to 1 GiB (1 GiB = 2097152 * 512 B sectors).

    The tests using this fixture need to be run with the root privileges, as
    dmsetup utility requires root.
    """
    device_name = str(uuid.uuid4())

    cmd = [EXT_DMSETUP, "create", device_name, "--table", "0 2097152 zero"]
    try:
        commands.run(cmd)
    except cmdutils.Error as e:
        raise Error("Could not create mapping {!r}: {}".format(device_name, e))

    try:
        yield device_name
    finally:
        # If the test didn't do the cleanup, remove the mapping.
        device_path = "{}{}".format(DMPATH_PREFIX, device_name)
        if os.path.exists(device_path):
            cmd = [EXT_DMSETUP, "remove", device_name]
            try:
                commands.run(cmd)
            except cmdutils.Error as e:
                raise Error(
                    "Could not remove mapping {!r}: {}".format(device_name, e))


def test_dm_status(fake_dmsetup_status):
    dmsetup.status.lines = FAKE_DMSETUP_STATUS

    res = devicemapper.multipath_status()
    expected = {
        '360014053d0b83eff3d347c48509fc426':
            [
                PathStatus('67:16', 'F'),
                PathStatus('65:240', 'A'),
                PathStatus('66:64', 'A')
            ],
        '3600140543cb8d7510d54f058c7b3f7ec':
            [
                PathStatus('65:224', 'A'),
                PathStatus('65:160', 'A'),
                PathStatus('66:176', 'F')
            ]
    }

    assert res == expected


def test_dm_status_no_device(fake_dmsetup_status):
    assert devicemapper.multipath_status() == {}


def test_get_paths_status(monkeypatch, fake_dmsetup_status):
    monkeypatch.setattr(
        devicemapper, "device_name", lambda major_minor: major_minor)
    dmsetup.status.lines = FAKE_DMSETUP_STATUS

    res = devicemapper.getPathsStatus()

    expected = {
        "67:16": "failed",
        "65:240": "active",
        "66:64": "active",
        "65:224": "active",
        "65:160": "active",
        "66:176": "failed",
    }
    assert res == expected


def test_get_paths_status_no_device(fake_dmsetup_status):
    assert devicemapper.getPathsStatus() == {}


@broken_on_ci
@requires_root
@pytest.mark.root
def test_remove_mapping(zero_dm_device):
    device_path = "{}{}".format(DMPATH_PREFIX, zero_dm_device)
    assert os.path.exists(device_path)

    devicemapper.removeMapping(zero_dm_device)
    assert not os.path.exists(device_path)


@broken_on_ci
@requires_root
@pytest.mark.root
def test_dm_id(zero_dm_device):
    # Resolve the dm link and get dm name of the device.
    device_path = "{}{}".format(DMPATH_PREFIX, zero_dm_device)
    real_path = os.path.realpath(device_path)
    device_name = real_path.split("/")[-1]

    dm_id = devicemapper.getDmId(zero_dm_device)
    assert device_name == dm_id

    # Test also devicemapper.resolveDevName() as it returns device_name
    # directly.
    resolved_name = devicemapper.resolveDevName(device_name)
    assert device_name == resolved_name

    # Or returns dm_id().
    resolved_name = devicemapper.resolveDevName(zero_dm_device)
    assert device_name == resolved_name


@broken_on_ci
@requires_root
@pytest.mark.root
def test_dev_name(zero_dm_device):
    dm_id = devicemapper.getDmId(zero_dm_device)
    device_name = devicemapper.getDevName(dm_id)
    assert zero_dm_device == device_name


@broken_on_ci
@requires_root
@pytest.mark.root
def test_is_virtual_device(zero_dm_device):
    dm_id = devicemapper.getDmId(zero_dm_device)
    assert devicemapper.isVirtualDevice(dm_id)


@broken_on_ci
@requires_root
@pytest.mark.root
def test_is_block_device(zero_dm_device):
    dm_id = devicemapper.getDmId(zero_dm_device)
    assert devicemapper.isBlockDevice(dm_id)


@broken_on_ci
@requires_root
@pytest.mark.root
def test_is_dm_device(zero_dm_device):
    dm_id = devicemapper.getDmId(zero_dm_device)
    assert devicemapper.isDmDevice(dm_id)


@broken_on_ci
@requires_root
@pytest.mark.root
def test_get_all_mapped_devices(zero_dm_device):
    devices = devicemapper.getAllMappedDevices()
    assert zero_dm_device in devices


@broken_on_ci
@requires_root
@pytest.mark.root
def test_get_all_slaves(zero_dm_device):
    slaves = devicemapper.getAllSlaves()
    assert zero_dm_device in slaves
    # Zero device mapping has no slaves.
    assert slaves[zero_dm_device] == []


@broken_on_ci
@requires_root
@pytest.mark.root
def test_get_slaves(zero_dm_device):
    slaves = devicemapper.getSlaves(zero_dm_device)
    # Zero device mapping has no slaves.
    assert slaves == []


@broken_on_ci
@requires_root
@pytest.mark.root
def test_get_holders(zero_dm_device):
    holders = devicemapper.getHolders(zero_dm_device)
    # Zero device mapping has no holders.
    assert holders == []


def test_block_device_name():
    devs = glob.glob("/sys/block/*/dev")
    dev_name = os.path.basename(os.path.dirname(devs[0]))
    with open(devs[0], 'r') as f:
        major_minor = f.readline().rstrip()
        assert devicemapper.device_name(major_minor) == dev_name
