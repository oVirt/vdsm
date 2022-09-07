# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

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
def panic(monkeypatch):
    panic = FakePanic()
    monkeypatch.setattr(spwd, "panic", panic)
    return panic


def test_normal_flow(panic):
    master = FakeMaster()
    cb = WatchdogCallback()
    watchdog = spwd.Watchdog(master, 0.01, callback=cb)

    watchdog.start()
    try:
        for i in range(10):
            cb.wait()
            assert not panic.was_called
            cb.resume()
    finally:
        watchdog.stop()


def test_panic_on_lost_lease(panic):
    master = FakeMaster()
    cb = WatchdogCallback()
    watchdog = spwd.Watchdog(master, 0.01, callback=cb)

    watchdog.start()
    try:
        # Let first check succeed.
        cb.wait()
        assert not panic.was_called

        # Simulate lost lease.
        del master.resources[-1]

        # Wait for the next check.
        cb.resume()
        cb.wait()
        assert panic.was_called
    finally:
        cb.resume()
        watchdog.stop()


def test_panic_on_wrong_disk(panic):
    master = FakeMaster()
    cb = WatchdogCallback()
    watchdog = spwd.Watchdog(master, 0.01, callback=cb)

    watchdog.start()
    try:
        # Let first check succeed.
        cb.wait()
        assert not panic.was_called

        # Simulate bad disk.
        master.resources[-1]["disks"] = [("/master/leases", 100 * MiB)]

        # Wait for the next check.
        cb.resume()
        cb.wait()
        assert panic.was_called
    finally:
        cb.resume()
        watchdog.stop()


def test_panic_on_error(panic):
    master = FakeMaster()
    cb = WatchdogCallback()
    watchdog = spwd.Watchdog(master, 0.01, callback=cb)

    watchdog.start()
    try:
        # Let first check succeed.
        cb.wait()
        assert not panic.was_called

        # Simulate error checking lease.
        master.error = Exception("Inquire error")

        # Wait for the next check.
        cb.resume()
        cb.wait()
        assert panic.was_called
    finally:
        cb.resume()
        watchdog.stop()


def test_temporary_error(panic):
    master = FakeMaster()
    cb = WatchdogCallback()
    watchdog = spwd.Watchdog(master, 0.01, callback=cb)

    watchdog.start()
    try:
        # Let first check succeed.
        cb.wait()
        assert not panic.was_called

        # Simulate a temporary error checking lease.
        e = sanlock.SanlockException(
            errno.EBUSY, "Inquire error", "Device or resource busy")
        master.error = se.SanlockInquireError(e.errno, str(e))

        # Wait for next 3 checks
        for i in range(3):
            cb.resume()
            cb.wait()
            assert not panic.was_called

        # Next error should trigger a panic.
        cb.resume()
        cb.wait()
        assert panic.was_called
    finally:
        cb.resume()
        watchdog.stop()


def test_max_errors(panic):
    master = FakeMaster()
    cb = WatchdogCallback()
    watchdog = spwd.Watchdog(master, 0.01, max_errors=0, callback=cb)

    watchdog.start()
    try:
        # Simulate a temporary error checking lease.
        e = sanlock.SanlockException(
            errno.EBUSY, "Inquire error", "Device or resource busy")
        master.error = se.SanlockInquireError(e.errno, str(e))

        # Next check should trigger a panic.
        cb.wait()
        assert panic.was_called
    finally:
        cb.resume()
        watchdog.stop()
