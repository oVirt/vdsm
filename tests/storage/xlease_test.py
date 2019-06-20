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

import six
import pytest

from vdsm import constants
from vdsm import utils
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


@pytest.fixture(scope="module", params=userstorage.PATHS, ids=str)
def user_storage(request):
    storage = request.param
    if storage.sector_size == 4096:
        pytest.xfail("Sector size {} not supported yet"
                     .format(storage.sector_size))
    if not os.path.exists(storage.path):
        pytest.xfail("{} storage not available".format(storage.name))
    return storage


class TemporaryVolume(object):
    """
    Temporary xleases volume.
    """

    def __init__(self, path):
        """
        Zero storage and format index area.
        """
        self.backend = xlease.DirectFile(path)
        self.zero_storage()
        self.format_index()

    def write_records(self, *records):
        """
        Write records to volume index area.
        """
        index = xlease.VolumeIndex()
        with utils.closing(index):
            index.load(self.backend)
            for recnum, record in records:
                block = index.copy_record_block(recnum)
                with utils.closing(block):
                    block.write_record(recnum, record)
                    block.dump(self.backend)

    def zero_storage(self):
        # TODO: suport block storage.
        with io.open(self.backend.name, "wb") as f:
            f.truncate(constants.GIB)

    def format_index(self):
        lockspace = os.path.basename(os.path.dirname(self.backend.name))
        xlease.format_index(lockspace, self.backend)

    def close(self):
        self.backend.close()


@pytest.fixture
def tmp_vol(user_storage):
    tv = TemporaryVolume(user_storage.path)
    yield tv
    tv.close()


@pytest.fixture
def fake_sanlock(monkeypatch):
    sanlock = FakeSanlock()
    monkeypatch.setattr(xlease, "sanlock", sanlock)
    yield sanlock


class TestIndex:

    def test_metadata(self, tmp_vol, monkeypatch):
        monkeypatch.setattr("time.time", lambda: 123456789)
        tmp_vol.format_index()
        vol = xlease.LeasesVolume(tmp_vol.backend)
        with utils.closing(vol):
            lockspace = os.path.basename(os.path.dirname(vol.path))
            assert vol.version == 1
            assert vol.lockspace == lockspace
            assert vol.mtime == 123456789

    def test_magic_big_endian(self, tmp_vol):
        vol = xlease.LeasesVolume(tmp_vol.backend)
        with utils.closing(vol):
            with io.open(vol.path, "rb") as f:
                f.seek(xlease.INDEX_BASE)
                assert f.read(4) == b"\x12\x15\x20\x16"

    def test_bad_magic(self, tmp_vol):
        tmp_vol.zero_storage()
        self.check_invalid_index(tmp_vol.backend.name)

    def test_bad_version(self, tmp_vol):
        vol = xlease.LeasesVolume(tmp_vol.backend)
        with utils.closing(vol):
            with io.open(vol.path, "r+b") as f:
                f.seek(xlease.INDEX_BASE + 5)
                f.write(b"blah")
            self.check_invalid_index(vol.path)

    def test_unsupported_version(self, tmp_vol):
        vol = xlease.LeasesVolume(tmp_vol.backend)
        with utils.closing(vol):
            md = xlease.IndexMetadata(2, "lockspace")
            with io.open(vol.path, "r+b") as f:
                f.seek(xlease.INDEX_BASE)
                f.write(md.bytes())
            self.check_invalid_index(vol.path)

    def test_bad_lockspace(self, tmp_vol):
        vol = xlease.LeasesVolume(tmp_vol.backend)
        with utils.closing(vol):
            with io.open(vol.path, "r+b") as f:
                f.seek(xlease.INDEX_BASE + 10)
                f.write(b"\xf0")
            self.check_invalid_index(vol.path)

    def test_bad_mtime(self, tmp_vol):
        vol = xlease.LeasesVolume(tmp_vol.backend)
        with utils.closing(vol):
            with io.open(vol.path, "r+b") as f:
                f.seek(xlease.INDEX_BASE + 59)
                f.write(b"not a number")
            self.check_invalid_index(vol.path)

    def test_updating(self, tmp_vol):
        vol = xlease.LeasesVolume(tmp_vol.backend)
        with utils.closing(vol):
            md = xlease.IndexMetadata(xlease.INDEX_VERSION, "lockspace",
                                      updating=True)
            with io.open(vol.path, "r+b") as f:
                f.seek(xlease.INDEX_BASE)
                f.write(md.bytes())
            self.check_invalid_index(vol.path)

    def test_truncated_index(self, tmp_vol):
        vol = xlease.LeasesVolume(tmp_vol.backend)
        with utils.closing(vol):
            # Truncate index, reading it should fail.
            with io.open(vol.path, "r+b") as f:
                f.truncate(
                    xlease.INDEX_BASE + xlease.INDEX_SIZE - xlease.BLOCK_SIZE)
            self.check_invalid_index(vol.path)

    def check_invalid_index(self, path):
        file = xlease.DirectFile(path)
        with utils.closing(file):
            with pytest.raises(xlease.InvalidIndex):
                vol = xlease.LeasesVolume(file)
                vol.close()

    def test_format(self, tmp_vol):
        vol = xlease.LeasesVolume(tmp_vol.backend)
        with utils.closing(vol):
            assert vol.leases() == {}

    def test_rebuild_empty(self, tmp_vol, fake_sanlock):
        vol = xlease.LeasesVolume(tmp_vol.backend)
        with utils.closing(vol):
            # Add underlying sanlock resources
            for i in [3, 4, 6]:
                resource = "%04d" % i
                offset = xlease.USER_RESOURCE_BASE + xlease.SLOT_SIZE * i
                fake_sanlock.write_resource(
                    vol.lockspace, resource, [(vol.path, offset)])
            # The index is empty
            assert vol.leases() == {}

            # After rebuilding the index it should contain all the underlying
            # resources.
            file = xlease.DirectFile(vol.path)
            with utils.closing(file):
                xlease.rebuild_index(vol.lockspace, file)
            expected = {
                "0003": {
                    "offset": xlease.USER_RESOURCE_BASE + xlease.SLOT_SIZE * 3,
                    "updating": False,
                },
                "0004": {
                    "offset": xlease.USER_RESOURCE_BASE + xlease.SLOT_SIZE * 4,
                    "updating": False,
                },
                "0006": {
                    "offset": xlease.USER_RESOURCE_BASE + xlease.SLOT_SIZE * 6,
                    "updating": False,
                },
            }
            file = xlease.DirectFile(vol.path)
            with utils.closing(file):
                vol = xlease.LeasesVolume(file)
                with utils.closing(vol):
                    assert vol.leases() == expected

    def test_create_read_failure(self, tmp_vol):
        file = FailingReader(tmp_vol.backend.name)
        with utils.closing(file):
            with pytest.raises(ReadError):
                xlease.LeasesVolume(file)

    def test_lookup_missing(self, tmp_vol):
        vol = xlease.LeasesVolume(tmp_vol.backend)
        with utils.closing(vol):
            with pytest.raises(se.NoSuchLease):
                vol.lookup(make_uuid())

    def test_lookup_updating(self, tmp_vol):
        record = xlease.Record(make_uuid(), 0, updating=True)
        tmp_vol.write_records((42, record))
        vol = xlease.LeasesVolume(tmp_vol.backend)
        with utils.closing(vol):
            leases = vol.leases()
            assert leases[record.resource]["updating"]
            with pytest.raises(xlease.LeaseUpdating):
                vol.lookup(record.resource)

    def test_add(self, tmp_vol, fake_sanlock):
        vol = xlease.LeasesVolume(tmp_vol.backend)
        with utils.closing(vol):
            lease_id = make_uuid()
            lease = vol.add(lease_id)
            assert lease.lockspace == vol.lockspace
            assert lease.resource == lease_id
            assert lease.path == vol.path
            res = fake_sanlock.read_resource(lease.path, lease.offset)
            assert res["lockspace"] == lease.lockspace
            assert res["resource"] == lease.resource

    def test_add_write_failure(self, tmp_vol):
        base = xlease.LeasesVolume(tmp_vol.backend)
        with utils.closing(base):
            file = FailingWriter(base.path)
            with utils.closing(file):
                vol = xlease.LeasesVolume(file)
                with utils.closing(vol):
                    lease_id = make_uuid()
                    with pytest.raises(WriteError):
                        vol.add(lease_id)
                    # Must succeed becuase writng to storage failed
                    assert lease_id not in vol.leases()

    def test_add_sanlock_failure(self, tmp_vol, fake_sanlock):
        vol = xlease.LeasesVolume(tmp_vol.backend)
        with utils.closing(vol):
            lease_id = make_uuid()
            # Make sanlock fail to write a resource
            fake_sanlock.errors["write_resource"] = \
                fake_sanlock.SanlockException
            with pytest.raises(fake_sanlock.SanlockException):
                vol.add(lease_id)
            # We should have an updating lease record
            lease = vol.leases()[lease_id]
            assert lease["updating"]
            # There should be no lease on storage
            with pytest.raises(fake_sanlock.SanlockException) as e:
                fake_sanlock.read_resource(vol.path, lease["offset"])
                assert e.exception.errno == fake_sanlock.SANLK_LEADER_MAGIC

    def test_leases(self, tmp_vol, fake_sanlock):
        vol = xlease.LeasesVolume(tmp_vol.backend)
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

    def test_add_exists(self, tmp_vol, fake_sanlock):
        vol = xlease.LeasesVolume(tmp_vol.backend)
        with utils.closing(vol):
            lease_id = make_uuid()
            lease = vol.add(lease_id)
            with pytest.raises(xlease.LeaseExists):
                vol.add(lease_id)
            res = fake_sanlock.read_resource(lease.path, lease.offset)
            assert res["lockspace"] == lease.lockspace
            assert res["resource"] == lease.resource

    def test_lookup_exists(self, tmp_vol, fake_sanlock):
        vol = xlease.LeasesVolume(tmp_vol.backend)
        with utils.closing(vol):
            lease_id = make_uuid()
            add_info = vol.add(lease_id)
            lookup_info = vol.lookup(lease_id)
            assert add_info == lookup_info

    def test_remove_exists(self, tmp_vol, fake_sanlock):
        vol = xlease.LeasesVolume(tmp_vol.backend)
        with utils.closing(vol):
            leases = [make_uuid() for i in range(3)]
            for lease in leases:
                vol.add(lease)
            lease = vol.lookup(leases[1])
            vol.remove(lease.resource)
            assert lease.resource not in vol.leases()
            res = fake_sanlock.read_resource(lease.path, lease.offset)
            # There is no sanlock api for removing a resource, so we mark a
            # removed resource with empty (invalid) lockspace and lease id.
            assert res["lockspace"] == ""
            assert res["resource"] == ""

    def test_remove_missing(self, tmp_vol):
        vol = xlease.LeasesVolume(tmp_vol.backend)
        with utils.closing(vol):
            lease_id = make_uuid()
            with pytest.raises(se.NoSuchLease):
                vol.remove(lease_id)

    def test_remove_write_failure(self, tmp_vol):
        record = xlease.Record(make_uuid(), 0, updating=True)
        tmp_vol.write_records((42, record))
        base = xlease.LeasesVolume(tmp_vol.backend)
        with utils.closing(base):
            file = FailingWriter(base.path)
            with utils.closing(file):
                vol = xlease.LeasesVolume(file)
                with utils.closing(vol):
                    with pytest.raises(WriteError):
                        vol.remove(record.resource)
                    # Must succeed becuase writng to storage failed
                    assert record.resource in vol.leases()

    def test_remove_sanlock_failure(self, tmp_vol, fake_sanlock):
        vol = xlease.LeasesVolume(tmp_vol.backend)
        with utils.closing(vol):
            lease_id = make_uuid()
            vol.add(lease_id)
            # Make sanlock fail to remove a resource (currnently removing a
            # resouce by writing invalid lockspace and resoruce name).
            fake_sanlock.errors["write_resource"] = \
                fake_sanlock.SanlockException
            with pytest.raises(fake_sanlock.SanlockException):
                vol.remove(lease_id)
            # We should have an updating lease record
            lease = vol.leases()[lease_id]
            assert lease["updating"]
            # There lease should still be on storage
            res = fake_sanlock.read_resource(vol.path, lease["offset"])
            assert res["lockspace"] == vol.lockspace
            assert res["resource"] == lease_id

    def test_add_first_free_slot(self, tmp_vol, fake_sanlock):
        vol = xlease.LeasesVolume(tmp_vol.backend)
        with utils.closing(vol):
            uuids = [make_uuid() for i in range(4)]
            for uuid in uuids[:3]:
                vol.add(uuid)
            vol.remove(uuids[1])
            vol.add(uuids[3])
            leases = vol.leases()
            # The first lease in the first slot
            assert leases[uuids[0]]["offset"] == xlease.USER_RESOURCE_BASE
            # The forth lease was added in the second slot after the second
            # lease was removed.
            assert (leases[uuids[3]]["offset"] ==
                    xlease.USER_RESOURCE_BASE + xlease.SLOT_SIZE)
            # The third lease in the third slot
            assert (leases[uuids[2]]["offset"] ==
                    xlease.USER_RESOURCE_BASE + xlease.SLOT_SIZE * 2)

    @pytest.mark.slow
    def test_time_lookup(self, tmp_vol):
        setup = """
import os
from testlib import make_uuid
from vdsm import utils
from vdsm.storage import exception as se
from vdsm.storage import xlease

path = "%s"
lockspace = os.path.basename(os.path.dirname(path))
lease_id = make_uuid()

def bench():
    file = xlease.DirectFile(path)
    with utils.closing(file):
        vol = xlease.LeasesVolume(file)
        with utils.closing(vol, log="test"):
            try:
                vol.lookup(lease_id)
            except se.NoSuchLease:
                pass
"""
        vol = xlease.LeasesVolume(tmp_vol.backend)
        with utils.closing(vol):
            count = 100
            elapsed = timeit.timeit("bench()", setup=setup % vol.path,
                                    number=count)
            print("%d lookups in %.6f seconds (%.6f seconds per lookup)"
                  % (count, elapsed, elapsed / count))

    @pytest.mark.slow
    def test_time_add(self, tmp_vol, fake_sanlock):
        setup = """
import os
from testlib import make_uuid
from vdsm import utils
from vdsm.storage import xlease

path = "%s"
lockspace = os.path.basename(os.path.dirname(path))

def bench():
    lease_id = make_uuid()
    file = xlease.DirectFile(path)
    with utils.closing(file):
        vol = xlease.LeasesVolume(file)
        with utils.closing(vol, log="test"):
            vol.add(lease_id)
"""
        vol = xlease.LeasesVolume(tmp_vol.backend)
        with utils.closing(vol):
            count = 100
            elapsed = timeit.timeit("bench()", setup=setup % vol.path,
                                    number=count)
            # Note: this does not include the time to create the real sanlock
            # resource.
            print("%d adds in %.6f seconds (%.6f seconds per add)"
                  % (count, elapsed, elapsed / count))


@pytest.fixture(params=[
    xlease.DirectFile,
    pytest.param(
        xlease.InterruptibleDirectFile,
        marks=pytest.mark.skipif(
            six.PY3,
            reason="ioprocess is not availale on python 3"))
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
            f.truncate(constants.GIB)
        file = direct_file(user_storage.path)
        with utils.closing(file):
            assert file.size() == constants.GIB

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
