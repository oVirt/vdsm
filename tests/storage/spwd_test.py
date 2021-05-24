#
# Copyright 2021 Red Hat, Inc.
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
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA  02110-1301 USA
#
# Refer to the README and COPYING files for full details of the license
#

import errno
import threading

import sanlock
import pytest

from vdsm.common.units import MiB
from vdsm.storage import clusterlock
from vdsm.storage import exception as se
from vdsm.storage import spwd


class WatchdogCallback:

    def __init__(self):
        self.ready = threading.Event()
        self.done = threading.Event()

    def __call__(self):
        """
        Called from watchdog loop to wake up the test.
        """
        self.done.set()
        self.ready.wait()

    def wait(self):
        """
        Wait until the watchdog invoke __call__.
        """
        self.ready.clear()
        if not self.done.wait(2):
            raise RuntimeError("Timeout waiting for watchdog")

    def resume(self):
        """
        Make __call__ return, resuming the watchdog.
        """
        self.done.clear()
        self.ready.set()


class FakeMaster:

    sdUUID = "master-domain-uuid"

    def __init__(self):
        self.resources = [
            # External lease in another domain.
            {
                "lockspace": "other-domain-uuid",
                "resource": "vm-id",
                "version": 1,
                "disks": [("/other/xleases", 100 * MiB)],
            },

            # A volume lease in the master domain.
            {
                "lockspace": "master-domain-uuid",
                "resource": "volume-uuid-2",
                "version": 1,
                "disks": [("/master/leases", 100 * MiB)],
            },

            # The cluster lease in the master domain.
            {
                "lockspace": "master-domain-uuid",
                "resource": "SDM",
                "version": 1,
                "disks": [("/master/leases", MiB)],
            },
        ]
        self.error = None

    def inquireClusterLock(self):
        if self.error:
            raise self.error

        return self.resources

    def getClusterLease(self):
        return clusterlock.Lease("SDM", "/master/leases", MiB)


class Panic(Exception):
    """
    Raised instead of terminating the process group.
    """


class FakePanic:

    def __init__(self):
        self.was_called = False

    def __call__(self, msg):
        self.was_called = True
        raise Panic(msg)


@pytest.fixture
def fake_panic(monkeypatch):
    fp = FakePanic()
    monkeypatch.setattr(spwd, "panic", fp)
    return fp


def test_normal_flow(fake_panic):
    fm = FakeMaster()
    cb = WatchdogCallback()
    wd = spwd.Watchdog(fm, 0.01, callback=cb)

    wd.start()
    try:
        for i in range(10):
            cb.wait()
            assert not fake_panic.was_called
            cb.resume()
    finally:
        wd.stop()


def test_panic_on_lost_lease(fake_panic):
    fm = FakeMaster()
    cb = WatchdogCallback()
    wd = spwd.Watchdog(fm, 0.01, callback=cb)

    wd.start()
    try:
        # Let first check succeed.
        cb.wait()
        assert not fake_panic.was_called

        # Simulate lost lease.
        del fm.resources[-1]

        # Wait for the next check.
        cb.resume()
        cb.wait()
        assert fake_panic.was_called
    finally:
        cb.resume()
        wd.stop()


def test_panic_on_wrong_disk(fake_panic):
    fm = FakeMaster()
    cb = WatchdogCallback()
    wd = spwd.Watchdog(fm, 0.01, callback=cb)

    wd.start()
    try:
        # Let first check succeed.
        cb.wait()
        assert not fake_panic.was_called

        # Simulate bad disk.
        fm.resources[-1]["disks"] = [("/master/leases", 100 * MiB)]

        # Wait for the next check.
        cb.resume()
        cb.wait()
        assert fake_panic.was_called
    finally:
        cb.resume()
        wd.stop()


def test_panic_on_error(fake_panic):
    fm = FakeMaster()
    cb = WatchdogCallback()
    wd = spwd.Watchdog(fm, 0.01, callback=cb)

    wd.start()
    try:
        # Let first check succeed.
        cb.wait()
        assert not fake_panic.was_called

        # Simulate error checking lease.
        fm.error = Exception("Inquire error")

        # Wait for the next check.
        cb.resume()
        cb.wait()
        assert fake_panic.was_called
    finally:
        cb.resume()
        wd.stop()


def test_temporary_error(fake_panic):
    fm = FakeMaster()
    cb = WatchdogCallback()
    wd = spwd.Watchdog(fm, 0.01, callback=cb)

    wd.start()
    try:
        # Let first check succeed.
        cb.wait()
        assert not fake_panic.was_called

        # Simulate a temporary error checking lease.
        e = sanlock.SanlockException(
            errno.EBUSY, "Inquire error", "Device or resource busy")
        fm.error = se.SanlockInquireError(e.errno, str(e))

        # Wait for next 3 checks
        for i in range(3):
            cb.resume()
            cb.wait()
            assert not fake_panic.was_called

        # Next error should trigger a panic.
        cb.resume()
        cb.wait()
        assert fake_panic.was_called
    finally:
        cb.resume()
        wd.stop()


def test_max_errors(fake_panic):
    fm = FakeMaster()
    cb = WatchdogCallback()
    wd = spwd.Watchdog(fm, 0.01, max_errors=0, callback=cb)

    wd.start()
    try:
        # Simulate a temporary error checking lease.
        e = sanlock.SanlockException(
            errno.EBUSY, "Inquire error", "Device or resource busy")
        fm.error = se.SanlockInquireError(e.errno, str(e))

        # Next check should trigger a panic.
        cb.wait()
        assert fake_panic.was_called
    finally:
        cb.resume()
        wd.stop()
