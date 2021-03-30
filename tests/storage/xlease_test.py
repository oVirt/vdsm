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
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA
#
# Refer to the README and COPYING files for full details of the license
#

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import functools
import io
import mmap
import os
import timeit

import pytest

from vdsm import utils
from vdsm.common.units import GiB
from vdsm.storage import constants as sc
from vdsm.storage import exception as se
from vdsm.storage import outOfProcess as oop
from vdsm.storage import xlease

from testlib import make_uuid

from . fakesanlock import FakeSanlock
from . import userstorage


class ReadError(Exception):
    """ Raised to simulate read errors """


class WriteError(Exception):
    """ Raised to simulate read errors """


class FailingReader(xlease.DirectFile):
    def pread(self, offset, buf):
        raise ReadError


class FailingWriter(xlease.DirectFile):
    def pwrite(self, offset, buf):
        raise WriteError


@pytest.fixture(
    scope="module",
    params=[
        userstorage.PATHS["file-512"],
        userstorage.PATHS["file-4k"],
    ],
    ids=str,
)
def user_storage(request):
    storage = request.param
    if not storage.exists():
        pytest.xfail("{} storage not available".format(storage.name))
    return storage


class TemporaryVolume(object):
    """
    Temporary xleases volume.
    """

    def __init__(
            self,
            backend,
            alignment,
            block_size=4096,
            max_records=xlease.MAX_RECORDS):
        """
        Zero storage and format index area.
        """
        self.backend = backend
        self.path = backend.name
        self.block_size = block_size
        self.alignment = alignment
        self.max_records = max_records
        self.lockspace = make_uuid()
        if os.path.exists(self.path):
            self.zero_storage()
        self.format_index()

    def write_records(self, *records):
        """
        Write records to volume index area.
        """
        index = xlease.VolumeIndex(self.alignment, self.block_size)
        with utils.closing(index):
            index.load(self.backend)
            for recnum, record in records:
                block = index.copy_record_block(recnum)
                with utils.closing(block):
                    block.write_record(recnum, record)
                    block.dump(self.backend)

    def zero_storage(self):
        # TODO: suport block storage.
        with io.open(self.path, "wb") as f:
            f.truncate(GiB)

    def format_index(self):
        xlease.format_index(
            self.lockspace,
            self.backend,
            alignment=self.alignment,
            block_size=self.block_size,
            max_records=self.max_records)

    def close(self):
        self.backend.close()


@pytest.fixture(params=[
    pytest.param(
        (userstorage.PATHS["file-512"], sc.ALIGNMENT_1M),
        id="file-512-1m"),
    pytest.param(
        (userstorage.PATHS["file-4k"], sc.ALIGNMENT_1M),
        id="file-4k-1m"),
    pytest.param(
        (userstorage.PATHS["file-4k"], sc.ALIGNMENT_2M),
        id="file-4k-2m"),
    pytest.param(
        (userstorage.PATHS["file-4k"], sc.ALIGNMENT_4M),
        id="file-4k-4m"),
    pytest.param(
        (userstorage.PATHS["file-4k"], sc.ALIGNMENT_8M),
        id="file-4k-8m"),
])
def tmp_vol(request):
    storage, alignment = request.param
    if not storage.exists():
        pytest.xfail("{} storage not available".format(storage.name))
    backend = xlease.DirectFile(storage.path)
    tv = TemporaryVolume(backend, alignment, block_size=storage.sector_size)

    yield tv
    tv.close()


@pytest.fixture
def memory_vol():
    """
    Provides a memory backend with limited index that fits 56 records only.
    Useful for tests that need to fill up the index and run out of space.
    """
    # For fast testing, use tiny index fitting in 4096 bytes without space
    # for actual sanlock resources.
    max_records = (4096 - xlease.METADATA_SIZE) // xlease.RECORD_SIZE

    # The first slot in the volume is the lockspace slot.
    backend = xlease.MemoryBackend(size=sc.ALIGNMENT_1M + 4096)

    memory_volume = TemporaryVolume(
        backend,
        alignment=sc.ALIGNMENT_1M,
        max_records=max_records)

    yield memory_volume
    memory_volume.close()


@pytest.fixture
def fake_sanlock(monkeypatch, tmp_vol):
    sanlock = FakeSanlock(sector_size=tmp_vol.block_size)
    monkeypatch.setattr(xlease, "sanlock", sanlock)
    yield sanlock


def check_lease(lease, lease_id, resource, volume):
    assert lease.lockspace == volume.lockspace
    assert lease.resource == lease_id
    assert lease.path == volume.path
    assert resource["lockspace"] == lease.lockspace.encode("utf-8")
    assert resource["resource"] == lease.resource.encode("utf-8")


class TestIndex:

    def test_metadata(self, tmp_vol, monkeypatch):
        monkeypatch.setattr("time.time", lambda: 123456789)
        tmp_vol.format_index()
        vol = xlease.LeasesVolume(
            tmp_vol.backend,
            alignment=tmp_vol.alignment,
            block_size=tmp_vol.block_size)
        with utils.closing(vol):
            assert vol.version == 1
            assert vol.lockspace == tmp_vol.lockspace
            assert vol.mtime == 123456789

    def test_magic_big_endian(self, tmp_vol):
        with io.open(tmp_vol.path, "rb") as f:
            f.seek(tmp_vol.alignment)
            assert f.read(4) == b"\x12\x15\x20\x16"

    def test_bad_magic(self, tmp_vol):
        tmp_vol.zero_storage()
        with pytest.raises(xlease.InvalidIndex):
            xlease.LeasesVolume(
                tmp_vol.backend,
                alignment=tmp_vol.alignment,
                block_size=tmp_vol.block_size).close()

    def test_bad_version(self, tmp_vol):
        with io.open(tmp_vol.path, "r+b") as f:
            f.seek(tmp_vol.alignment + 5)
            f.write(b"blah")

        with pytest.raises(xlease.InvalidIndex):
            xlease.LeasesVolume(
                tmp_vol.backend,
                alignment=tmp_vol.alignment,
                block_size=tmp_vol.block_size).close()

    def test_unsupported_version(self, tmp_vol):
        md = xlease.IndexMetadata(2, "lockspace")
        with io.open(tmp_vol.path, "r+b") as f:
            f.seek(tmp_vol.alignment)
            f.write(md.bytes())

        with pytest.raises(xlease.InvalidIndex):
            xlease.LeasesVolume(
                tmp_vol.backend,
                alignment=tmp_vol.alignment,
                block_size=tmp_vol.block_size).close()

    def test_bad_lockspace(self, tmp_vol):
        with io.open(tmp_vol.path, "r+b") as f:
            f.seek(tmp_vol.alignment + 10)
            f.write(b"\xf0")

        with pytest.raises(xlease.InvalidIndex):
            xlease.LeasesVolume(
                tmp_vol.backend,
                alignment=tmp_vol.alignment,
                block_size=tmp_vol.block_size).close()

    def test_bad_mtime(self, tmp_vol):
        with io.open(tmp_vol.path, "r+b") as f:
            f.seek(tmp_vol.alignment + 59)
            f.write(b"not a number")

        with pytest.raises(xlease.InvalidIndex):
            xlease.LeasesVolume(
                tmp_vol.backend,
                alignment=tmp_vol.alignment,
                block_size=tmp_vol.block_size).close()

    def test_updating(self, tmp_vol):
        md = xlease.IndexMetadata(
            xlease.INDEX_VERSION, "lockspace", updating=True)
        with io.open(tmp_vol.path, "r+b") as f:
            f.seek(tmp_vol.alignment)
            f.write(md.bytes())

        with pytest.raises(xlease.InvalidIndex):
            xlease.LeasesVolume(
                tmp_vol.backend,
                alignment=tmp_vol.alignment,
                block_size=tmp_vol.block_size).close()

    def test_truncated_index(self, tmp_vol):
        # Truncate index, reading it should fail.
        with io.open(tmp_vol.path, "r+b") as f:
            f.truncate(
                tmp_vol.alignment + xlease.INDEX_SIZE - tmp_vol.block_size)

        with pytest.raises(xlease.InvalidIndex):
            xlease.LeasesVolume(
                tmp_vol.backend,
                alignment=tmp_vol.alignment,
                block_size=tmp_vol.block_size).close()

    def test_empty(self, tmp_vol):
        vol = xlease.LeasesVolume(
            tmp_vol.backend,
            alignment=tmp_vol.alignment,
            block_size=tmp_vol.block_size)
        with utils.closing(vol):
            assert vol.leases() == {}

    def test_rebuild_missing_resources(self, tmp_vol, fake_sanlock):
        vol = xlease.LeasesVolume(
            tmp_vol.backend,
            alignment=tmp_vol.alignment,
            block_size=tmp_vol.block_size)

        # Create a few leases.
        lease_ids = []
        for _ in range(6):
            lease_id = make_uuid()
            vol.add(lease_id)
            lease_ids.append(lease_id)

        # Remove some of the leases to create "holes".
        vol.remove(lease_ids[0])
        vol.remove(lease_ids[1])
        vol.remove(lease_ids[4])

        # Get info on all the leases that are left.
        leases = [vol.lookup(lease) for lease in vol.leases()]

        # Wipe fake sanlock resources.
        fake_sanlock.resources = {}

        # Rebuild sanlock resources from index.
        xlease.rebuild_index(
            vol.lockspace,
            tmp_vol.backend,
            alignment=tmp_vol.alignment,
            block_size=tmp_vol.block_size)

        # Verify resources can be found after rebuild.
        for lease in leases:
            res = fake_sanlock.read_resource(
                lease.path,
                lease.offset,
                align=tmp_vol.alignment,
                sector=tmp_vol.block_size)
            check_lease(lease, lease.resource, res, vol)

    def test_rebuild_leftover_resource(self, tmp_vol, fake_sanlock):
        lease_id = make_uuid()
        vol = xlease.LeasesVolume(
            tmp_vol.backend,
            alignment=tmp_vol.alignment,
            block_size=tmp_vol.block_size)

        # Add a lease as usual, writing resource to sanlock.
        vol.add(lease_id)
        offset = vol.leases()[lease_id]['offset']
        with utils.closing(vol):

            # Make sanlock fail to write/remove a resource.
            fake_sanlock.errors["write_resource"] = \
                fake_sanlock.SanlockException

            # Removal will not raise - this will remove the record from index
            # but due to sanlock write failure the resource will be left over.
            vol.remove(lease_id)

        # Check we can still find sanlock resource.
        fake_sanlock.errors = {}
        fake_sanlock.read_resource(
            tmp_vol.backend.name,
            offset,
            align=tmp_vol.alignment,
            sector=tmp_vol.block_size)

        # Rebuild index.
        xlease.rebuild_index(
            vol.lockspace,
            tmp_vol.backend,
            alignment=tmp_vol.alignment,
            block_size=tmp_vol.block_size)

        # Reload volume and check the lease is gone.
        vol = xlease.LeasesVolume(
            tmp_vol.backend,
            alignment=tmp_vol.alignment,
            block_size=tmp_vol.block_size)
        with pytest.raises(se.NoSuchLease):
            vol.lookup(lease_id)

        # Check bogus lease is gone from sanlock and resource cleared.
        res = fake_sanlock.read_resource(
            tmp_vol.backend.name,
            offset,
            align=tmp_vol.alignment,
            sector=tmp_vol.block_size)
        assert not res['resource']
        assert not res['lockspace']

    def test_rebuild_restore_existing_resource(self, tmp_vol, fake_sanlock):
        lease_id1 = make_uuid()
        lease_id2 = make_uuid()
        lease_id3 = make_uuid()

        vol = xlease.LeasesVolume(
            tmp_vol.backend,
            alignment=tmp_vol.alignment,
            block_size=tmp_vol.block_size)
        vol.add(lease_id1)
        vol.add(lease_id2)
        vol.add(lease_id3)

        lease1 = vol.lookup(lease_id1)
        lease3 = vol.lookup(lease_id3)
        resource1 = fake_sanlock.resources[(lease1.path, lease1.offset)]

        # Prepare temporary buffer with corrupted record.
        buf = mmap.mmap(-1, 4096)
        tmp_vol.backend.pread(tmp_vol.alignment, buf)
        buf.seek(xlease.METADATA_SIZE + xlease.RECORD_SIZE * 2)
        buf.write(b"\0" * xlease.RECORD_SIZE)

        # Write corrupt data to storage.
        tmp_vol.backend.pwrite(tmp_vol.alignment, buf)

        # Verify lease is gone if we load new volume from storage.
        vol = xlease.LeasesVolume(
            tmp_vol.backend,
            alignment=tmp_vol.alignment,
            block_size=tmp_vol.block_size)
        with pytest.raises(se.NoSuchLease):
            vol.lookup(lease_id3)

        # Simulate leftover resource in sanlock - same id, different offset.
        fake_sanlock.resources[(lease3.path, lease3.offset)] = resource1

        # Rebuild index - clear corrupted record.
        xlease.rebuild_index(
            vol.lockspace,
            tmp_vol.backend,
            alignment=tmp_vol.alignment,
            block_size=tmp_vol.block_size)

        vol = xlease.LeasesVolume(
            tmp_vol.backend,
            alignment=tmp_vol.alignment,
            block_size=tmp_vol.block_size)

        # Check that lease was not incorrectly restored from sanlock.
        with pytest.raises(se.NoSuchLease):
            vol.lookup(lease_id3)

        # Check that resource of removed lease was cleared at it's offset.
        assert fake_sanlock.resources[(
            lease3.path, lease3.offset)]['resource'] == b''

        # Check lease was not changed by rebuild.
        assert vol.lookup(lease_id1).resource == \
            resource1['resource'].decode("utf-8")

    def test_rebuild_corrupted_record_repair_from_resource(
            self, tmp_vol, fake_sanlock):
        lease_id = make_uuid()
        vol = xlease.LeasesVolume(
            tmp_vol.backend,
            alignment=tmp_vol.alignment,
            block_size=tmp_vol.block_size)
        vol.add(lease_id)

        # Prepare zeroed temporary buffer for record corruption.
        buf = mmap.mmap(-1, tmp_vol.alignment)
        tmp_vol.backend.pread(tmp_vol.alignment, buf)
        buf.seek(xlease.METADATA_SIZE)
        buf.write(b'\0' * xlease.RECORD_SIZE)

        # Write corrupt data to storage.
        tmp_vol.backend.pwrite(tmp_vol.alignment, buf)

        # Verify lease is gone if we load new volume from storage.
        vol = xlease.LeasesVolume(
            tmp_vol.backend,
            alignment=tmp_vol.alignment,
            block_size=tmp_vol.block_size)
        with pytest.raises(se.NoSuchLease):
            vol.lookup(lease_id)

        # Rebuild index.
        xlease.rebuild_index(
            vol.lockspace,
            tmp_vol.backend,
            alignment=tmp_vol.alignment,
            block_size=tmp_vol.block_size)

        # Check rebuild fixed the index.
        vol = xlease.LeasesVolume(
            tmp_vol.backend,
            alignment=tmp_vol.alignment,
            block_size=tmp_vol.block_size)
        assert vol.lookup(lease_id).resource == lease_id

    def test_rebuild_corrupted_record_fails(self, tmp_vol, fake_sanlock):
        lease_id = make_uuid()
        vol = xlease.LeasesVolume(
            tmp_vol.backend,
            alignment=tmp_vol.alignment,
            block_size=tmp_vol.block_size)
        vol.add(lease_id)
        first_offset = vol.lookup(lease_id).offset

        # Prepare zeroed temporary buffer for record corruption.
        buf = mmap.mmap(-1, tmp_vol.alignment)
        tmp_vol.backend.pread(tmp_vol.alignment, buf)
        buf.seek(xlease.METADATA_SIZE)
        buf.write(b'\0' * xlease.RECORD_SIZE)

        # Write corrupt data to storage.
        tmp_vol.backend.pwrite(tmp_vol.alignment, buf)

        # Wipe out sanlock resources.
        fake_sanlock.resources = {}

        # Rebuild index can't repair record without sanlock resource,
        # but it should clear the corrupted record for later use.
        xlease.rebuild_index(
            vol.lockspace,
            tmp_vol.backend,
            alignment=tmp_vol.alignment,
            block_size=tmp_vol.block_size)

        # Check lease can not be found.
        vol = xlease.LeasesVolume(
            tmp_vol.backend,
            alignment=tmp_vol.alignment,
            block_size=tmp_vol.block_size)
        with pytest.raises(se.NoSuchLease):
            vol.lookup(lease_id)

        # Add a lease again.
        vol.add(lease_id)

        # Check the lease ended up on first offset again.
        current_offset = vol.lookup(lease_id).offset
        assert current_offset == first_offset

    def test_rebuild_mismatch_record(self, tmp_vol, fake_sanlock):
        lease_id = make_uuid()
        wrong_id = make_uuid()
        vol = xlease.LeasesVolume(
            tmp_vol.backend,
            alignment=tmp_vol.alignment,
            block_size=tmp_vol.block_size)
        vol.add(lease_id)
        offset = vol.leases()[lease_id]['offset']

        # Mangle sanlock resource id.
        res = fake_sanlock.resources[(tmp_vol.backend.name, offset)]
        res["resource"] = wrong_id.encode("utf-8")

        # Rebuild index.
        xlease.rebuild_index(
            vol.lockspace,
            tmp_vol.backend,
            alignment=tmp_vol.alignment,
            block_size=tmp_vol.block_size)

        repaired_res = fake_sanlock.read_resource(
            tmp_vol.backend.name,
            offset,
            align=tmp_vol.alignment,
            sector=tmp_vol.block_size)

        # Check lease still exists in index.
        resource = vol.lookup(lease_id).resource

        # Check that resource was repaired.
        assert repaired_res['resource'].decode("utf-8") == resource

    def test_rebuild_updating_index(self, tmp_vol, fake_sanlock):
        lease_id = make_uuid()
        vol = xlease.LeasesVolume(
            tmp_vol.backend,
            alignment=tmp_vol.alignment,
            block_size=tmp_vol.block_size)
        vol.add(lease_id)

        # Write index in updating state to storage, simulating failed rebuild.
        md = xlease.IndexMetadata(
            xlease.INDEX_VERSION, "lockspace", updating=True)
        with io.open(tmp_vol.path, "r+b") as f:
            f.seek(tmp_vol.alignment)
            f.write(md.bytes())

        # This fails because index is now updating.
        with pytest.raises(xlease.InvalidIndex):
            vol = xlease.LeasesVolume(
                tmp_vol.backend,
                alignment=tmp_vol.alignment,
                block_size=tmp_vol.block_size)

        # Rebuild index which clears updating flag.
        xlease.rebuild_index(
            vol.lockspace,
            tmp_vol.backend,
            alignment=tmp_vol.alignment,
            block_size=tmp_vol.block_size)

        # This works ok after rebuild.
        vol = xlease.LeasesVolume(
            tmp_vol.backend,
            alignment=tmp_vol.alignment,
            block_size=tmp_vol.block_size)

        lease = vol.lookup(lease_id)
        res = fake_sanlock.resources[(lease.path, lease.offset)]
        assert lease.resource == lease_id
        assert res['resource'].decode("utf-8") == lease_id
        assert res['lockspace'].decode("utf-8") == lease.lockspace
        assert not vol.leases()[lease_id]['updating']

    def test_rebuild_corrupted_metadata(self, tmp_vol, fake_sanlock):
        vol = xlease.LeasesVolume(
            tmp_vol.backend,
            alignment=tmp_vol.alignment,
            block_size=tmp_vol.block_size)

        # Prepare zeroed temporary buffer for metadata corruption.
        buf = mmap.mmap(-1, tmp_vol.alignment)
        tmp_vol.backend.pread(tmp_vol.alignment, buf)
        buf.seek(0)
        buf.write(b'\0' * xlease.METADATA_SIZE)

        # Write corrupt data to storage.
        tmp_vol.backend.pwrite(tmp_vol.alignment, buf)

        # Rebuild index should fail due to invalid metadata.
        with pytest.raises(xlease.InvalidIndex):
            xlease.rebuild_index(
                vol.lockspace,
                tmp_vol.backend,
                alignment=tmp_vol.alignment,
                block_size=tmp_vol.block_size)

    def test_create_read_failure(self, tmp_vol):
        file = FailingReader(tmp_vol.path)
        with utils.closing(file):
            with pytest.raises(ReadError):
                xlease.LeasesVolume(
                    file,
                    alignment=tmp_vol.alignment,
                    block_size=tmp_vol.block_size)

    def test_lookup_missing(self, tmp_vol):
        vol = xlease.LeasesVolume(
            tmp_vol.backend,
            alignment=tmp_vol.alignment,
            block_size=tmp_vol.block_size)
        with utils.closing(vol):
            with pytest.raises(se.NoSuchLease):
                vol.lookup(make_uuid())

    def test_lookup_updating(self, tmp_vol):
        # This simulates updating record which should only happen when lease
        # is partially created with older vdsm versions.
        record = xlease.Record(make_uuid(), 0, updating=True)
        tmp_vol.write_records((42, record))
        vol = xlease.LeasesVolume(
            tmp_vol.backend,
            alignment=tmp_vol.alignment,
            block_size=tmp_vol.block_size)
        with utils.closing(vol):
            leases = vol.leases()
            # Verify record is in updating state.
            assert leases[record.resource]["updating"]
            # Test that lookup raises when updating flag is present.
            with pytest.raises(se.NoSuchLease):
                vol.lookup(record.resource)

    def test_add(self, tmp_vol, fake_sanlock):
        vol = xlease.LeasesVolume(
            tmp_vol.backend,
            alignment=tmp_vol.alignment,
            block_size=tmp_vol.block_size)
        with utils.closing(vol):
            lease_id = make_uuid()
            lease = vol.add(lease_id)
            res = fake_sanlock.read_resource(
                lease.path,
                lease.offset,
                align=tmp_vol.alignment,
                sector=tmp_vol.block_size)
            check_lease(lease, lease_id, res, vol)

    def test_add_write_failure(self, tmp_vol, fake_sanlock):
        backend = FailingWriter(tmp_vol.path)
        with utils.closing(backend):
            vol = xlease.LeasesVolume(
                backend,
                alignment=tmp_vol.alignment,
                block_size=tmp_vol.block_size)
            with utils.closing(vol):
                lease_id = make_uuid()
                with pytest.raises(WriteError):
                    vol.add(lease_id)
                # Must succeed becuase writng to storage failed
                assert lease_id not in vol.leases()

    def test_add_out_of_space(self, monkeypatch, memory_vol):
        sanlock = FakeSanlock(sector_size=memory_vol.block_size)
        monkeypatch.setattr(xlease, "sanlock", sanlock)
        # Create LeasesVolume with reduced size backend to fit one lease only.
        vol = xlease.LeasesVolume(
            memory_vol.backend,
            alignment=memory_vol.alignment,
            block_size=memory_vol.block_size)
        # Fill up the index with records.
        for _ in range(memory_vol.max_records):
            vol.add(make_uuid())
        # Index is now full so next add should raise.
        with pytest.raises(xlease.NoSpace):
            vol.add(make_uuid())

    def test_remove_sanlock_failure(self, tmp_vol, fake_sanlock):
        lease_id = make_uuid()
        vol = xlease.LeasesVolume(
            tmp_vol.backend,
            alignment=tmp_vol.alignment,
            block_size=tmp_vol.block_size)
        # Add a lease as usual.
        vol.add(lease_id)
        with utils.closing(vol):
            # Make sanlock fail to write a resource.
            fake_sanlock.errors["write_resource"] = \
                fake_sanlock.SanlockException
            # Removal must not raise.
            vol.remove(lease_id)
            # Lookup lease id, it should not exist despite sanlock failure.
            with pytest.raises(se.NoSuchLease):
                vol.lookup(lease_id)

    def test_add_sanlock_failure(self, tmp_vol, fake_sanlock):
        lease_id = make_uuid()
        vol = xlease.LeasesVolume(
            tmp_vol.backend,
            alignment=tmp_vol.alignment,
            block_size=tmp_vol.block_size)
        with utils.closing(vol):
            # Make sanlock fail to write a resource.
            fake_sanlock.errors["write_resource"] = \
                fake_sanlock.SanlockException
            with pytest.raises(fake_sanlock.SanlockException):
                vol.add(lease_id)
            # Lookup lease id, it should not exist.
            with pytest.raises(se.NoSuchLease):
                vol.lookup(lease_id)

    def test_add_updating(self, tmp_vol, fake_sanlock):
        lease_id = make_uuid()
        # This simulates updating record which should only happen when lease
        # is partially created with older vdsm versions.
        record = xlease.Record(lease_id, 0, updating=True)
        tmp_vol.write_records((42, record))
        vol = xlease.LeasesVolume(
            tmp_vol.backend,
            alignment=tmp_vol.alignment,
            block_size=tmp_vol.block_size)
        # Adding a lease should pass despite record updating state.
        with utils.closing(vol):
            lease = vol.add(lease_id)
            res = fake_sanlock.read_resource(
                lease.path,
                lease.offset,
                align=tmp_vol.alignment,
                sector=tmp_vol.block_size)
            check_lease(lease, lease_id, res, vol)

    def test_leases(self, tmp_vol, fake_sanlock):
        vol = xlease.LeasesVolume(
            tmp_vol.backend,
            alignment=tmp_vol.alignment,
            block_size=tmp_vol.block_size)
        with utils.closing(vol):
            uuid = make_uuid()
            lease_info = vol.add(uuid)
            leases = vol.leases()
            expected = {
                uuid: {
                    "offset": lease_info.offset,
                    "updating": False,
                }
            }
            assert leases == expected

    def test_leases_bad_record(self, tmp_vol, fake_sanlock):
        vol = xlease.LeasesVolume(
            tmp_vol.backend,
            alignment=tmp_vol.alignment,
            block_size=tmp_vol.block_size)

        # Index three leases.
        with utils.closing(vol):
            vol.add("lease1")
            vol.add("lease2")
            vol.add("lease3")

        # Corrupt index record of second lease on storage.
        with io.open(tmp_vol.path, "r+b") as f:
            index_record_offset = xlease.RECORD_BASE + 1 * xlease.RECORD_SIZE
            f.seek(tmp_vol.alignment + index_record_offset)
            f.write(b"!" * xlease.RECORD_SIZE)

        # Reload the index to memory from storage.
        vol = xlease.LeasesVolume(
            tmp_vol.backend,
            alignment=tmp_vol.alignment,
            block_size=tmp_vol.block_size)

        with utils.closing(vol):
            # Dumping leases from index skips the second bad index record
            # and gets to the last record.
            assert set(vol.leases()) == {"lease1", "lease3"}

    def test_add_exists(self, tmp_vol, fake_sanlock):
        vol = xlease.LeasesVolume(
            tmp_vol.backend,
            alignment=tmp_vol.alignment,
            block_size=tmp_vol.block_size)
        with utils.closing(vol):
            lease_id = make_uuid()
            lease = vol.add(lease_id)
            with pytest.raises(xlease.LeaseExists):
                vol.add(lease_id)
            res = fake_sanlock.read_resource(
                lease.path,
                lease.offset,
                align=tmp_vol.alignment,
                sector=tmp_vol.block_size)
            assert res["lockspace"] == lease.lockspace.encode("utf-8")
            assert res["resource"] == lease.resource.encode("utf-8")

    def test_lookup_exists(self, tmp_vol, fake_sanlock):
        vol = xlease.LeasesVolume(
            tmp_vol.backend,
            alignment=tmp_vol.alignment,
            block_size=tmp_vol.block_size)
        with utils.closing(vol):
            lease_id = make_uuid()
            add_info = vol.add(lease_id)
            lookup_info = vol.lookup(lease_id)
            assert add_info == lookup_info

    def test_remove_exists(self, tmp_vol, fake_sanlock):
        vol = xlease.LeasesVolume(
            tmp_vol.backend,
            alignment=tmp_vol.alignment,
            block_size=tmp_vol.block_size)
        with utils.closing(vol):
            leases = [make_uuid() for i in range(3)]
            for lease in leases:
                vol.add(lease)
            lease = vol.lookup(leases[1])
            vol.remove(lease.resource)
            assert lease.resource not in vol.leases()
            res = fake_sanlock.read_resource(
                lease.path,
                lease.offset,
                align=tmp_vol.alignment,
                sector=tmp_vol.block_size)
            # There is no sanlock api for removing a resource, so we mark a
            # removed resource with empty (invalid) lockspace and lease id.
            assert res["lockspace"] == b""
            assert res["resource"] == b""

    def test_remove_missing(self, tmp_vol):
        vol = xlease.LeasesVolume(
            tmp_vol.backend,
            alignment=tmp_vol.alignment,
            block_size=tmp_vol.block_size)
        with utils.closing(vol):
            lease_id = make_uuid()
            with pytest.raises(se.NoSuchLease):
                vol.remove(lease_id)

    def test_remove_write_failure(self, tmp_vol):
        record = xlease.Record(make_uuid(), 0, updating=True)
        tmp_vol.write_records((42, record))
        backend = FailingWriter(tmp_vol.path)
        with utils.closing(backend):
            vol = xlease.LeasesVolume(
                backend,
                alignment=tmp_vol.alignment,
                block_size=tmp_vol.block_size)
            with utils.closing(vol):
                with pytest.raises(WriteError):
                    vol.remove(record.resource)
                # Must succeed becuase writng to storage failed
                assert record.resource in vol.leases()

    def test_add_first_free_slot(self, tmp_vol, fake_sanlock):
        vol = xlease.LeasesVolume(
            tmp_vol.backend,
            alignment=tmp_vol.alignment,
            block_size=tmp_vol.block_size)
        with utils.closing(vol):
            uuids = [make_uuid() for i in range(4)]
            for uuid in uuids[:3]:
                vol.add(uuid)
            vol.remove(uuids[1])
            vol.add(uuids[3])
            leases = vol.leases()

            # The first lease in the first slot
            offset = xlease.lease_offset(0, tmp_vol.alignment)
            assert leases[uuids[0]]["offset"] == offset

            # The forth lease was added in the second slot after the second
            # lease was removed.
            offset = xlease.lease_offset(1, tmp_vol.alignment)
            assert leases[uuids[3]]["offset"] == offset

            # The third lease in the third slot
            offset = xlease.lease_offset(2, tmp_vol.alignment)
            assert leases[uuids[2]]["offset"] == offset

    @pytest.mark.slow
    def test_time_lookup(self, tmp_vol):
        setup = """
import os
from testlib import make_uuid
from vdsm import utils
from vdsm.storage import exception as se
from vdsm.storage import xlease

path = "%(path)s"
alignment = %(alignment)d
block_size = %(block_size)d
lockspace = os.path.basename(os.path.dirname(path))
lease_id = make_uuid()

def bench():
    file = xlease.DirectFile(path)
    with utils.closing(file):
        vol = xlease.LeasesVolume(
            file,
            alignment=alignment,
            block_size=block_size)
        with utils.closing(vol, log="test"):
            try:
                vol.lookup(lease_id)
            except se.NoSuchLease:
                pass
"""
        setup = setup % {
            "path": tmp_vol.path,
            "alignment": tmp_vol.alignment,
            "block_size": tmp_vol.block_size,
        }
        count = 100
        elapsed = timeit.timeit("bench()", setup=setup, number=count)
        print("%d lookups in %.6f seconds (%.6f seconds per lookup)"
              % (count, elapsed, elapsed / count))

    @pytest.mark.slow
    def test_time_add(self, tmp_vol, fake_sanlock):
        setup = """
import os
from testlib import make_uuid
from vdsm import utils
from vdsm.storage import xlease

path = "%(path)s"
alignment = %(alignment)d
block_size = %(block_size)d
lockspace = os.path.basename(os.path.dirname(path))

def bench():
    lease_id = make_uuid()
    file = xlease.DirectFile(path)
    with utils.closing(file):
        vol = xlease.LeasesVolume(
            file,
            alignment=alignment,
            block_size=block_size)
        with utils.closing(vol, log="test"):
            vol.add(lease_id)
"""
        setup = setup % {
            "path": tmp_vol.path,
            "alignment": tmp_vol.alignment,
            "block_size": tmp_vol.block_size,
        }
        count = 100
        elapsed = timeit.timeit("bench()", setup=setup, number=count)
        # Note: this does not include the time to create the real sanlock
        # resource.
        print("%d adds in %.6f seconds (%.6f seconds per add)"
              % (count, elapsed, elapsed / count))


@pytest.fixture(params=[
    xlease.DirectFile,
    xlease.InterruptibleDirectFile,
])
def direct_file(request):
    """
    Returns a direct file factory function accpting a path. Test for
    xlease.*DirectFile can use this fixture for testing both implemntations.
    """
    if request.param == xlease.InterruptibleDirectFile:
        try:
            test_oop = oop.getProcessPool("test")
            yield functools.partial(request.param, oop=test_oop)
        finally:
            oop.stop()
    else:
        yield request.param


class TestDirectFile:

    def test_name(self, user_storage, direct_file):
        file = direct_file(user_storage.path)
        with utils.closing(file):
            assert file.name == user_storage.path

    def test_size(self, user_storage, direct_file):
        with io.open(user_storage.path, "wb") as f:
            f.truncate(GiB)
        file = direct_file(user_storage.path)
        with utils.closing(file):
            assert file.size() == GiB

    @pytest.mark.parametrize("offset,size", [
        (0, 1024),      # some content
        (0, 2048),      # all content
        (512, 1024),    # offset, some content
        (1024, 1024),   # offset, all content
    ])
    def test_pread(self, tmpdir, direct_file, offset, size):
        data = b"a" * 512 + b"b" * 512 + b"c" * 512 + b"d" * 512
        path = tmpdir.join("file")
        path.write(data)
        file = direct_file(str(path))
        with utils.closing(file):
            buf = mmap.mmap(-1, size)
            with utils.closing(buf):
                n = file.pread(offset, buf)
                assert n == size
                assert buf[:] == data[offset:offset + size]

    def test_pread_short(self, tmpdir, direct_file):
        data = b"a" * 1024
        path = tmpdir.join("file")
        path.write(data)
        file = direct_file(str(path))
        with utils.closing(file):
            buf = mmap.mmap(-1, 1024)
            with utils.closing(buf):
                n = file.pread(512, buf)
                assert n == 512
                assert buf[:n] == data[512:]

    @pytest.mark.parametrize("offset,size", [
        (0, 1024),      # some content
        (0, 2048),      # all content
        (512, 1024),    # offset, some content
        (1024, 1024),   # offset, all content
    ])
    def test_pwrite(self, tmpdir, direct_file, offset, size):
        # Create a file full of "a"s
        path = tmpdir.join("file")
        path.write(b"a" * 2048)
        buf = mmap.mmap(-1, size)
        with utils.closing(buf):
            # Write "b"s
            buf.write(b"b" * size)
            file = direct_file(str(path))
            with utils.closing(file):
                file.pwrite(offset, buf)
        data = path.read()
        expected = ("a" * offset +
                    "b" * size +
                    "a" * (2048 - offset - size))
        assert data == expected
