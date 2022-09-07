# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import
from __future__ import division

import json
import time

import pytest
import sanlock

from vdsm.common import concurrent
from vdsm.common.units import MiB
from vdsm.storage import clusterlock
from vdsm.storage import constants as sc
from vdsm.storage import exception as se


LS_NAME = b"sd-uuid"
LS_PATH = "ids"
LS_OFF = 0
HOST_ID = 1
LEASE = clusterlock.Lease("SDM", "leases", MiB)


class FakePanic(Exception):
    pass


@pytest.fixture
def lock(monkeypatch):
    # Reset class attributes to keep tests isolated.
    monkeypatch.setattr(clusterlock.SANLock, "_process_fd", None)
    monkeypatch.setattr(clusterlock.SANLock, "_lease_count", 0)

    # Monkeypatch clusterlock.panic() to allow testing panic without killing
    # the tests process.

    def fake_panic(msg):
        raise FakePanic(msg)

    monkeypatch.setattr(clusterlock, "panic", fake_panic)

    # Create new sanlock instance.
    sanlock = clusterlock.SANLock(LS_NAME.decode("utf-8"), LS_PATH, LEASE)
    sanlock.initLock(LEASE)
    return sanlock


def test_acquire_host_id_sync(fake_sanlock, lock):
    lock.acquireHostId(HOST_ID, wait=True)
    acquired = fake_sanlock.inq_lockspace(LS_NAME, HOST_ID, LS_PATH, LS_OFF)
    assert acquired is True


def test_acquire_host_id_async(fake_sanlock, lock):
    lock.acquireHostId(HOST_ID, wait=False)
    acquired = fake_sanlock.inq_lockspace(LS_NAME, HOST_ID, LS_PATH, LS_OFF)
    assert acquired is None


def test_release_host_id_sync(fake_sanlock, lock):
    lock.acquireHostId(HOST_ID, wait=True)
    lock.releaseHostId(HOST_ID, wait=True, unused=False)
    acquired = fake_sanlock.inq_lockspace(LS_NAME, HOST_ID, LS_PATH, LS_OFF)
    assert acquired is False


def test_release_host_id_async(fake_sanlock, lock):
    lock.acquireHostId(HOST_ID, wait=True)
    lock.releaseHostId(HOST_ID, wait=False, unused=False)
    acquired = fake_sanlock.inq_lockspace(LS_NAME, HOST_ID, LS_PATH, LS_OFF)
    assert acquired is None


def test_acquire(fake_sanlock, lock):
    lock.acquireHostId(HOST_ID, wait=True)
    lock.acquire(HOST_ID, LEASE)
    res = fake_sanlock.resources[(LEASE.path, LEASE.offset)]
    assert res["acquired"]
    assert not res["lvb"]


def test_acquire_lvb(fake_sanlock, lock):
    lock.acquireHostId(HOST_ID, wait=True)
    lock.acquire(HOST_ID, LEASE, lvb=True)
    res = fake_sanlock.resources[(LEASE.path, LEASE.offset)]
    assert res["acquired"]
    assert res["lvb"]


def test_acquire_process_fd_closed_recover(fake_sanlock, lock):
    lock.acquireHostId(HOST_ID, wait=True)

    # Acquire and release first lease to register socket with sanlock.
    lease1 = clusterlock.Lease("lease-1", "/leases", 100 * MiB)
    fake_sanlock.write_resource(
        LS_NAME, lease1.name.encode("utf-8"), [(lease1.path, lease1.offset)])
    lock.acquire(HOST_ID, lease1)
    lock.release(lease1)

    old_socket = fake_sanlock.process_socket

    # Simulate process fd closed on sanlock daemon side. Since we don't hold
    # any lease, we don't care about this.
    fake_sanlock.process_socket.close()

    lease2 = clusterlock.Lease("lease-2", "/leases", 101 * MiB)
    fake_sanlock.write_resource(
        LS_NAME, lease2.name.encode("utf-8"), [(lease2.path, lease2.offset)])

    # Since we have no leases, recover from the failure by registering new
    # socket with sanlock and retrying the acquire() call.
    lock.acquire(HOST_ID, lease2)

    assert fake_sanlock.process_socket != old_socket

    res = fake_sanlock.resources[(lease2.path, lease2.offset)]
    assert res["acquired"]
    assert not res["lvb"]


def test_acquire_process_fd_closed_panic(fake_sanlock, lock):
    lock.acquireHostId(HOST_ID, wait=True)

    # Acquire the first lease.
    lease1 = clusterlock.Lease("lease-1", "/leases", 100 * MiB)
    fake_sanlock.write_resource(
        LS_NAME, lease1.name.encode("utf-8"), [(lease1.path, lease1.offset)])
    lock.acquire(HOST_ID, LEASE)

    # Simulate process fd closed on sanlock daemon side. The lease was released
    # behind our back.
    fake_sanlock.process_socket.close()
    old_socket = fake_sanlock.process_socket

    lease2 = clusterlock.Lease("lease-2", "/leases", 101 * MiB)
    fake_sanlock.write_resource(
        LS_NAME, lease2.name.encode("utf-8"), [(lease2.path, lease2.offset)])

    # Since we have a lease, panic!
    with pytest.raises(FakePanic):
        lock.acquire(HOST_ID, lease2)

    assert old_socket == fake_sanlock.process_socket


def test_release(fake_sanlock, lock):
    lock.acquireHostId(HOST_ID, wait=True)
    lock.acquire(HOST_ID, LEASE)
    lock.release(LEASE)
    res = fake_sanlock.read_resource(LEASE.path, LEASE.offset)
    assert not res["acquired"]


def test_release_process_fd_closed_raise(fake_sanlock, lock):
    lock.acquireHostId(HOST_ID, wait=True)

    # Acquire and release first lease to register socket with sanlock.
    lock.acquire(HOST_ID, LEASE)
    lock.release(LEASE)

    # Simulate process fd closed on sanlock daemon side.
    fake_sanlock.process_socket.close()
    old_socket = fake_sanlock.process_socket

    # Since we have no leases, recover from the failure by raising.
    with pytest.raises(se.ReleaseLockFailure):
        lock.release(LEASE)

    # In release there is no need to reopen the socket.
    assert old_socket == fake_sanlock.process_socket


def test_release_process_fd_closed_panic(fake_sanlock, lock):
    lock.acquireHostId(HOST_ID, wait=True)
    lock.acquire(HOST_ID, LEASE)

    # Simulate process fd closed on sanlock daemon side.
    fake_sanlock.process_socket.close()
    old_socket = fake_sanlock.process_socket

    # Since we have a lease, panic!
    with pytest.raises(FakePanic):
        lock.release(LEASE)

    # In release there is no need to reopen the socket.
    assert old_socket == fake_sanlock.process_socket


def test_acquire_wait_until_host_id_is_acquired(fake_sanlock, lock):
    # Starts async host id acquire...
    lock.acquireHostId(HOST_ID, wait=False)

    def monitor():
        # Simulate the domain monitor checking if host id was acquire every 10
        # seconds...
        for i in range(3):
            lock.hasHostId(HOST_ID)
            time.sleep(0.3)

        fake_sanlock.complete_async(LS_NAME)
        # Discover that host id was acquired, and wake up threads waiting on
        # acquire().
        lock.hasHostId(HOST_ID)

    t = concurrent.thread(monitor)
    t.start()
    try:
        # Acquire should wait until host id acquire is completed.
        lock.acquire(HOST_ID, LEASE)
        res = fake_sanlock.read_resource(LEASE.path, LEASE.offset)
    finally:
        t.join()
    assert res["acquired"]


def test_acquire_after_inq_lockspace_failure(fake_sanlock, lock):
    # Starts async host id acquire...
    lock.acquireHostId(HOST_ID, wait=False)

    def monitor():
        time.sleep(0.3)

        # Simulate failing hasHostId...
        fake_sanlock.errors["inq_lockspace"] = fake_sanlock.SanlockException(1)
        try:
            lock.hasHostId(HOST_ID)
        except fake_sanlock.SanlockException:
            pass

        time.sleep(0.3)

        # Make the next try successful
        fake_sanlock.complete_async(LS_NAME)
        del fake_sanlock.errors["inq_lockspace"]
        lock.hasHostId(HOST_ID)

    t = concurrent.thread(monitor)
    t.start()
    try:
        # Acquire should wait until host id acquire is completed.
        lock.acquire(HOST_ID, LEASE)
        res = fake_sanlock.read_resource(LEASE.path, LEASE.offset)
    finally:
        t.join()
    assert res["acquired"]


def test_acquire_timeout_waiting_for_host_id(fake_sanlock, lock, monkeypatch):
    # Make this test fast
    monkeypatch.setattr(lock, "ACQUIRE_HOST_ID_TIMEOUT", 0.0)
    # Starts async host id acquire that will never complete...
    lock.acquireHostId(HOST_ID, wait=False)
    # Acquire should time out
    pytest.raises(se.AcquireHostIdFailure, lock.acquire, HOST_ID, LEASE)


def test_acquire_after_relases_host_id(fake_sanlock, lock):
    lock.acquireHostId(HOST_ID, wait=True)
    lock.releaseHostId(HOST_ID, wait=True, unused=False)
    pytest.raises(concurrent.InvalidEvent, lock.acquire, HOST_ID, LEASE)


def test_inspect(fake_sanlock, lock):
    lock.acquireHostId(HOST_ID, wait=True)
    lock.acquire(HOST_ID, LEASE)
    version, owner = lock.inspect(LEASE)
    assert version == 0
    assert owner == HOST_ID


@pytest.mark.parametrize("status,expected_owner_id", [
    (sanlock.HOST_LIVE, HOST_ID),
    (sanlock.HOST_FAIL, HOST_ID),
    (sanlock.HOST_UNKNOWN, HOST_ID),
    (sanlock.HOST_FREE, None),
    (sanlock.HOST_DEAD, None)
])
def test_inspect_owner_status(fake_sanlock, lock, status, expected_owner_id):
    lock.acquireHostId(HOST_ID, wait=True)
    lock.acquire(HOST_ID, LEASE)
    # we are simulating another host inquiring the lease
    fake_sanlock.hosts[HOST_ID]["flags"] = status
    version, owner = lock.inspect(LEASE)
    assert version == 0
    assert owner == expected_owner_id


def test_inspect_owner_reconnected(fake_sanlock, lock):
    # This simulates a host reconnecting to the lockspace.
    # The lease should have no owner since the generation
    # increases each time a host reconnects to the lockspace
    lock.acquireHostId(HOST_ID, wait=True)
    lock.acquire(HOST_ID, LEASE)
    lock.releaseHostId(HOST_ID, wait=True, unused=True)
    lock.acquireHostId(HOST_ID, wait=True)
    version, owner = lock.inspect(LEASE)
    assert version == 0
    assert owner is None


def test_inspect_smaller_host_generation(fake_sanlock, lock):
    lock.acquireHostId(HOST_ID, wait=True)
    lock.releaseHostId(HOST_ID, wait=True, unused=True)
    lock.acquireHostId(HOST_ID, wait=True)
    lock.acquire(HOST_ID, LEASE)
    # Setting the host generation to be smaller than the
    # generation on the lease (an invalid state), the
    # lease should have no owner
    fake_sanlock.hosts[HOST_ID]["generation"] = 0
    version, owner = lock.inspect(LEASE)
    assert version == 0
    assert owner is None


def test_inspect_has_no_owner(fake_sanlock, lock):
    lock.acquireHostId(HOST_ID, wait=True)
    version, owner = lock.inspect(LEASE)
    assert version is None
    assert owner is None


def test_inquire(fake_sanlock, lock):
    # No lockspace yet...
    assert lock.inquire() == []

    # Add lockspace.
    lock.acquireHostId(HOST_ID, wait=True)
    assert lock.inquire() == []

    # Acquire a resource.
    lock.acquire(HOST_ID, LEASE)
    assert lock.inquire() == [
        {
            "lockspace": LS_NAME.decode("utf-8"),
            "resource": LEASE.name,
            "flags": fake_sanlock.RES_LVER,
            "version": 0,
            "disks": [(LEASE.path, LEASE.offset)],
        }
    ]

    # Release the resource.
    lock.release(LEASE)
    assert lock.inquire() == []


def test_inquire_temporary_error(fake_sanlock, lock):
    lock.acquireHostId(HOST_ID, wait=True)
    lock.acquire(HOST_ID, LEASE)

    fake_sanlock.resources[(LEASE.path, LEASE.offset)]["busy"] = True

    with pytest.raises(se.SanlockInquireError) as e:
        lock.inquire()
    assert e.value.is_temporary()


def test_inquire_process_fd_closed_recover(fake_sanlock, lock):
    lock.acquireHostId(HOST_ID, wait=True)

    # Acquire and release a lease to register socket with sanlock.
    lock.acquire(HOST_ID, LEASE)
    lock.release(LEASE)

    # Simulate process fd closed on sanlock daemon side. Since we don't hold
    # any lease, we don't care about this.
    fake_sanlock.process_socket.close()
    old_socket = fake_sanlock.process_socket

    # Since we have no lease, this should not panic.
    with pytest.raises(se.SanlockInquireError):
        lock.inquire()

    # Old socket is not modifed by failed inquire.
    assert old_socket == fake_sanlock.process_socket


def test_inquire_process_fd_closed_panic(fake_sanlock, lock):
    lock.acquireHostId(HOST_ID, wait=True)

    # Acquire a lease.
    lock.acquire(HOST_ID, LEASE)

    # Simulate process fd closed on sanlock daemon side. The lease was released
    # behind our back.
    fake_sanlock.process_socket.close()
    old_socket = fake_sanlock.process_socket

    # Since we have a lease, panic!
    with pytest.raises(FakePanic):
        lock.inquire()

    # Old socket is not modifed by failed inquire.
    assert old_socket == fake_sanlock.process_socket


@pytest.mark.parametrize('block_size, max_hosts, alignment', [
    (sc.BLOCK_SIZE_512, 250, sc.ALIGNMENT_1M),
    (sc.BLOCK_SIZE_512, 2000, sc.ALIGNMENT_1M),
    (sc.BLOCK_SIZE_4K, 250, sc.ALIGNMENT_1M),
    (sc.BLOCK_SIZE_4K, 251, sc.ALIGNMENT_2M),
    (sc.BLOCK_SIZE_4K, 499, sc.ALIGNMENT_2M),
    (sc.BLOCK_SIZE_4K, 500, sc.ALIGNMENT_2M),
    (sc.BLOCK_SIZE_4K, 501, sc.ALIGNMENT_4M),
    (sc.BLOCK_SIZE_4K, 999, sc.ALIGNMENT_4M),
    (sc.BLOCK_SIZE_4K, 1000, sc.ALIGNMENT_4M),
    (sc.BLOCK_SIZE_4K, 1001, sc.ALIGNMENT_8M),
    (sc.BLOCK_SIZE_4K, 1999, sc.ALIGNMENT_8M),
    (sc.BLOCK_SIZE_4K, 2000, sc.ALIGNMENT_8M),
])
def test_sanlock_alignment(block_size, max_hosts, alignment):
    assert clusterlock.alignment(block_size, max_hosts) == alignment


@pytest.mark.parametrize('block_size, max_hosts', [
    (sc.BLOCK_SIZE_512 - 1, sc.HOSTS_512_1M),
    (sc.BLOCK_SIZE_512 + 1, sc.HOSTS_512_1M),
    (sc.BLOCK_SIZE_4K - 1, sc.HOSTS_4K_8M),
    (sc.BLOCK_SIZE_4K + 1, sc.HOSTS_4K_1M),
])
def test_sanlock_invalid_block_size(block_size, max_hosts):
    with pytest.raises(se.InvalidParameterException) as e:
        clusterlock.alignment(block_size, max_hosts)
    error_str = str(e)
    assert "block_size" in error_str
    assert str(block_size) in error_str


@pytest.mark.parametrize('block_size, max_hosts', [
    (sc.BLOCK_SIZE_512, -1),
    (sc.BLOCK_SIZE_4K, 0),
    (sc.BLOCK_SIZE_512, sc.HOSTS_512_1M + 1),
    (sc.BLOCK_SIZE_4K, sc.HOSTS_4K_8M + 1),
])
def test_sanlock_invalid_max_hosts(block_size, max_hosts):
    with pytest.raises(se.InvalidParameterException) as e:
        clusterlock.alignment(block_size, max_hosts)
    error_str = str(e)
    assert "max_hosts" in error_str
    assert str(max_hosts) in error_str


def test_set_lvb(fake_sanlock, lock):
    lock.acquireHostId(HOST_ID, wait=True)
    lock.acquire(HOST_ID, LEASE, lvb=True)

    # Test smaller size
    info = {
        "generation": 1,
        "job_status": "STARTED",
        "padding": ""
    }

    lock.set_lvb(LEASE, info)
    result = lock.get_lvb(LEASE)
    assert info == result

    # Test max size
    json_size = len(json.dumps(info).encode("utf-8"))
    info["padding"] = "a" * (clusterlock.LVB_SIZE - json_size)
    lock.set_lvb(LEASE, info)
    result = lock.get_lvb(LEASE)
    assert info == result

    # Test larger size
    info["padding"] = "a" * (clusterlock.LVB_SIZE - json_size + 1)
    with pytest.raises(se.SanlockLVBError):
        lock.set_lvb(LEASE, info)


def test_get_lvb_empty(fake_sanlock, lock):
    lock.acquireHostId(HOST_ID, wait=True)
    lock.acquire(HOST_ID, LEASE, lvb=True)

    lock.get_lvb(LEASE) == {}
