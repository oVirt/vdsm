#
# Copyright 2009-2017 Red Hat, Inc.
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
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA
#
# Refer to the README and COPYING files for full details of the license
#

from __future__ import print_function

import glob
import os
import pprint
import threading

import pytest

from vdsm.storage import udev

import loopback

EVENT = udev.MultipathEvent(type=udev.MPATH_REMOVED,
                            mpath_uuid="fake-uuid-3",
                            path=None,
                            valid_paths=None)


class FakeDevice(dict):
    @property
    def action(self):
        return self["action"]

DEVICE = FakeDevice(DM_UUID="mpath-fake-uuid-3",
                    action="remove")


class Callback(object):
    def __init__(self):
        self.calls = []

    def __call__(self, event):
        self.calls.append(event)


def fake_block_device_name(dev):
    return "sda"


def test_start():
    mp_listener = udev.MultipathListener()
    try:
        mp_listener.start()
    except Exception as e:
        pytest.fail("Unexpected Exception: %s", e)
    else:
        mp_listener.stop()


def test_start_twice():
    mp_listener = udev.MultipathListener()
    mp_listener.start()
    with pytest.raises(AssertionError):
        mp_listener.start()
    mp_listener.stop()


def test_stop():
    mp_listener = udev.MultipathListener()
    mp_listener.start()
    try:
        mp_listener.stop()
    except Exception as e:
        pytest.fail("Unexpected Exception: %s", e)


def test_stop_twice():
    mp_listener = udev.MultipathListener()
    mp_listener.start()
    mp_listener.stop()
    try:
        mp_listener.stop()
    except Exception as e:
        pytest.fail("Unexpected Exception: %s", e)


@pytest.mark.parametrize("device,expected", [
    (
        # Multipath path is restored
        FakeDevice(
            action="change",
            DM_ACTION="PATH_REINSTATED",
            DM_UUID="mpath-fake-uuid-1",
            DM_PATH="sda",
            DM_NR_VALID_PATHS="1"),
        udev.MultipathEvent(
            type=udev.PATH_REINSTATED,
            mpath_uuid="fake-uuid-1",
            path="sda",
            valid_paths=1)
    ),
    (
        # Multipath path has failed
        FakeDevice(
            action="change",
            DM_ACTION="PATH_FAILED",
            DM_UUID="mpath-fake-uuid-2",
            DM_PATH="66:32",
            DM_NR_VALID_PATHS="4"),
        udev.MultipathEvent(
            type=udev.PATH_FAILED,
            mpath_uuid="fake-uuid-2",
            path="sda",
            valid_paths=4)
    ),
    (
        # Multipath device has been removed
        FakeDevice(
            action="remove",
            DM_UUID="mpath-fake-uuid-3"),
        udev.MultipathEvent(
            type=udev.MPATH_REMOVED,
            mpath_uuid="fake-uuid-3",
            path=None,
            valid_paths=None)
    ),
])
def test_report_events(device, expected):
    mp_listener = udev.MultipathListener()
    # Avoid accessing non-existing devices
    mp_listener._block_device_name = fake_block_device_name
    cb = Callback()
    mp_listener.register(cb)
    mp_listener._callback(device)

    assert cb.calls == [expected]


@pytest.mark.parametrize("device", [
    # the DM_UUID does not start with "mpath"
    FakeDevice(action="change",
               DM_UUID="usb-fake-uuid-1"),
    # the DM_ACTION is not supported
    FakeDevice(action="change",
               DM_UUID="mpath-fake-uuid-2",
               DM_ACTION="PATH_DISINTEGRATED",
               DM_NR_VALID_PATHS="4"),
    # the "action" is not supported
    FakeDevice(action="update",
               DM_UUID="mpath-fake-uuid-3")
])
def test_filter_event(device):
    mp_listener = udev.MultipathListener()
    cb = Callback()
    mp_listener.register(cb)
    mp_listener._callback(device)

    assert cb.calls == []


def test_cb_unregistered():
    mp_listener = udev.MultipathListener()
    cb = Callback()
    mp_listener.register(cb)
    mp_listener._callback(DEVICE)
    assert cb.calls == [EVENT]
    mp_listener.unregister(cb)
    mp_listener._callback(DEVICE)
    assert cb.calls == [EVENT]


def test_cb_not_registered():
    mp_listener = udev.MultipathListener()
    with pytest.raises(AssertionError):
        mp_listener.unregister(None)


def test_cb_already_registered():
    mp_listener = udev.MultipathListener()
    mp_listener.register(None)
    with pytest.raises(AssertionError):
        mp_listener.register(None)


def test_cb_exception():
    mp_listener = udev.MultipathListener()

    def bad_cb(event):
        raise RuntimeError("bad callback")

    mp_listener.register(bad_cb)
    good_cb = Callback()
    mp_listener.register(good_cb)
    mp_listener._callback(DEVICE)
    assert good_cb.calls == [EVENT]

    # Call again, after registering in a different order
    mp_listener = udev.MultipathListener()
    mp_listener.register(bad_cb)
    cb = Callback()
    mp_listener.register(cb)
    mp_listener._callback(DEVICE)
    assert cb.calls == [EVENT]


def test_add_cb2_from_cb1():
    mp_listener = udev.MultipathListener()
    cb2 = Callback()

    def cb1(device):
        mp_listener.register(cb2)

    mp_listener.register(cb1)
    mp_listener._callback(DEVICE)
    mp_listener._callback(DEVICE)
    assert cb2.calls == [EVENT]


def test_remove_cb1_from_cb2():
    mp_listener = udev.MultipathListener()

    calls = []

    def cb(e):
        calls.append(e)
        mp_listener.unregister(cb)

    mp_listener.register(cb)
    mp_listener._callback(DEVICE)
    mp_listener._callback(DEVICE)

    assert calls == [EVENT]


def test_failing_event():
    fd = FakeDevice(
        action="change",
        DM_ACTION="PATH_REINSTATED",
        DM_UUID="mpath-fake-uuid-1",
        DM_PATH="sda",
        DM_NR_VALID_PATHS="sfsdfs")
    mp_listener = udev.MultipathListener()
    cb = Callback()
    mp_listener.register(cb)
    mp_listener._callback(fd)

    mp_listener._callback(DEVICE)
    assert cb.calls == [EVENT]


def test_block_device_name():
    devs = glob.glob("/sys/block/*/dev")
    dev_name = os.path.basename(os.path.dirname(devs[0]))
    with open(devs[0], 'r') as f:
        major_minor = f.readline().rstrip()
        mp_listener = udev.MultipathListener()
        assert mp_listener._block_device_name(major_minor) == dev_name


@pytest.mark.xfail(
    os.environ.get("OVIRT_CI") or os.environ.get("TRAVIS_CI"),
    reason="Requires real env")
@pytest.mark.skipif(os.geteuid() != 0, reason="Requires root")
def test_loopback_event(tmpdir):
    mp_listener = udev.MultipathListener()
    received = threading.Event()
    devices = []

    def callback(device):
        pprint.pprint({k: device[k] for k in device})
        devices.append(device)
        received.set()

    mp_listener._callback = callback
    mp_listener.start()
    try:
        # Create a backing file
        filename = str(tmpdir.join("file"))
        with open(filename, "wb") as f:
            f.truncate(1024**2 * 10)

        # Create and remove a loop device
        with loopback.Device(filename) as loop:
            print("Created a loop device at %r" % loop.path)
            if not received.wait(1):
                raise RuntimeError("Timeout receiving event")

            # We expect an event about our loop device
            assert devices[0].get("DEVNAME") == loop.path
    finally:
        mp_listener.stop()
