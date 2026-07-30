# SPDX-FileCopyrightText: oVirt Developers
# SPDX-License-Identifier: GPL-2.0-or-later

import errno
import threading

import sanlock
import pytest

from vdsm.common.units import MiB
from vdsm.storage import clusterlock
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

    def __init__(self, owner=1, version=1):
        # The sanlock host id owning the cluster lease on disk, or None when
        # the lease is free (e.g. generation mismatch after a storage blip).
        self.owner = owner
        self.version = version
        self.error = None

    def inspectClusterLock(self):
        if self.error:
            raise self.error
        if self.owner is None:
            return None, None
        return self.version, self.owner

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
    master = FakeMaster(owner=1)
    cb = WatchdogCallback()
    watchdog = spwd.Watchdog(master, 0.01, host_id=1, callback=cb)

    watchdog.start()
    try:
        for i in range(10):
            cb.wait()
            assert not panic.was_called
            cb.resume()
    finally:
        watchdog.stop()


def test_panic_on_lost_lease(panic):
    # The lease has no valid owner on disk (generation mismatch after a
    # storage blip, or no owner record) while the in-memory SPM role is
    # still set.
    master = FakeMaster(owner=1)
    cb = WatchdogCallback()
    watchdog = spwd.Watchdog(master, 0.01, host_id=1, callback=cb)

    watchdog.start()
    try:
        # Let first check succeed.
        cb.wait()
        assert not panic.was_called

        # Simulate lost lease on disk.
        master.owner = None

        # Wait for the next check.
        cb.resume()
        cb.wait()
        assert panic.was_called
    finally:
        cb.resume()
        watchdog.stop()


def test_panic_on_other_owner(panic):
    # The lease is held by another host on disk while we still have a stale
    # in-memory SPM role - the split brain case.
    master = FakeMaster(owner=1)
    cb = WatchdogCallback()
    watchdog = spwd.Watchdog(master, 0.01, host_id=1, callback=cb)

    watchdog.start()
    try:
        # Let first check succeed.
        cb.wait()
        assert not panic.was_called

        # Another host acquired the lease.
        master.owner = 2

        # Wait for the next check.
        cb.resume()
        cb.wait()
        assert panic.was_called
    finally:
        cb.resume()
        watchdog.stop()


def test_panic_on_error(panic):
    master = FakeMaster(owner=1)
    cb = WatchdogCallback()
    watchdog = spwd.Watchdog(master, 0.01, host_id=1, callback=cb)

    watchdog.start()
    try:
        # Let first check succeed.
        cb.wait()
        assert not panic.was_called

        # Simulate error checking lease.
        master.error = Exception("Inspect error")

        # Wait for the next check.
        cb.resume()
        cb.wait()
        assert panic.was_called
    finally:
        cb.resume()
        watchdog.stop()


def test_temporary_error(panic):
    master = FakeMaster(owner=1)
    cb = WatchdogCallback()
    watchdog = spwd.Watchdog(master, 0.01, host_id=1, callback=cb)

    watchdog.start()
    try:
        # Let first check succeed.
        cb.wait()
        assert not panic.was_called

        # Simulate a temporary error (add_lockspace not complete).
        master.error = clusterlock.TemporaryFailure(
            "inspect",
            clusterlock.Lease("SDM", "/master/leases", MiB),
            "add_lockspace not completed yet",
        )

        # Wait for next 3 checks - tolerated up to max_errors.
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


def test_storage_error(panic):
    # A storage I/O error reading the lease (e.g. during a storage blip) is
    # tolerated up to max_errors before panicking. The sanlock host id
    # watchdog fences the host if storage does not recover.
    master = FakeMaster(owner=1)
    cb = WatchdogCallback()
    watchdog = spwd.Watchdog(master, 0.01, host_id=1, callback=cb)

    watchdog.start()
    try:
        # Let first check succeed.
        cb.wait()
        assert not panic.was_called

        # Simulate a storage I/O error reading the lease.
        master.error = sanlock.SanlockException(
            errno.EIO, "read_resource", "Input/output error"
        )

        # Wait for next 3 checks - tolerated up to max_errors.
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
    master = FakeMaster(owner=1)
    cb = WatchdogCallback()
    watchdog = spwd.Watchdog(
        master, 0.01, host_id=1, max_errors=0, callback=cb
    )

    watchdog.start()
    try:
        # Simulate a temporary error checking lease.
        master.error = clusterlock.TemporaryFailure(
            "inspect",
            clusterlock.Lease("SDM", "/master/leases", MiB),
            "add_lockspace not completed yet",
        )

        # Next check should trigger a panic (max_errors=0).
        cb.wait()
        assert panic.was_called
    finally:
        cb.resume()
        watchdog.stop()


def test_transient_error_counter_reset(panic):
    # A successful check resets the error counter, so transient errors
    # are tolerated up to max_errors again after a recovery.
    master = FakeMaster(owner=1)
    cb = WatchdogCallback()
    watchdog = spwd.Watchdog(master, 0.01, host_id=1, callback=cb)

    watchdog.start()
    try:
        # Let first check succeed.
        cb.wait()
        assert not panic.was_called

        # Tolerate one transient error.
        master.error = clusterlock.TemporaryFailure(
            "inspect",
            clusterlock.Lease("SDM", "/master/leases", MiB),
            "add_lockspace not completed yet",
        )
        cb.resume()
        cb.wait()
        assert not panic.was_called

        # Recover - the next successful check resets the counter.
        master.error = None
        cb.resume()
        cb.wait()
        assert not panic.was_called

        # Without the reset, the third of these errors would panic.
        for i in range(3):
            master.error = clusterlock.TemporaryFailure(
                "inspect",
                clusterlock.Lease("SDM", "/master/leases", MiB),
                "add_lockspace not completed yet",
            )
            cb.resume()
            cb.wait()
            assert not panic.was_called

        # The next error exceeds max_errors and panics.
        cb.resume()
        cb.wait()
        assert panic.was_called
    finally:
        cb.resume()
        watchdog.stop()
