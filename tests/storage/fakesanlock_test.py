#
# Copyright 2016 Red Hat, Inc.
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

from __future__ import absolute_import
from __future__ import division

import errno
import os

import pytest
import sanlock

from .fakesanlock import FakeSanlock
from vdsm.common import concurrent
from vdsm.common.units import KiB, MiB
from vdsm.storage import constants as sc


INVALID_ALIGN_SECTOR = [
    # Invalid alignment
    (KiB, sc.BLOCK_SIZE_512),
    # Invalid block size
    (sc.ALIGNMENT_1M, 8 * KiB),
]

WRONG_ALIGN_SECTOR = [
    # Wrong alignment
    (sc.ALIGNMENT_8M, sc.BLOCK_SIZE_512),
    # Wrong block size
    (sc.ALIGNMENT_1M, sc.BLOCK_SIZE_4K),
]


DIFFERENT_DISK_SANLOCK_SECTOR = [
    (sc.BLOCK_SIZE_512, sc.BLOCK_SIZE_4K),
    (sc.BLOCK_SIZE_4K, sc.BLOCK_SIZE_512),
]


LOCKSPACE_NAME = b"lockspace"
RESOURCE_NAME = b"resource"


class ExpectedError(Exception):
    pass


# Managing lockspaces

def test_add_lockspace_sync():
    fs = FakeSanlock()
    fs.write_lockspace(LOCKSPACE_NAME, "path")
    fs.add_lockspace(LOCKSPACE_NAME, 1, "path")
    ls = fs.spaces[LOCKSPACE_NAME]
    assert ls["host_id"] == 1
    assert ls["path"] == "path"
    assert ls["offset"] == 0
    assert ls["iotimeout"] == 0
    assert ls["ready"].is_set()


def test_add_lockspace_options():
    fs = FakeSanlock()
    fs.write_lockspace(LOCKSPACE_NAME, "path", offset=42)
    fs.add_lockspace(LOCKSPACE_NAME, 1, "path", offset=42, iotimeout=10)
    ls = fs.spaces[LOCKSPACE_NAME]
    assert ls["offset"] == 42
    assert ls["iotimeout"] == 10


def test_add_lockspace_async():
    fs = FakeSanlock()
    fs.write_lockspace(LOCKSPACE_NAME, "path")
    fs.add_lockspace(LOCKSPACE_NAME, 1, "path", wait=False)
    ls = fs.spaces[LOCKSPACE_NAME]
    assert ls["iotimeout"] == 0
    assert not ls["ready"].is_set()


@pytest.mark.parametrize(
    "disk_sector, sanlock_sector", DIFFERENT_DISK_SANLOCK_SECTOR)
def test_write_lockspace_wrong_sector(disk_sector, sanlock_sector):
    fs = FakeSanlock(disk_sector)
    with pytest.raises(fs.SanlockException) as e:
        fs.write_lockspace(LOCKSPACE_NAME, "path", sector=sanlock_sector)
    assert e.value.errno == errno.EINVAL


def test_rem_lockspace_sync():
    fs = FakeSanlock()
    fs.write_lockspace(LOCKSPACE_NAME, "path")
    fs.add_lockspace(LOCKSPACE_NAME, 1, "path")
    fs.rem_lockspace(LOCKSPACE_NAME, 1, "path")
    assert "host_id" not in fs.spaces[LOCKSPACE_NAME]


def test_rem_lockspace_async():
    fs = FakeSanlock()
    fs.write_lockspace(LOCKSPACE_NAME, "path")
    fs.add_lockspace(LOCKSPACE_NAME, 1, "path")
    fs.rem_lockspace(LOCKSPACE_NAME, 1, "path", wait=False)
    ls = fs.spaces[LOCKSPACE_NAME]
    assert not ls["ready"].is_set()


def test_rem_lockspace_while_holding_lock():
    fs = FakeSanlock()
    fs.write_lockspace(LOCKSPACE_NAME, "path")
    fs.write_resource(LOCKSPACE_NAME, RESOURCE_NAME, [("path", MiB)])
    fs.add_lockspace(LOCKSPACE_NAME, 1, "path")
    fd = fs.register()
    fs.acquire(LOCKSPACE_NAME, RESOURCE_NAME, [("path", MiB)], slkfd=fd)
    fs.rem_lockspace(LOCKSPACE_NAME, 1, "path", wait=False)

    # Fake sanlock return special None value when sanlock is in process of
    # releasing host_id.
    acquired = fs.inq_lockspace(LOCKSPACE_NAME, 1, "path")
    assert acquired is None

    # Finish rem_lockspace.
    fs.complete_async(LOCKSPACE_NAME)

    # Lock shouldn't be hold any more.
    acquired = fs.inq_lockspace(LOCKSPACE_NAME, 1, "path")
    assert acquired is not None
    assert not acquired


def test_inq_lockspace_acquired():
    fs = FakeSanlock()
    fs.write_lockspace(LOCKSPACE_NAME, "path")
    fs.add_lockspace(LOCKSPACE_NAME, 1, "path")
    acquired = fs.inq_lockspace(LOCKSPACE_NAME, 1, "path")
    assert acquired


def test_inq_lockspace_acquring_no_wait():
    fs = FakeSanlock()
    fs.write_lockspace(LOCKSPACE_NAME, "path")
    fs.add_lockspace(LOCKSPACE_NAME, 1, "path", wait=False)
    acquired = fs.inq_lockspace(LOCKSPACE_NAME, 1, "path")
    assert acquired is None


def test_inq_lockspace_acquiring_wait():
    fs = FakeSanlock()
    fs.write_lockspace(LOCKSPACE_NAME, "path")
    fs.add_lockspace(LOCKSPACE_NAME, 1, "path", wait=False)

    t = concurrent.thread(fs.complete_async, args=(LOCKSPACE_NAME,))
    t.start()
    try:
        acquired = fs.inq_lockspace(LOCKSPACE_NAME, 1, "path", wait=True)
    finally:
        t.join()
    assert acquired


def test_inq_lockspace_released():
    fs = FakeSanlock()
    fs.write_lockspace(LOCKSPACE_NAME, "path")
    fs.add_lockspace(LOCKSPACE_NAME, 1, "path")
    fs.rem_lockspace(LOCKSPACE_NAME, 1, "path")
    acquired = fs.inq_lockspace(LOCKSPACE_NAME, 1, "path")
    assert not acquired


def test_inq_lockspace_releasing_no_wait():
    fs = FakeSanlock()
    fs.write_lockspace(LOCKSPACE_NAME, "path")
    fs.add_lockspace(LOCKSPACE_NAME, 1, "path")
    fs.rem_lockspace(LOCKSPACE_NAME, 1, "path", wait=False)
    acquired = fs.inq_lockspace(LOCKSPACE_NAME, 1, "path")
    assert not acquired


def test_inq_lockspace_releasing_wait():
    fs = FakeSanlock()
    fs.write_lockspace(LOCKSPACE_NAME, "path")
    fs.add_lockspace(LOCKSPACE_NAME, 1, "path")
    fs.rem_lockspace(LOCKSPACE_NAME, 1, "path", wait=False)

    t = concurrent.thread(fs.complete_async, args=(LOCKSPACE_NAME,))
    t.start()
    try:
        acquired = fs.inq_lockspace(LOCKSPACE_NAME, 1, "path", wait=True)
    finally:
        t.join()
    assert not acquired


# Writing and reading resources

def test_write_read_resource():
    fs = FakeSanlock()
    fs.write_resource(LOCKSPACE_NAME, RESOURCE_NAME, [("path", MiB)])
    info = fs.read_resource("path", MiB)
    expected = {
        "resource": RESOURCE_NAME,
        "lockspace": LOCKSPACE_NAME,
        "version": 0,
        "acquired": False,
        "align": sc.ALIGNMENT_1M,
        "sector": sc.BLOCK_SIZE_512,
    }
    assert info == expected


def test_non_existing_resource():
    fs = FakeSanlock()
    with pytest.raises(fs.SanlockException) as e:
        fs.read_resource("path", MiB)
    assert e.value.errno == fs.SANLK_LEADER_MAGIC


def test_write_resource_failure():
    fs = FakeSanlock()
    fs.errors["write_resource"] = ExpectedError
    with pytest.raises(ExpectedError):
        fs.write_resource(LOCKSPACE_NAME, RESOURCE_NAME, [("path", MiB)])
    with pytest.raises(fs.SanlockException) as e:
        fs.read_resource("path", MiB)
    assert e.value.errno == fs.SANLK_LEADER_MAGIC


@pytest.mark.parametrize("align, sector", INVALID_ALIGN_SECTOR)
def test_write_resource_invalid_align_sector(align, sector):
    fs = FakeSanlock()
    disks = [("path", 0)]
    with pytest.raises(ValueError):
        fs.write_resource(
            LOCKSPACE_NAME, RESOURCE_NAME, disks, align=align, sector=sector)


@pytest.mark.parametrize(
    "disk_sector, sanlock_sector", DIFFERENT_DISK_SANLOCK_SECTOR)
def test_write_resource_wrong_sector(disk_sector, sanlock_sector):
    fs = FakeSanlock(disk_sector)
    disks = [("path", 0)]
    # Real sanlock succeeds even with wrong sector size.
    fs.write_resource(
        LOCKSPACE_NAME, RESOURCE_NAME, disks, sector=sanlock_sector)


def test_read_resource_failure():
    fs = FakeSanlock()
    fs.errors["read_resource"] = ExpectedError
    fs.write_resource(LOCKSPACE_NAME, RESOURCE_NAME, [("path", MiB)])
    with pytest.raises(ExpectedError):
        fs.read_resource("path", MiB)


@pytest.mark.parametrize("align, sector", INVALID_ALIGN_SECTOR)
def test_read_resource_invalid_align_sector(align, sector):
    fs = FakeSanlock()
    fs.write_resource(LOCKSPACE_NAME, RESOURCE_NAME, [("path", MiB)])
    with pytest.raises(ValueError):
        fs.read_resource("path", MiB, align=align, sector=sector)


@pytest.mark.parametrize("align, sector", WRONG_ALIGN_SECTOR)
def test_read_resource_wrong_align_sector(align, sector):
    fs = FakeSanlock()
    fs.write_resource(LOCKSPACE_NAME, RESOURCE_NAME, [("path", MiB)])
    with pytest.raises(fs.SanlockException) as e:
        fs.read_resource("path", MiB, align=align, sector=sector)
    assert e.value.errno == errno.EINVAL


@pytest.mark.parametrize(
    "disk_sector, sanlock_sector", DIFFERENT_DISK_SANLOCK_SECTOR)
def test_read_resource_wrong_sector(disk_sector, sanlock_sector):
    fs = FakeSanlock(disk_sector)
    fs.write_resource(
        LOCKSPACE_NAME, RESOURCE_NAME, [("path", MiB)], sector=disk_sector)
    with pytest.raises(fs.SanlockException) as e:
        fs.read_resource("path", MiB, sector=sanlock_sector)
    assert e.value.errno == errno.EINVAL


# Registering with the sanlock daemon
def test_register():
    fs = FakeSanlock()
    fd = fs.register()
    assert fd == fs.process_socket.fileno()


def test_register_twice():
    fs = FakeSanlock()
    fs.register()
    with pytest.raises(AssertionError):
        fs.register()


def test_register_after_close():
    fs = FakeSanlock()
    fs.register()
    old_socket = fs.process_socket
    fs.process_socket.close()
    fs.register()
    assert fs.process_socket != old_socket


# Acquiring and releasing resources
def test_acquire():
    fs = FakeSanlock()
    fs.write_lockspace(LOCKSPACE_NAME, "path")
    fs.write_resource(LOCKSPACE_NAME, RESOURCE_NAME, [("path", MiB)])
    fs.add_lockspace(LOCKSPACE_NAME, 1, "path")
    fd = fs.register()
    fs.acquire(LOCKSPACE_NAME, RESOURCE_NAME, [("path", MiB)], slkfd=fd)
    res = fs.read_resource("path", MiB)
    assert res["acquired"]
    assert fs.spaces[LOCKSPACE_NAME]["host_id"] == res["host_id"]
    assert fs.hosts[1]["generation"] == res["generation"]


def test_acquire_slkfd_closed():
    fs = FakeSanlock()
    fd = fs.register()
    # Simulate slkfd closed by sanlock daemon.
    fs.process_socket.close()
    with pytest.raises(fs.SanlockException) as e:
        fs.acquire(LOCKSPACE_NAME, RESOURCE_NAME, [("path", MiB)], slkfd=fd)
    assert e.value.errno == errno.EPIPE


def test_release_slkfd_closed():
    fs = FakeSanlock()
    fd = fs.register()
    # Simulate slkfd closed by sanlock daemon.
    fs.process_socket.close()
    with pytest.raises(fs.SanlockException) as e:
        fs.release(LOCKSPACE_NAME, RESOURCE_NAME, [("path", MiB)], slkfd=fd)
    assert e.value.errno == errno.EPIPE


def test_acquire_no_lockspace():
    fs = FakeSanlock()
    fs.write_resource(LOCKSPACE_NAME, RESOURCE_NAME, [("path", MiB)])
    fd = fs.register()
    with pytest.raises(fs.SanlockException) as e:
        fs.acquire(LOCKSPACE_NAME, RESOURCE_NAME, [("path", MiB)], slkfd=fd)
    assert e.value.errno == errno.ENOSPC


def test_acquire_lockspace_adding():
    fs = FakeSanlock()
    fs.write_lockspace(LOCKSPACE_NAME, "path")
    fs.write_resource(LOCKSPACE_NAME, RESOURCE_NAME, [("path", MiB)])
    fs.add_lockspace(LOCKSPACE_NAME, 1, "path", wait=False)
    fd = fs.register()
    with pytest.raises(fs.SanlockException) as e:
        fs.acquire(LOCKSPACE_NAME, RESOURCE_NAME, [("path", MiB)], slkfd=fd)
    assert e.value.errno == errno.ENOSPC


def test_acquire_an_acquired_resource():
    fs = FakeSanlock()
    fs.write_lockspace(LOCKSPACE_NAME, "path")
    fs.write_resource(LOCKSPACE_NAME, RESOURCE_NAME, [("path", MiB)])
    fs.add_lockspace(LOCKSPACE_NAME, 1, "path")
    fd = fs.register()
    fs.acquire(LOCKSPACE_NAME, RESOURCE_NAME, [("path", MiB)], slkfd=fd)
    with pytest.raises(fs.SanlockException) as e:
        fs.acquire(LOCKSPACE_NAME, RESOURCE_NAME, [("path", MiB)], slkfd=fd)
    assert e.value.errno == errno.EEXIST
    res = fs.read_resource("path", MiB)
    assert res["acquired"]


def test_release():
    fs = FakeSanlock()
    fs.write_lockspace(LOCKSPACE_NAME, "path")
    fs.write_resource(LOCKSPACE_NAME, RESOURCE_NAME, [("path", MiB)])
    fs.add_lockspace(LOCKSPACE_NAME, 1, "path")
    fd = fs.register()
    fs.acquire(LOCKSPACE_NAME, RESOURCE_NAME, [("path", MiB)], slkfd=fd)
    fs.release(LOCKSPACE_NAME, RESOURCE_NAME, [("path", MiB)], slkfd=fd)
    res = fs.read_resource("path", MiB)
    assert not res["acquired"]
    # The resource has been released and the owner is zeroed
    assert res["host_id"] == 0
    assert fs.hosts[1]["generation"] == res["generation"]


def test_release_not_acquired():
    fs = FakeSanlock()
    fs.write_lockspace(LOCKSPACE_NAME, "path")
    fs.write_resource(LOCKSPACE_NAME, RESOURCE_NAME, [("path", MiB)])
    fs.add_lockspace(LOCKSPACE_NAME, 1, "path")
    fd = fs.register()
    with pytest.raises(fs.SanlockException) as e:
        fs.release(LOCKSPACE_NAME, RESOURCE_NAME, [("path", MiB)], slkfd=fd)
    assert e.value.errno == errno.EPERM


def test_release_no_lockspace():
    fs = FakeSanlock()
    with pytest.raises(fs.SanlockException) as e:
        fs.release(LOCKSPACE_NAME, RESOURCE_NAME, [("path", MiB)])
    assert e.value.errno == errno.ENOSPC


def test_inquire_no_argument():
    fs = FakeSanlock()
    with pytest.raises(fs.SanlockException):
        # Either slkfd= or pid= must be specified.
        fs.inquire()


def test_inquire_invalid_arguments():
    fs = FakeSanlock()
    with pytest.raises(fs.SanlockException):
        # Either slkfd= or pid= must be valid.
        fs.inquire(slkfd=-1, pid=-1)


def test_inquire_no_lockspace():
    fs = FakeSanlock()
    fd = fs.register()
    assert fs.inquire(slkfd=fd) == []
    assert fs.inquire(pid=os.getpid()) == []


def test_inquire_no_resources():
    fs = FakeSanlock()
    fs.write_lockspace(LOCKSPACE_NAME, "path")
    fs.add_lockspace(LOCKSPACE_NAME, 1, "path")
    fd = fs.register()
    assert fs.inquire(slkfd=fd) == []
    assert fs.inquire(pid=os.getpid()) == []


def test_inquire_some_resources():
    fs = FakeSanlock()

    # Write some lockspaces.
    fs.write_lockspace(b"s1", "path1")
    fs.write_lockspace(b"s2", "path2")
    fs.write_lockspace(b"s3", "path3")
    fs.write_lockspace(b"s4", "path4")

    # Add some lockspaces.
    fs.add_lockspace(b"s1", 1, "path1")
    fs.add_lockspace(b"s2", 1, "path2")
    fs.add_lockspace(b"s3", 1, "path3")
    fs.add_lockspace(b"s4", 1, "path4")

    # Create some resources.
    fs.write_resource(b"s1", b"r1", [("path1", 1 * MiB)])
    fs.write_resource(b"s2", b"r2", [("path2", 2 * MiB)])
    fs.write_resource(b"s3", b"r3", [("path3", 3 * MiB)])
    fs.write_resource(b"s4", b"r4", [("path4", 4 * MiB)])

    fd = fs.register()

    # Acquire some resources.
    fs.acquire(b"s1", b"r1", [("path1", 1 * MiB)], slkfd=fd)
    fs.acquire(b"s3", b"r3", [("path3", 3 * MiB)], slkfd=fd)

    # Only acquired resources should be reported.
    expected = [
        {
            "lockspace": b"s1",
            "resource": b"r1",
            "flags": fs.RES_LVER,
            "version": 0,
            "disks": [("path1", 1 * MiB)]
        },
        {
            "lockspace": b"s3",
            "resource": b"r3",
            "flags": fs.RES_LVER,
            "version": 0,
            "disks": [("path3", 3 * MiB)]
        },
    ]

    # We can query using slkfd= or pid=.
    assert fs.inquire(slkfd=fd) == expected
    assert fs.inquire(pid=os.getpid()) == expected


def test_inquire_busy():
    fs = FakeSanlock()
    fd = fs.register()

    # Add an acquired resource.
    fs.write_lockspace(b"s1", "path1")
    fs.add_lockspace(b"s1", 1, "path1")
    fs.write_resource(b"s1", b"r1", [("path1", 1 * MiB)])
    fs.acquire(b"s1", b"r1", [("path1", 1 * MiB)], slkfd=fd)

    # Simulate a busy resource.
    fs.resources[("path1", MiB)]["busy"] = True

    with pytest.raises(fs.SanlockException) as e:
        fs.inquire(slkfd=fd)
    assert e.value.errno == errno.EBUSY


def test_inquire_slkfd_closed():
    fs = FakeSanlock()
    fd = fs.register()
    # Simulate slkfd closed by sanlock daemon.
    fs.process_socket.close()
    with pytest.raises(fs.SanlockException) as e:
        fs.inquire(slkfd=fd)
    assert e.value.errno == errno.EPIPE


def test_read_resource_owners_lockspace_not_initialized():
    fs = FakeSanlock()
    fs.write_resource(LOCKSPACE_NAME, RESOURCE_NAME, [("path", MiB)])
    with pytest.raises(fs.SanlockException) as e:
        fs.read_resource_owners(LOCKSPACE_NAME, RESOURCE_NAME, [("path", MiB)])
    assert e.value.errno == errno.EINVAL


def test_read_resource_owners_no_owner():
    fs = FakeSanlock()
    fs.write_lockspace(LOCKSPACE_NAME, "path")
    fs.write_resource(LOCKSPACE_NAME, RESOURCE_NAME, [("path", MiB)])
    fs.add_lockspace(LOCKSPACE_NAME, 1, "path")
    owners = fs.read_resource_owners(LOCKSPACE_NAME,
                                     RESOURCE_NAME,
                                     [("path", MiB)])
    assert len(owners) == 0


def test_read_resource_owners():
    fs = FakeSanlock()
    fs.write_lockspace(LOCKSPACE_NAME, "path")
    fs.write_resource(LOCKSPACE_NAME, RESOURCE_NAME, [("path", MiB)])
    fs.add_lockspace(LOCKSPACE_NAME, 1, "path")
    fd = fs.register()
    fs.acquire(LOCKSPACE_NAME, RESOURCE_NAME, [("path", MiB)], slkfd=fd)
    owners = fs.read_resource_owners(LOCKSPACE_NAME,
                                     RESOURCE_NAME,
                                     [("path", MiB)])
    assert len(owners) == 1
    assert owners[0]["host_id"] == 1
    assert owners[0]["generation"] == 0


def test_read_resource_owners_resource_released():
    fs = FakeSanlock()
    fs.write_lockspace(LOCKSPACE_NAME, "path")
    fs.write_resource(LOCKSPACE_NAME, RESOURCE_NAME, [("path", MiB)])
    fs.add_lockspace(LOCKSPACE_NAME, 1, "path")
    fd = fs.register()
    fs.acquire(LOCKSPACE_NAME, RESOURCE_NAME, [("path", MiB)], slkfd=fd)
    fs.release(LOCKSPACE_NAME, RESOURCE_NAME, [("path", MiB)], slkfd=fd)
    owners = fs.read_resource_owners(
        LOCKSPACE_NAME, RESOURCE_NAME, [("path", MiB)])
    assert owners == []


def test_read_resource_owners_lockspace_removed():
    fs = FakeSanlock()
    fs.write_lockspace(LOCKSPACE_NAME, "path")
    fs.write_resource(LOCKSPACE_NAME, RESOURCE_NAME, [("path", MiB)])
    fs.add_lockspace(LOCKSPACE_NAME, 1, "path")
    fd = fs.register()
    fs.acquire(LOCKSPACE_NAME, RESOURCE_NAME, [("path", MiB)], slkfd=fd)
    fs.release(LOCKSPACE_NAME, RESOURCE_NAME, [("path", MiB)], slkfd=fd)
    fs.rem_lockspace(LOCKSPACE_NAME, 1, "path")
    owners = fs.read_resource_owners(
        LOCKSPACE_NAME, RESOURCE_NAME, [("path", MiB)])
    assert owners == []


@pytest.mark.parametrize("align, sector", INVALID_ALIGN_SECTOR)
def test_read_resource_owners_invalid_align_sector(align, sector):
    fs = FakeSanlock()
    fs.write_lockspace(LOCKSPACE_NAME, "path")
    fs.write_resource(LOCKSPACE_NAME, RESOURCE_NAME, [("path", MiB)])
    disks = [("path", MiB)]
    with pytest.raises(ValueError):
        fs.read_resource_owners(
            LOCKSPACE_NAME, RESOURCE_NAME, disks, align=align, sector=sector)


@pytest.mark.parametrize("align, sector", WRONG_ALIGN_SECTOR)
def test_read_resource_owners_wrong_align_sector(align, sector):
    fs = FakeSanlock()
    fs.write_lockspace(LOCKSPACE_NAME, "path")
    fs.write_resource(LOCKSPACE_NAME, RESOURCE_NAME, [("path", MiB)])
    disks = [("path", MiB)]
    with pytest.raises(fs.SanlockException) as e:
        fs.read_resource_owners(
            LOCKSPACE_NAME, RESOURCE_NAME, disks, align=align, sector=sector)
    assert e.value.errno == errno.EINVAL


@pytest.mark.parametrize(
    "disk_sector, sanlock_sector", DIFFERENT_DISK_SANLOCK_SECTOR)
def test_read_resource_owners_wrong_sector(disk_sector, sanlock_sector):
    fs = FakeSanlock(disk_sector)
    fs.write_lockspace(LOCKSPACE_NAME, "path", sector=disk_sector)
    fs.write_resource(
        LOCKSPACE_NAME, RESOURCE_NAME, [("path", MiB)], sector=disk_sector)
    disks = [("path", MiB)]
    with pytest.raises(fs.SanlockException) as e:
        fs.read_resource_owners(
            LOCKSPACE_NAME, RESOURCE_NAME, disks, sector=sanlock_sector)
    assert e.value.errno == errno.EINVAL


def test_get_hosts():
    fs = FakeSanlock()
    fs.write_lockspace(LOCKSPACE_NAME, "path")
    fs.add_lockspace(LOCKSPACE_NAME, 1, "path")
    host = fs.get_hosts(LOCKSPACE_NAME, 1)
    assert host[0]["id"] == 1
    assert host[0]["generation"] == 0


def test_get_hosts_no_lockspace():
    fs = FakeSanlock()
    with pytest.raises(fs.SanlockException) as e:
        fs.get_hosts(LOCKSPACE_NAME, 1)
    assert e.value.errno == errno.ENOSPC


def test_add_lockspace_generation_increase():
    fs = FakeSanlock()
    fs.write_lockspace(LOCKSPACE_NAME, "path")
    fs.write_resource(LOCKSPACE_NAME, RESOURCE_NAME, [("path", MiB)])
    fs.add_lockspace(LOCKSPACE_NAME, 1, "path")
    fs.rem_lockspace(LOCKSPACE_NAME, 1, "path")
    fs.add_lockspace(LOCKSPACE_NAME, 1, "path")
    host = fs.get_hosts(LOCKSPACE_NAME, 1)
    assert host[0]["id"] == 1
    assert host[0]["generation"] == 1
    assert host[0]["flags"] == sanlock.HOST_LIVE


def test_write_lockspace():
    lockspace = LOCKSPACE_NAME
    fs = FakeSanlock()

    assert lockspace not in fs.spaces

    fs.write_lockspace(lockspace, "/var/tmp/test", offset=0, max_hosts=1)

    expected = {
        "path": "/var/tmp/test",
        "offset": 0,
        "max_hosts": 1,
        "iotimeout": 0,
        "align": sc.ALIGNMENT_1M,
        "sector": sc.BLOCK_SIZE_512,
    }
    assert expected == fs.spaces[lockspace]


@pytest.mark.parametrize("align, sector", INVALID_ALIGN_SECTOR)
def test_write_lockspace_invalid_align_sector(align, sector):
    fs = FakeSanlock()
    with pytest.raises(ValueError):
        fs.write_lockspace(LOCKSPACE_NAME, "path", align=align, sector=sector)


def test_write_resource():
    fs = FakeSanlock()
    fs.write_lockspace(LOCKSPACE_NAME, "path")
    fs.write_resource(LOCKSPACE_NAME, RESOURCE_NAME, [("path", MiB)])
    info = fs.read_resource("path", MiB)
    expected = {
        "resource": RESOURCE_NAME,
        "lockspace": LOCKSPACE_NAME,
        "version": 0,
        "acquired": False,
        "align": sc.ALIGNMENT_1M,
        "sector": sc.BLOCK_SIZE_512,
    }
    assert info == expected


def test_dump_lockspace():
    fs = FakeSanlock()
    fs.write_lockspace(b'lockspace1', 'ids')
    fs.write_lockspace(b'lockspace2', 'ids', offset=sc.ALIGNMENT_1M)

    dump = list(fs.dump_lockspace('ids'))
    assert dump == [{
        'offset': i * sc.ALIGNMENT_1M,
        'lockspace': 'lockspace{}'.format(i + 1),
        'resource': 0,
        'timestamp': 0,
        'own': 0,
        'gen': 0
    } for i in range(2)]

    dump = list(fs.dump_lockspace('ids', offset=sc.ALIGNMENT_1M))
    assert dump == [{
        'offset': sc.ALIGNMENT_1M,
        'lockspace': 'lockspace2',
        'resource': 0,
        'timestamp': 0,
        'own': 0,
        'gen': 0
    }]

    dump = list(fs.dump_lockspace('ids', offset=0, size=sc.ALIGNMENT_1M))
    assert dump == [{
        'offset': 0,
        'lockspace': 'lockspace1',
        'resource': 0,
        'timestamp': 0,
        'own': 0,
        'gen': 0
    }]


def test_dump_leases():
    fs = FakeSanlock()
    fs.write_lockspace(LOCKSPACE_NAME, 'ids')
    fs.write_resource(LOCKSPACE_NAME, b'resource1', [('leases', 0)])
    fs.write_resource(
        LOCKSPACE_NAME, b'resource2', [('leases', sc.ALIGNMENT_1M)])

    dump = list(fs.dump_leases('leases'))
    assert dump == [{
        'offset': i * sc.ALIGNMENT_1M,
        'lockspace': LOCKSPACE_NAME.decode('utf-8'),
        'resource': 'resource{}'.format(i + 1),
        'timestamp': 0,
        'own': 0,
        'gen': 0,
        'lver': 0
    } for i in range(2)]

    dump = list(fs.dump_leases('leases', offset=sc.ALIGNMENT_1M))
    assert dump == [{
        'offset': sc.ALIGNMENT_1M,
        'lockspace': LOCKSPACE_NAME.decode('utf-8'),
        'resource': 'resource2',
        'timestamp': 0,
        'own': 0,
        'gen': 0,
        'lver': 0
    }]

    dump = list(fs.dump_leases('leases', offset=0, size=sc.ALIGNMENT_1M))
    assert dump == [{
        'offset': 0,
        'lockspace': LOCKSPACE_NAME.decode('utf-8'),
        'resource': 'resource1',
        'timestamp': 0,
        'own': 0,
        'gen': 0,
        'lver': 0
    }]


def test_add_without_init_lockpsace():
    fs = FakeSanlock()
    with pytest.raises(fs.SanlockException) as e:
        fs.add_lockspace(LOCKSPACE_NAME, 1, "path")
    assert e.value.errno == fs.SANLK_LEADER_MAGIC


def test_add_lockspace_twice():
    fs = FakeSanlock()
    fs.write_lockspace(LOCKSPACE_NAME, "path")
    fs.add_lockspace(LOCKSPACE_NAME, 1, "path")
    with pytest.raises(fs.SanlockException) as e:
        fs.add_lockspace(LOCKSPACE_NAME, 1, "path")
    assert e.value.errno == errno.EEXIST


def test_add_lockspace_wrong_path():
    fs = FakeSanlock()
    fs.write_lockspace(LOCKSPACE_NAME, "path")
    with pytest.raises(fs.SanlockException) as e:
        fs.add_lockspace(LOCKSPACE_NAME, 1, "path2", )
    assert e.value.errno == errno.EINVAL


def test_add_lockspace_wrong_offset():
    fs = FakeSanlock()
    fs.write_lockspace(LOCKSPACE_NAME, "path")
    with pytest.raises(fs.SanlockException) as e:
        fs.add_lockspace(LOCKSPACE_NAME, 1, "path", offset=42)
    assert e.value.errno == errno.EINVAL


def test_add_lockspace_validate_bytes():
    fs = FakeSanlock()
    with pytest.raises(TypeError):
        fs.add_lockspace(u"lockspace_name", 1, "path")


def test_rem_lockspace_validate_bytes():
    fs = FakeSanlock()
    with pytest.raises(TypeError):
        fs.rem_lockspace(u"lockspace_name", 1, "path")


def test_write_lockspace_validate_bytes():
    fs = FakeSanlock()
    with pytest.raises(TypeError):
        fs.write_lockspace(u"lockspace_name", "path")


def test_write_resource_validate_bytes():
    fs = FakeSanlock()
    with pytest.raises(TypeError):
        fs.write_resource(LOCKSPACE_NAME, u"resouce_name", [("path", 0)])

    with pytest.raises(TypeError):
        fs.write_resource(
            u"lockspace_name", RESOURCE_NAME, [("path", 0)])


def test_acquire_resource_validate_bytes():
    fs = FakeSanlock()
    with pytest.raises(TypeError):
        fs.acquire(u"lockspace_name", RESOURCE_NAME, [("path", 0)])

    with pytest.raises(TypeError):
        fs.acquire(LOCKSPACE_NAME, u"resource_name", [("path", 0)])


def test_release_resource_validate_bytes():
    fs = FakeSanlock()
    with pytest.raises(TypeError):
        fs.release(u"lockspace_name", RESOURCE_NAME, [("path", 0)])

    with pytest.raises(TypeError):
        fs.acquire(LOCKSPACE_NAME, u"resource_name", [("path", 0)])


def test_get_hosts_validate_bytes():
    fs = FakeSanlock()
    with pytest.raises(TypeError):
        fs.get_hosts(u"lockspace_name", 1)


def test_read_resource_owners_validate_bytes():
    fs = FakeSanlock()
    with pytest.raises(TypeError):
        fs.read_resource_owners(
            u"lockspace_name", RESOURCE_NAME, [("path", 0)], sector=0)

    with pytest.raises(TypeError):
        fs.read_resource_owners(
            LOCKSPACE_NAME, u"resource_name", [("path", 0)], sector=0)


@pytest.mark.parametrize(
    "sector_size", [sc.BLOCK_SIZE_512, sc.BLOCK_SIZE_4K])
def test_get_set_lvb_after_acquire(sector_size):
    fs = FakeSanlock(sector_size=sector_size)
    fs.write_lockspace(LOCKSPACE_NAME, "path", sector=sector_size)
    fs.write_resource(LOCKSPACE_NAME, RESOURCE_NAME, [("path", MiB)])
    fs.add_lockspace(LOCKSPACE_NAME, 1, "path")
    fd = fs.register()
    fs.acquire(LOCKSPACE_NAME, RESOURCE_NAME, [("path", MiB)], slkfd=fd,
               lvb=True)

    # get empty lvb
    data = fs.get_lvb(
        LOCKSPACE_NAME, RESOURCE_NAME, [("path", MiB)], size=512)
    assert data == b"\0" * 512

    data = b"{generation:0}"
    fs.set_lvb(LOCKSPACE_NAME, RESOURCE_NAME, [("path", MiB)],
               data.ljust(512, b"\0"))
    lvb_data = fs.get_lvb(LOCKSPACE_NAME, RESOURCE_NAME, [("path", MiB)],
                          sector_size)

    # make sure the uninitialized area is poisoned
    assert lvb_data == data.ljust(512, b"\0").ljust(sector_size, b"x")

    lvb_data = fs.get_lvb(LOCKSPACE_NAME, RESOURCE_NAME, [("path", MiB)],
                          len(data))

    assert lvb_data == data


def test_set_lvb_without_acquire():
    fs = FakeSanlock()
    fs.write_lockspace(LOCKSPACE_NAME, "path")
    fs.write_resource(LOCKSPACE_NAME, RESOURCE_NAME, [("path", MiB)])
    fs.add_lockspace(LOCKSPACE_NAME, 1, "path")

    with pytest.raises(fs.SanlockException) as e:
        fs.set_lvb(LOCKSPACE_NAME, RESOURCE_NAME, [("path", MiB)], b"data")
    assert e.value.errno == errno.ENOENT


def test_set_lvb_without_lockspace():
    fs = FakeSanlock()
    with pytest.raises(fs.SanlockException) as e:
        fs.set_lvb(LOCKSPACE_NAME, RESOURCE_NAME, [("path", MiB)], b"data")
    assert e.value.errno == errno.ENOSPC


@pytest.mark.parametrize(
    "sector_size", [sc.BLOCK_SIZE_512, sc.BLOCK_SIZE_4K])
def test_get_lvb_invalid_size(sector_size):
    fs = FakeSanlock(sector_size=sector_size)
    fs.write_lockspace(LOCKSPACE_NAME, "path", sector=sector_size)
    fs.write_resource(LOCKSPACE_NAME, RESOURCE_NAME, [("path", MiB)])
    fs.add_lockspace(LOCKSPACE_NAME, 1, "path")
    fd = fs.register()
    fs.acquire(LOCKSPACE_NAME, RESOURCE_NAME, [("path", MiB)], slkfd=fd,
               lvb=True)
    with pytest.raises(ValueError):
        fs.get_lvb(LOCKSPACE_NAME, RESOURCE_NAME, [("path", MiB)], size=4097)


@pytest.mark.parametrize(
    "sector_size", [sc.BLOCK_SIZE_512, sc.BLOCK_SIZE_4K])
def test_set_lvb_too_big(sector_size):
    fs = FakeSanlock(sector_size=sector_size)
    fs.write_lockspace(LOCKSPACE_NAME, "path", sector=sector_size)
    fs.write_resource(LOCKSPACE_NAME, RESOURCE_NAME, [("path", MiB)])
    fs.add_lockspace(LOCKSPACE_NAME, 1, "path")
    fd = fs.register()
    fs.acquire(LOCKSPACE_NAME, RESOURCE_NAME, [("path", MiB)], slkfd=fd,
               lvb=True)
    with pytest.raises(fs.SanlockException) as e:
        fs.set_lvb(LOCKSPACE_NAME, RESOURCE_NAME, [("path", MiB)],
                   data=b"a" * 4097)
    assert e.value.errno == errno.E2BIG
