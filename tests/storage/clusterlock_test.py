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
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA  02110-1301 USA
#
# Refer to the README and COPYING files for full details of the license
#

import time
import pytest
from fakesanlock import FakeSanlock
from vdsm.common import concurrent
from vdsm.storage import clusterlock
from vdsm.storage import exception as se

LS_NAME = "sd-uuid"
LS_PATH = "ids"
LS_OFF = 0
HOST_ID = 1
LEASE = clusterlock.Lease("SDM", "leases", 1024**2)


@pytest.fixture
def fake_sanlock(monkeypatch):
    fs = FakeSanlock()
    monkeypatch.setattr(clusterlock, "sanlock", fs)
    # FakeSanlock does not implement the depracated init_resource, so we will
    # create the resource using write_resource, so we can test acquire and
    # release.
    fs.write_resource(LS_NAME, LEASE.name, [(LEASE.path, LEASE.offset)])
    return fs


def test_acquire_host_id_sync(fake_sanlock):
    sl = clusterlock.SANLock(LS_NAME, LS_PATH, LEASE)
    sl.acquireHostId(HOST_ID, async=False)
    acquired = fake_sanlock.inq_lockspace(LS_NAME, HOST_ID, LS_PATH, LS_OFF)
    assert acquired is True


def test_acquire_host_id_async(fake_sanlock):
    sl = clusterlock.SANLock(LS_NAME, LS_PATH, LEASE)
    sl.acquireHostId(HOST_ID, async=True)
    acquired = fake_sanlock.inq_lockspace(LS_NAME, HOST_ID, LS_PATH, LS_OFF)
    assert acquired is None


def test_release_host_id_sync(fake_sanlock):
    sl = clusterlock.SANLock(LS_NAME, LS_PATH, LEASE)
    sl.acquireHostId(HOST_ID, async=False)
    sl.releaseHostId(HOST_ID, async=False, unused=False)
    acquired = fake_sanlock.inq_lockspace(LS_NAME, HOST_ID, LS_PATH, LS_OFF)
    assert acquired is False


def test_release_host_id_async(fake_sanlock):
    sl = clusterlock.SANLock(LS_NAME, LS_PATH, LEASE)
    sl.acquireHostId(HOST_ID, async=False)
    sl.releaseHostId(HOST_ID, async=True, unused=False)
    acquired = fake_sanlock.inq_lockspace(LS_NAME, HOST_ID, LS_PATH, LS_OFF)
    assert acquired is None


def test_acquire(fake_sanlock):
    sl = clusterlock.SANLock(LS_NAME, LS_PATH, LEASE)
    sl.acquireHostId(HOST_ID, async=False)
    sl.acquire(HOST_ID, LEASE)
    res = fake_sanlock.read_resource(LEASE.path, LEASE.offset)
    assert res["acquired"]


def test_release(fake_sanlock):
    sl = clusterlock.SANLock(LS_NAME, LS_PATH, LEASE)
    sl.acquireHostId(HOST_ID, async=False)
    sl.acquire(HOST_ID, LEASE)
    sl.release(LEASE)
    res = fake_sanlock.read_resource(LEASE.path, LEASE.offset)
    assert not res["acquired"]


def test_acquire_wait_until_host_id_is_acquired(fake_sanlock):
    sl = clusterlock.SANLock(LS_NAME, LS_PATH, LEASE)
    # Starts async host id acquire...
    sl.acquireHostId(HOST_ID, async=True)

    def monitor():
        # Simulate the domain monitor checking if host id was acquire every 10
        # seconds...
        for i in range(3):
            sl.hasHostId(HOST_ID)
            time.sleep(0.3)

        fake_sanlock.complete_async(LS_NAME)
        # Discover that host id was acquired, and wake up threads waiting on
        # acquire().
        sl.hasHostId(HOST_ID)

    t = concurrent.thread(monitor)
    t.start()
    try:
        # Acquire should wait until host id acquire is completed.
        sl.acquire(HOST_ID, LEASE)
        res = fake_sanlock.read_resource(LEASE.path, LEASE.offset)
    finally:
        t.join()
    assert res["acquired"]


def test_acquire_after_inq_lockspace_failure(fake_sanlock):
    sl = clusterlock.SANLock(LS_NAME, LS_PATH, LEASE)
    # Starts async host id acquire...
    sl.acquireHostId(HOST_ID, async=True)

    def monitor():
        time.sleep(0.3)

        # Simulate failing hasHostId...
        fake_sanlock.errors["inq_lockspace"] = fake_sanlock.SanlockException(1)
        try:
            sl.hasHostId(HOST_ID)
        except fake_sanlock.SanlockException:
            pass

        time.sleep(0.3)

        # Make the next try successful
        fake_sanlock.complete_async(LS_NAME)
        del fake_sanlock.errors["inq_lockspace"]
        sl.hasHostId(HOST_ID)

    t = concurrent.thread(monitor)
    t.start()
    try:
        # Acquire should wait until host id acquire is completed.
        sl.acquire(HOST_ID, LEASE)
        res = fake_sanlock.read_resource(LEASE.path, LEASE.offset)
    finally:
        t.join()
    assert res["acquired"]


def test_acquire_timeout_waiting_for_host_id(fake_sanlock, monkeypatch):
    # Make this test fast
    monkeypatch.setattr(clusterlock.SANLock, "ACQUIRE_HOST_ID_TIMEOUT", 0.0)
    sl = clusterlock.SANLock(LS_NAME, LS_PATH, LEASE)
    # Starts async host id acquire that will never complete...
    sl.acquireHostId(HOST_ID, async=True)
    # Acquire should time out
    pytest.raises(se.AcquireHostIdFailure, sl.acquire, HOST_ID, LEASE)


def test_acquire_after_relases_host_id(fake_sanlock):
    sl = clusterlock.SANLock(LS_NAME, LS_PATH, LEASE)
    sl.acquireHostId(HOST_ID, async=False)
    sl.releaseHostId(HOST_ID, async=False, unused=False)
    pytest.raises(concurrent.InvalidEvent, sl.acquire, HOST_ID, LEASE)
