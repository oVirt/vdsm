# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import
from __future__ import division

import collections

import pytest
import userstorage

from vdsm.storage import constants as sc
from vdsm.storage import fileSD
from vdsm.storage import outOfProcess as oop
from vdsm.storage import sd


EXAMPLE_DATA = {
    (sc.BLOCK_SIZE_512, sc.ALIGNMENT_1M): """\
ALIGNMENT=1048576
BLOCK_SIZE=512
CLASS=Data
DESCRIPTION=storage domain
IOOPTIMEOUTSEC=10
LEASERETRIES=3
LEASETIMESEC=60
LOCKPOLICY=
LOCKRENEWALINTERVALSEC=5
POOL_UUID=
REMOTE_PATH=server:/path
ROLE=Regular
SDUUID=275766cb-c7d8-43d6-a663-4e52160de620
TYPE=LOCALFS
VERSION=5
_SHA_CKSUM=9336f77705ff9e3d5faf65c4d0dc818a29de458d
""",
    (sc.BLOCK_SIZE_4K, sc.ALIGNMENT_1M): """\
ALIGNMENT=1048576
BLOCK_SIZE=4096
CLASS=Data
DESCRIPTION=storage domain
IOOPTIMEOUTSEC=10
LEASERETRIES=3
LEASETIMESEC=60
LOCKPOLICY=
LOCKRENEWALINTERVALSEC=5
POOL_UUID=
REMOTE_PATH=server:/path
ROLE=Regular
SDUUID=275766cb-c7d8-43d6-a663-4e52160de620
TYPE=LOCALFS
VERSION=5
_SHA_CKSUM=7910a564cc0a340f93aea5680e0e9c2d6ff4903a
""",
    (sc.BLOCK_SIZE_4K, sc.ALIGNMENT_2M): """\
ALIGNMENT=2097152
BLOCK_SIZE=4096
CLASS=Data
DESCRIPTION=storage domain
IOOPTIMEOUTSEC=10
LEASERETRIES=3
LEASETIMESEC=60
LOCKPOLICY=
LOCKRENEWALINTERVALSEC=5
POOL_UUID=
REMOTE_PATH=server:/path
ROLE=Regular
SDUUID=275766cb-c7d8-43d6-a663-4e52160de620
TYPE=LOCALFS
VERSION=5
_SHA_CKSUM=769baef6c65aeef08cf6d177c2c44046b2aac877
""",
}
BACKENDS = userstorage.load_config("storage.py").BACKENDS

Storage = collections.namedtuple("Storage", "path, block_size, alignment")


@pytest.fixture(
    params=[
        pytest.param(
            (BACKENDS["file-512"], sc.ALIGNMENT_1M),
            id="file-512-1m"),
        pytest.param(
            (BACKENDS["file-4k"], sc.ALIGNMENT_1M),
            id="file-4k-1m"),
        pytest.param(
            (BACKENDS["file-4k"], sc.ALIGNMENT_2M),
            id="file-4k-2m"),
    ],
)
def storage(request):
    backend, alignment = request.param
    with backend:
        yield Storage(backend.path, backend.sector_size, alignment)
        oop.stop()


def make_metadata(storage):
    """
    Create metadata dict with file storage domain metadata.
    """
    lease = sd.DEFAULT_LEASE_PARAMS
    return {
        fileSD.REMOTE_PATH: "server:/path",
        sd.DMDK_ALIGNMENT: storage.alignment,
        sd.DMDK_BLOCK_SIZE: storage.block_size,
        sd.DMDK_CLASS: sd.DATA_DOMAIN,
        sd.DMDK_DESCRIPTION: "storage domain",
        sd.DMDK_IO_OP_TIMEOUT_SEC: lease[sd.DMDK_IO_OP_TIMEOUT_SEC],
        sd.DMDK_LEASE_RETRIES: lease[sd.DMDK_LEASE_RETRIES],
        sd.DMDK_LEASE_TIME_SEC: lease[sd.DMDK_LEASE_TIME_SEC],
        sd.DMDK_LOCK_POLICY: "",
        sd.DMDK_LOCK_RENEWAL_INTERVAL_SEC:
            lease[sd.DMDK_LOCK_RENEWAL_INTERVAL_SEC],
        sd.DMDK_POOLS: [],
        sd.DMDK_ROLE: sd.REGULAR_DOMAIN,
        sd.DMDK_SDUUID: "275766cb-c7d8-43d6-a663-4e52160de620",
        sd.DMDK_TYPE: sd.LOCALFS_DOMAIN,
        sd.DMDK_VERSION: 5,
    }


def test_write(storage):
    metadata = make_metadata(storage)

    # Check that md is empty when storage is empty.
    md = fileSD.FileSDMetadata(storage.path)
    assert md.copy() == {}

    # Update metadata.
    md.update(metadata)

    # Check that memory state was modifed.
    assert md.copy() == metadata

    # Check that storage was modifed.
    with open(storage.path, "rb") as f:
        data = f.read().decode("utf-8")
    assert data == EXAMPLE_DATA[(storage.block_size, storage.alignment)]


def test_read(storage):
    data = EXAMPLE_DATA[(storage.block_size, storage.alignment)]
    with open(storage.path, "wb") as f:
        f.write(data.encode("utf-8"))

    # Check that metadata loaded from storage.
    md = fileSD.FileSDMetadata(storage.path)

    metadata = make_metadata(storage)
    assert md.copy() == metadata
    assert md[sd.DMDK_VERSION] == metadata[sd.DMDK_VERSION]


def test_read_strip_zero_padding(storage):
    data = EXAMPLE_DATA[(storage.block_size, storage.alignment)]

    # With Gluster we may get extra padding when reading metadata from storage.
    # Simulate this by padding file data when writing.
    # https://bugzilla.redhat.com/1737141
    padded = data.ljust(storage.block_size, "\0")

    with open(storage.path, "wb") as f:
        f.write(padded.encode("utf-8"))

    # Check that metadata loaded from storage.
    md = fileSD.FileSDMetadata(storage.path)

    metadata = make_metadata(storage)
    assert md.copy() == metadata
    assert md[sd.DMDK_VERSION] == metadata[sd.DMDK_VERSION]


def test_update(storage):
    data = EXAMPLE_DATA[(storage.block_size, storage.alignment)]
    with open(storage.path, "wb") as f:
        f.write(data.encode("utf-8"))

    # Prepare metadata with some changes.
    metadata = make_metadata(storage)
    changes = {
        sd.DMDK_VERSION: 6,
        sd.DMDK_DESCRIPTION: "better domain",
    }
    metadata.update(changes)

    # Update md.
    md = fileSD.FileSDMetadata(storage.path)
    md.update(changes)

    # Check that memory state was changed.
    assert md.copy() == metadata

    # Check that changes written to storage.
    md = fileSD.FileSDMetadata(storage.path)
    assert md.copy() == metadata


def test_transaction(storage):
    data = EXAMPLE_DATA[(storage.block_size, storage.alignment)]
    with open(storage.path, "wb") as f:
        f.write(data.encode("utf-8"))

    # Prepare metadata with some changes.
    metadata = make_metadata(storage)
    metadata[sd.DMDK_VERSION] = 6
    metadata[sd.DMDK_DESCRIPTION] = "better domain"

    # Change mulitple keys in one transaction.
    md = fileSD.FileSDMetadata(storage.path)
    with md.transaction():
        md[sd.DMDK_VERSION] = metadata[sd.DMDK_VERSION]
        md[sd.DMDK_DESCRIPTION] = metadata[sd.DMDK_DESCRIPTION]

    # Check that memory state was changed.
    assert md.copy() == metadata

    # Check that changes written to storage.
    md = fileSD.FileSDMetadata(storage.path)
    assert md.copy() == metadata


def test_invalidate(storage):
    data = EXAMPLE_DATA[(storage.block_size, storage.alignment)]
    with open(storage.path, "wb") as f:
        f.write(data.encode("utf-8"))

    # Read initial metadata.
    md = fileSD.FileSDMetadata(storage.path)
    initial_metadata = md.copy()

    # Simulate another host changing data on storage...
    other_md = fileSD.FileSDMetadata(storage.path)
    other_md[sd.DMDK_VERSION] = 6
    new_metadata = other_md.copy()

    # Check that reading return cached data.
    assert md.copy() == initial_metadata

    # Check that invalidating read data from storage.
    md.invalidate()
    assert md.copy() == new_metadata
