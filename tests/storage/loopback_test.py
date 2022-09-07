# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import
from __future__ import division
import io
import os

import pytest

from vdsm.common.units import GiB
from vdsm.storage import constants as sc
from vdsm.storage import multipath

import testing

from . import loopback
from . marks import requires_root
from . marks import requires_loopback_sector_size


BEFORE = b"a" * 10
AFTER = b"b" * 10


@requires_root
@pytest.mark.root
@pytest.mark.parametrize("sector_size", [
    None,
    pytest.param(sc.BLOCK_SIZE_512, marks=requires_loopback_sector_size),
    pytest.param(sc.BLOCK_SIZE_4K, marks=[
        requires_loopback_sector_size,
        pytest.mark.xfail(
            testing.on_ovirt_ci() or testing.on_travis_ci(),
            reason="fails randomly to create loop device with 4k sector "
                   "size, only in CI - needs investigation",
            strict=False),
    ]),
])
def test_with_device(tmpdir, sector_size):
    filename = str(tmpdir.join("file"))
    prepare_backing_file(filename)
    with loopback.Device(filename, sector_size=sector_size) as device:
        assert device.is_attached()
        check_device(device)
        block_size, _ = multipath.getDeviceBlockSizes(device.path)
        expected = sector_size if sector_size else sc.BLOCK_SIZE_512
        assert block_size == expected
    assert not device.is_attached()
    check_backing_file(filename)


@requires_root
@pytest.mark.root
def test_attach_detach_manually(tmpdir):
    filename = str(tmpdir.join("file"))
    prepare_backing_file(filename)
    device = loopback.Device(filename)
    device.attach()
    try:
        assert device.is_attached()
        check_device(device)
    finally:
        device.detach()
    assert not device.is_attached()
    check_backing_file(filename)


@requires_root
@pytest.mark.root
@pytest.mark.stress
def test_many_devices(tmpdir):
    filename = str(tmpdir.join("file"))
    prepare_backing_file(filename)
    for i in range(300):
        with loopback.Device(filename) as device:
            assert device.is_attached()
        assert not device.is_attached()


def prepare_backing_file(filename):
    with io.open(filename, "wb") as f:
        f.truncate(GiB)
        f.write(BEFORE)


def check_device(device):
    with io.open(device.path, "r+b", buffering=0) as f:
        assert f.read(len(BEFORE)) == BEFORE
        f.write(AFTER)
        os.fsync(f.fileno())


def check_backing_file(filename):
    expected = BEFORE + AFTER
    with io.open(filename, "rb") as f:
        assert f.read(len(expected)) == expected
