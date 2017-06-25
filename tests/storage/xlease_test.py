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
from __future__ import print_function

import io
import os
import time
import timeit

from contextlib import contextmanager

import pytest

from fakesanlock import FakeSanlock
from monkeypatch import MonkeyPatch
from testlib import VdsmTestCase
from testlib import make_uuid
from testlib import namedTemporaryDir

from vdsm import constants
from vdsm import utils
from vdsm.storage import xlease


class ReadError(Exception):
    """ Raised to simulate read errors """


class WriteError(Exception):
    """ Raised to simulate read errors """


class FailingReader(xlease.DirectFile):
    def readinto(self, buf):
        raise ReadError


class FailingWriter(xlease.DirectFile):
    def write(self, buf):
        raise WriteError


class TestIndex(VdsmTestCase):

    @MonkeyPatch(time, 'time', lambda: 123456789)
    def test_metadata(self):
        with make_volume() as vol:
            lockspace = os.path.basename(os.path.dirname(vol.path))
            self.assertEqual(vol.version, 1)
            self.assertEqual(vol.lockspace, lockspace)
            self.assertEqual(vol.mtime, 123456789)

    def test_magic_big_endian(self):
        with make_volume() as vol:
            with io.open(vol.path, "rb") as f:
                f.seek(xlease.INDEX_BASE)
                self.assertEqual(f.read(4), b"\x12\x15\x20\x16")

    def test_bad_magic(self):
        with make_leases() as path:
            self.check_invalid_index(path)

    def test_bad_version(self):
        with make_volume() as vol:
            with io.open(vol.path, "r+b") as f:
                f.seek(xlease.INDEX_BASE + 5)
                f.write(b"blah")
            self.check_invalid_index(vol.path)

    def test_unsupported_version(self):
        with make_volume() as vol:
            md = xlease.IndexMetadata(2, "lockspace")
            with io.open(vol.path, "r+b") as f:
                f.seek(xlease.INDEX_BASE)
                f.write(md.bytes())
            self.check_invalid_index(vol.path)

    def test_bad_lockspace(self):
        with make_volume() as vol:
            with io.open(vol.path, "r+b") as f:
                f.seek(xlease.INDEX_BASE + 10)
                f.write(b"\xf0")
            self.check_invalid_index(vol.path)

    def test_bad_mtime(self):
        with make_volume() as vol:
            with io.open(vol.path, "r+b") as f:
                f.seek(xlease.INDEX_BASE + 59)
                f.write(b"not a number")
            self.check_invalid_index(vol.path)

    def test_updating(self):
        with make_volume() as vol:
            md = xlease.IndexMetadata(xlease.INDEX_VERSION, "lockspace",
                                      updating=True)
            with io.open(vol.path, "r+b") as f:
                f.seek(xlease.INDEX_BASE)
                f.write(md.bytes())
            self.check_invalid_index(vol.path)

    def check_invalid_index(self, path):
        file = xlease.DirectFile(path)
        with utils.closing(file):
            with self.assertRaises(xlease.InvalidIndex):
                vol = xlease.LeasesVolume(file)
                vol.close()

    def test_format(self):
        with make_volume() as vol:
            self.assertEqual(vol.leases(), {})

    def test_create_read_failure(self):
        with make_leases() as path:
            file = FailingReader(path)
            with utils.closing(file):
                with self.assertRaises(ReadError):
                    xlease.LeasesVolume(file)

    def test_lookup_missing(self):
        with make_volume() as vol:
            with self.assertRaises(xlease.NoSuchLease):
                vol.lookup(make_uuid())

    def test_lookup_updating(self):
        record = xlease.Record(make_uuid(), 0, updating=True)
        with make_volume((42, record)) as vol:
            leases = vol.leases()
            self.assertTrue(leases[record.resource]["updating"])
            with self.assertRaises(xlease.LeaseUpdating):
                vol.lookup(record.resource)

    @MonkeyPatch(xlease, "sanlock", FakeSanlock())
    def test_add(self):
        with make_volume() as vol:
            lease_id = make_uuid()
            lease = vol.add(lease_id)
            self.assertEqual(lease.lockspace, vol.lockspace)
            self.assertEqual(lease.resource, lease_id)
            self.assertEqual(lease.path, vol.path)
            sanlock = xlease.sanlock
            res = sanlock.read_resource(lease.path, lease.offset)
            self.assertEqual(res["lockspace"], lease.lockspace)
            self.assertEqual(res["resource"], lease.resource)

    def test_add_write_failure(self):
        with make_volume() as base:
            file = FailingWriter(base.path)
            with utils.closing(file):
                vol = xlease.LeasesVolume(file)
                with utils.closing(vol):
                    lease_id = make_uuid()
                    with self.assertRaises(WriteError):
                        vol.add(lease_id)
                    # Must succeed becuase writng to storage failed
                    self.assertNotIn(lease_id, vol.leases())

    @MonkeyPatch(xlease, "sanlock", FakeSanlock())
    def test_add_sanlock_failure(self):
        with make_volume() as vol:
            lease_id = make_uuid()
            sanlock = xlease.sanlock
            # Make sanlock fail to write a resource
            sanlock.errors["write_resource"] = sanlock.SanlockException
            with self.assertRaises(sanlock.SanlockException):
                vol.add(lease_id)
            # We should have an updating lease record
            lease = vol.leases()[lease_id]
            self.assertTrue(lease["updating"])
            # There should be no lease on storage
            with self.assertRaises(sanlock.SanlockException) as e:
                sanlock.read_resource(vol.path, lease["offset"])
            self.assertEqual(e.exception.errno, sanlock.SANLK_LEADER_MAGIC)

    @MonkeyPatch(xlease, "sanlock", FakeSanlock())
    def test_leases(self):
        with make_volume() as vol:
            uuid = make_uuid()
            lease_info = vol.add(uuid)
            leases = vol.leases()
            expected = {
                uuid: {
                    "offset": lease_info.offset,
                    "updating": False,
                }
            }
            self.assertEqual(leases, expected)

    @MonkeyPatch(xlease, "sanlock", FakeSanlock())
    def test_add_exists(self):
        with make_volume() as vol:
            lease_id = make_uuid()
            lease = vol.add(lease_id)
            with self.assertRaises(xlease.LeaseExists):
                vol.add(lease_id)
            sanlock = xlease.sanlock
            res = sanlock.read_resource(lease.path, lease.offset)
            self.assertEqual(res["lockspace"], lease.lockspace)
            self.assertEqual(res["resource"], lease.resource)

    @MonkeyPatch(xlease, "sanlock", FakeSanlock())
    def test_lookup_exists(self):
        with make_volume() as vol:
            lease_id = make_uuid()
            add_info = vol.add(lease_id)
            lookup_info = vol.lookup(lease_id)
            self.assertEqual(add_info, lookup_info)

    @MonkeyPatch(xlease, "sanlock", FakeSanlock())
    def test_remove_exists(self):
        with make_volume() as vol:
            leases = [make_uuid() for i in range(3)]
            for lease in leases:
                vol.add(lease)
            lease = vol.lookup(leases[1])
            vol.remove(lease.resource)
            self.assertNotIn(lease.resource, vol.leases())
            sanlock = xlease.sanlock
            res = sanlock.read_resource(lease.path, lease.offset)
            # There is no sanlock api for removing a resource, so we mark a
            # removed resource with empty (invalid) lockspace and lease id.
            self.assertEqual(res["lockspace"], "")
            self.assertEqual(res["resource"], "")

    def test_remove_missing(self):
        with make_volume() as vol:
            lease_id = make_uuid()
            with self.assertRaises(xlease.NoSuchLease):
                vol.remove(lease_id)

    def test_remove_write_failure(self):
        record = xlease.Record(make_uuid(), 0, updating=True)
        with make_volume((42, record)) as base:
            file = FailingWriter(base.path)
            with utils.closing(file):
                vol = xlease.LeasesVolume(file)
                with utils.closing(vol):
                    with self.assertRaises(WriteError):
                        vol.remove(record.resource)
                    # Must succeed becuase writng to storage failed
                    self.assertIn(record.resource, vol.leases())

    @MonkeyPatch(xlease, "sanlock", FakeSanlock())
    def test_remove_sanlock_failure(self):
        with make_volume() as vol:
            lease_id = make_uuid()
            vol.add(lease_id)
            sanlock = xlease.sanlock
            # Make sanlock fail to remove a resource (currnently removing a
            # resouce by writing invalid lockspace and resoruce name).
            sanlock.errors["write_resource"] = sanlock.SanlockException
            with self.assertRaises(sanlock.SanlockException):
                vol.remove(lease_id)
            # We should have an updating lease record
            lease = vol.leases()[lease_id]
            self.assertTrue(lease["updating"])
            # There lease should still be on storage
            res = sanlock.read_resource(vol.path, lease["offset"])
            self.assertEqual(res["lockspace"], vol.lockspace)
            self.assertEqual(res["resource"], lease_id)

    @MonkeyPatch(xlease, "sanlock", FakeSanlock())
    def test_add_first_free_slot(self):
        with make_volume() as vol:
            uuids = [make_uuid() for i in range(4)]
            for uuid in uuids[:3]:
                vol.add(uuid)
            vol.remove(uuids[1])
            vol.add(uuids[3])
            leases = vol.leases()
            # The first lease in the first slot
            self.assertEqual(leases[uuids[0]]["offset"],
                             xlease.USER_RESOURCE_BASE)
            # The forth lease was added in the second slot after the second
            # lease was removed.
            self.assertEqual(leases[uuids[3]]["offset"],
                             xlease.USER_RESOURCE_BASE + xlease.SLOT_SIZE)
            # The third lease in the third slot
            self.assertEqual(leases[uuids[2]]["offset"],
                             xlease.USER_RESOURCE_BASE + xlease.SLOT_SIZE * 2)

    @pytest.mark.slow
    def test_time_lookup(self):
        setup = """
import os
from testlib import make_uuid
from vdsm import utils
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
            except xlease.NoSuchLease:
                pass
"""
        with make_volume() as vol:
            count = 100
            elapsed = timeit.timeit("bench()", setup=setup % vol.path,
                                    number=count)
            print("%d lookups in %.6f seconds (%.6f seconds per lookup)"
                  % (count, elapsed, elapsed / count))

    @pytest.mark.slow
    @MonkeyPatch(xlease, "sanlock", FakeSanlock())
    def test_time_add(self):
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
        with make_volume() as vol:
            count = 100
            elapsed = timeit.timeit("bench()", setup=setup % vol.path,
                                    number=count)
            # Note: this does not include the time to create the real sanlock
            # resource.
            print("%d adds in %.6f seconds (%.6f seconds per add)"
                  % (count, elapsed, elapsed / count))


class TestDirectFile(VdsmTestCase):

    # TODO: test other methods

    def test_size(self):
        with make_leases() as path:
            file = xlease.DirectFile(path)
            with utils.closing(file):
                self.assertEqual(file.size(), constants.GIB)


@contextmanager
def make_volume(*records):
    with make_leases() as path:
        lockspace = os.path.basename(os.path.dirname(path))
        file = xlease.DirectFile(path)
        with utils.closing(file):
            xlease.format_index(lockspace, file)
            if records:
                write_records(records, file)
            vol = xlease.LeasesVolume(file)
            with utils.closing(vol):
                yield vol


@contextmanager
def make_leases():
    with namedTemporaryDir() as tmpdir:
        path = os.path.join(tmpdir, "xleases")
        with io.open(path, "wb") as f:
            f.truncate(constants.GIB)
        yield path


def write_records(records, file):
    index = xlease.VolumeIndex()
    with utils.closing(index):
        index.load(file)
        for recnum, record in records:
            block = index.copy_record_block(recnum)
            with utils.closing(block):
                block.write_record(recnum, record)
                block.dump(file)
