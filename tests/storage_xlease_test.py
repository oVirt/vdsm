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
from testlib import VdsmTestCase
from testlib import make_uuid
from testlib import namedTemporaryDir

from vdsm import utils
from vdsm.storage import xlease


class TestIndex(VdsmTestCase):

    def test_format(self):
        with make_index() as index:
            self.assertEqual(index.leases(), {})

    def test_lookup_missing(self):
        with make_index() as index:
            with self.assertRaises(xlease.NoSuchLease):
                index.lookup(make_uuid())

    def test_lookup_stale(self):
        with make_index() as base:
            record = xlease.Record(make_uuid(), xlease.RECORD_STALE)
            with io.open(base.path, "r+b") as f:
                f.seek(xlease.RECORD_BASE)
                f.write(record.bytes())
            file = xlease.DirectFile(base.path)
            with utils.closing(file):
                index = xlease.Index("lockspace", file)
                with utils.closing(index):
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

    def test_leases(self):
        with make_index() as index:
            leases = [make_uuid() for i in range(3)]
            for lease in leases:
                index.add(lease)
            expected = {
                leases[0]: xlease.LEASE_BASE,
                leases[1]: xlease.LEASE_BASE + xlease.LEASE_SIZE,
                leases[2]: xlease.LEASE_BASE + xlease.LEASE_SIZE * 2,
            }
            self.assertEqual(index.leases(), expected)

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
            expected = {
                leases[0]: xlease.LEASE_BASE,
                leases[2]: xlease.LEASE_BASE + xlease.LEASE_SIZE * 2,
            }
            self.assertEqual(index.leases(), expected)

    def test_remove_missing(self):
        with make_index() as index:
            lease_id = make_uuid()
            with self.assertRaises(xlease.NoSuchLease):
                index.remove(lease_id)

    def test_add_first_free_slot(self):
        with make_index() as index:
            leases = [make_uuid() for i in range(4)]
            for lease in leases[:3]:
                index.add(lease)
            index.remove(leases[1])
            index.add(leases[3])
            expected = {
                leases[0]: xlease.LEASE_BASE,
                leases[3]: xlease.LEASE_BASE + xlease.LEASE_SIZE,
                leases[2]: xlease.LEASE_BASE + xlease.LEASE_SIZE * 2,
            }
            self.assertEqual(index.leases(), expected)

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
def make_index():
    with make_leases() as path:
        lockspace = os.path.basename(os.path.dirname(path))
        file = xlease.DirectFile(path)
        with utils.closing(file):
            index = xlease.Index(lockspace, file)
            with utils.closing(index, log="test"):
                index.format()
                yield index


@contextmanager
def make_leases():
    with namedTemporaryDir() as tmpdir:
        path = os.path.join(tmpdir, "xleases")
        with io.open(path, "wb") as f:
            f.truncate(xlease.INDEX_SIZE)
        yield path
