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

from testValidation import slowtest
from testValidation import brokentest
from testlib import VdsmTestCase
from testlib import make_uuid
from testlib import namedTemporaryDir

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

    def test_format(self):
        with make_index() as index:
            self.assertEqual(index.leases(), {})

    def test_create_read_failure(self):
        with make_leases() as path:
            file = FailingReader(path)
            with utils.closing(file):
                with self.assertRaises(ReadError):
                    xlease.Index("lockspace", file)

    def test_lookup_missing(self):
        with make_index() as index:
            with self.assertRaises(xlease.NoSuchLease):
                index.lookup(make_uuid())

    def test_lookup_stale(self):
        record = xlease.Record(make_uuid(), xlease.RECORD_STALE)
        with make_index((42, record)) as index:
            leases = index.leases()
            self.assertEqual(leases[record.resource]["state"], "STALE")
            with self.assertRaises(xlease.StaleLease):
                index.lookup(record.resource)

    def test_add(self):
        with make_index() as index:
            lease_id = make_uuid()
            start_time = int(time.time())
            lease_info = index.add(lease_id)
        self.assertEqual(lease_info.lockspace, index.lockspace)
        self.assertEqual(lease_info.resource, lease_id)
        self.assertEqual(lease_info.path, index.path)
        self.assertTrue(start_time <= lease_info.modified <= start_time + 1)

    @brokentest("not implemented yet")
    def test_add_write_failure(self):
        with make_index() as base:
            file = FailingWriter(base.path)
            with utils.closing(file):
                index = xlease.Index(base.lockspace, file)
                with utils.closing(index):
                    lease_id = make_uuid()
                    with self.assertRaises(WriteError):
                        index.add(lease_id)
                    # Must succeed becuase writng to storage failed
                    self.assertNotIn(lease_id, index.leases())

    def test_leases(self):
        with make_index() as index:
            uuid = make_uuid()
            lease_info = index.add(uuid)
            leases = index.leases()
            self.assertEqual(len(leases), 1)
            self.assertEqual(leases[uuid]["offset"], xlease.LEASE_BASE)
            self.assertEqual(leases[uuid]["state"], "USED")
            self.assertEqual(leases[uuid]["modified"], lease_info.modified)

    def test_add_exists(self):
        with make_index() as index:
            lease_id = make_uuid()
            index.add(lease_id)
            with self.assertRaises(xlease.LeaseExists):
                index.add(lease_id)

    def test_lookup_exists(self):
        with make_index() as index:
            lease_id = make_uuid()
            add_info = index.add(lease_id)
            lookup_info = index.lookup(lease_id)
            self.assertEqual(add_info, lookup_info)

    def test_remove_exists(self):
        with make_index() as index:
            leases = [make_uuid() for i in range(3)]
            for lease in leases:
                index.add(lease)
            index.remove(leases[1])
            self.assertNotIn(leases[1], index.leases())

    def test_remove_missing(self):
        with make_index() as index:
            lease_id = make_uuid()
            with self.assertRaises(xlease.NoSuchLease):
                index.remove(lease_id)

    @brokentest("not implemented yet")
    def test_remove_write_failure(self):
        record = xlease.Record(make_uuid(), xlease.RECORD_STALE)
        with make_index((42, record)) as base:
            file = FailingWriter(base.path)
            with utils.closing(file):
                index = xlease.Index(base.lockspace, file)
                with utils.closing(index):
                    with self.assertRaises(WriteError):
                        index.remove(record.resource)
                    # Must succeed becuase writng to storage failed
                    self.assertIn(record.resource, index.leases())

    def test_add_first_free_slot(self):
        with make_index() as index:
            uuids = [make_uuid() for i in range(4)]
            for uuid in uuids[:3]:
                index.add(uuid)
            index.remove(uuids[1])
            index.add(uuids[3])
            leases = index.leases()
            # The first lease in the first slot
            self.assertEqual(leases[uuids[0]]["offset"],
                             xlease.LEASE_BASE)
            # The forth lease was added in the second slot after the second
            # lease was removed.
            self.assertEqual(leases[uuids[3]]["offset"],
                             xlease.LEASE_BASE + xlease.LEASE_SIZE)
            # The third lease in the third slot
            self.assertEqual(leases[uuids[2]]["offset"],
                             xlease.LEASE_BASE + xlease.LEASE_SIZE * 2)

    @slowtest
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
        index = xlease.Index(lockspace, file)
        with utils.closing(index, log="test"):
            try:
                index.lookup(lease_id)
            except xlease.NoSuchLease:
                pass
"""
        with make_index() as index:
            count = 1000
            elapsed = timeit.timeit("bench()", setup=setup % index.path,
                                    number=count)
            print("%d lookups in %.6f seconds (%.6f seconds per lookup)"
                  % (count, elapsed, elapsed / count))

    @slowtest
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
        index = xlease.Index(lockspace, file)
        with utils.closing(index, log="test"):
            index.add(lease_id)
"""
        with make_index() as index:
            count = 100
            elapsed = timeit.timeit("bench()", setup=setup % index.path,
                                    number=count)
            print("%d adds in %.6f seconds (%.6f seconds per add)"
                  % (count, elapsed, elapsed / count))


@contextmanager
def make_index(*records):
    with make_leases() as path:
        lockspace = os.path.basename(os.path.dirname(path))
        format_index(lockspace, path)
        if records:
            write_records(records, lockspace, path)
        file = xlease.DirectFile(path)
        with utils.closing(file):
            index = xlease.Index(lockspace, file)
            with utils.closing(index):
                yield index


@contextmanager
def make_leases():
    with namedTemporaryDir() as tmpdir:
        path = os.path.join(tmpdir, "xleases")
        with io.open(path, "wb") as f:
            f.truncate(xlease.INDEX_SIZE)
        yield path


def format_index(lockspace, path):
    file = xlease.DirectFile(path)
    with utils.closing(file):
        index = xlease.Index(lockspace, file)
        with utils.closing(index):
            index.format()


def write_records(records, lockspace, path):
    file = xlease.DirectFile(path)
    with utils.closing(file):
        buf = xlease.IndexBuffer(file)
        with utils.closing(buf):
            for recnum, record in records:
                buf.write_record(recnum, record)
                buf.dump_record(recnum, file)
