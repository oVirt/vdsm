#
# Copyright 2017 Red Hat, Inc.
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

import logging
import os

import pytest

from vdsm.storage import lvmfilter
from vdsm.storage.lvmfilter import MountInfo

FAKE_LSBLK = os.path.join(os.path.dirname(__file__), "fake-lsblk")
FAKE_DEVICES = ("/dev/sda2",)

log = logging.getLogger("test")


@pytest.mark.parametrize("plat,expected", [
    ("rhel74", [
        MountInfo("/dev/mapper/vg0-lv_home", "/home", FAKE_DEVICES),
        MountInfo("/dev/mapper/vg0-lv_root", "/", FAKE_DEVICES),
        MountInfo("/dev/mapper/vg0-lv_swap", "[SWAP]", FAKE_DEVICES),
    ]),
    ("fedora", [
        MountInfo("/dev/mapper/fedora-home", "/home", FAKE_DEVICES),
        MountInfo("/dev/mapper/fedora-root", "/", FAKE_DEVICES),
        MountInfo("/dev/mapper/fedora-swap", "[SWAP]", FAKE_DEVICES),
    ]),
])
def test_find_lvm_mounts(monkeypatch, plat, expected):
    # Monkeypatch the module to run the fake-lsblk returning data collected on
    # on real platform.
    monkeypatch.setattr(lvmfilter, "LSBLK", FAKE_LSBLK)
    monkeypatch.setenv("FAKE_STDOUT", FAKE_LSBLK + "." + plat + ".out")

    # Monkeypatch lvm helper, requires real devices on the host. We are testing
    # the helpers in other tests when running as root.
    monkeypatch.setattr(lvmfilter, "find_lv_devices", lambda x: FAKE_DEVICES)

    mounts = lvmfilter.find_lvm_mounts()
    log.info("found mounts %s", mounts)
    assert mounts == expected


def test_build_filter():
    mounts = [
        MountInfo("/dev/mapper/vg0-lv_home",
                  "/home",
                  ["/dev/sda2", "/dev/sdb2"]),
        MountInfo("/dev/mapper/vg0-lv_root",
                  "/",
                  ["/dev/sda2"]),
        MountInfo("/dev/mapper/vg0-lv_swap",
                  "[SWAP]",
                  ["/dev/sda2"]),
    ]
    lvm_filter = lvmfilter.build_filter(mounts)
    assert lvm_filter == ["a|^/dev/sda2$|", "a|^/dev/sdb2$|", "r|.*|"]


def test_build_filter_no_mounts():
    lvm_filter = lvmfilter.build_filter([])
    assert lvm_filter == ["r|.*|"]


def test_format_option():
    lvm_filter = ["a|^/dev/sda2$|", "r|.*|"]
    expected = 'filter = [ "a|^/dev/sda2$|", "r|.*|" ]'
    assert lvmfilter.format_option(lvm_filter) == expected


@pytest.mark.skipif(os.geteuid() != 0, reason="Requires root")
def test_real_find_lvm_mounts():
    mounts = lvmfilter.find_lvm_mounts()
    # This will return different results on any host, but we expect to find a
    # logical volume mounted at / with non empty devices list.
    for mnt in mounts:
        if mnt.mountpoint == "/":
            assert mnt.devices != []


@pytest.mark.skipif(os.geteuid() != 0, reason="Requires root")
def test_real_build_filter():
    mounts = lvmfilter.find_lvm_mounts()
    lvm_filter = lvmfilter.build_filter(mounts)
    log.info("LVM filter for this host:\n%r", lvm_filter)
    for mnt in mounts:
        for dev in mnt.devices:
            match = "'a|^%s$|'" % dev
            assert match in lvm_filter
