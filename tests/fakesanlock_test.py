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
from fakesanlock import FakeSanlock
from vdsm.common import concurrent
from testlib import VdsmTestCase


class ExpectedError(Exception):
    pass


class TestFakeSanlock(VdsmTestCase):

    # Managing lockspaces

    def test_add_lockspace_sync(self):
        fs = FakeSanlock()
        fs.add_lockspace("lockspace", 1, "path")
        ls = fs.spaces["lockspace"]
        self.assertEqual(ls["host_id"], 1)
        self.assertEqual(ls["path"], "path")
        self.assertEqual(ls["offset"], 0)
        self.assertEqual(ls["iotimeout"], 0)
        self.assertTrue(ls["ready"].is_set(), "lockspace is not ready")

    def test_add_lockspace_options(self):
        fs = FakeSanlock()
        fs.add_lockspace("lockspace", 1, "path", offset=42, iotimeout=10)
        ls = fs.spaces["lockspace"]
        self.assertEqual(ls["offset"], 42)
        self.assertEqual(ls["iotimeout"], 10)

    def test_add_lockspace_async(self):
        fs = FakeSanlock()
        fs.add_lockspace("lockspace", 1, "path", async=True)
        ls = fs.spaces["lockspace"]
        self.assertEqual(ls["iotimeout"], 0)
        self.assertFalse(ls["ready"].is_set(), "lockspace is ready")

    def test_rem_lockspace_sync(self):
        fs = FakeSanlock()
        fs.add_lockspace("lockspace", 1, "path")
        fs.rem_lockspace("lockspace", 1, "path")
        self.assertNotIn("lockspace", fs.spaces)

    def test_rem_lockspace_async(self):
        fs = FakeSanlock()
        fs.add_lockspace("lockspace", 1, "path")
        fs.rem_lockspace("lockspace", 1, "path", async=True)
        ls = fs.spaces["lockspace"]
        self.assertFalse(ls["ready"].is_set(), "lockspace is ready")

    def test_inq_lockspace_acquired(self):
        fs = FakeSanlock()
        fs.add_lockspace("lockspace", 1, "path")
        acquired = fs.inq_lockspace("lockspace", 1, "path")
        self.assertTrue(acquired, "lockspace not acquired")

    def test_inq_lockspace_acquring_no_wait(self):
        fs = FakeSanlock()
        fs.add_lockspace("lockspace", 1, "path", async=True)
        acquired = fs.inq_lockspace("lockspace", 1, "path")
        self.assertIsNone(acquired, "lockspace is ready")

    def test_inq_lockspace_acquiring_wait(self):
        fs = FakeSanlock()
        fs.add_lockspace("lockspace", 1, "path", async=True)

        t = concurrent.thread(fs.complete_async, args=("lockspace",))
        t.start()
        try:
            acquired = fs.inq_lockspace("lockspace", 1, "path", wait=True)
        finally:
            t.join()
        self.assertTrue(acquired, "lockspace not acquired")

    def test_inq_lockspace_released(self):
        fs = FakeSanlock()
        fs.add_lockspace("lockspace", 1, "path")
        fs.rem_lockspace("lockspace", 1, "path")
        acquired = fs.inq_lockspace("lockspace", 1, "path")
        self.assertFalse(acquired, "lockspace not released")

    def test_inq_lockspace_releasing_no_wait(self):
        fs = FakeSanlock()
        fs.add_lockspace("lockspace", 1, "path")
        fs.rem_lockspace("lockspace", 1, "path", async=True)
        acquired = fs.inq_lockspace("lockspace", 1, "path")
        self.assertFalse(acquired, "lockspace not released")

    def test_inq_lockspace_releasing_wait(self):
        fs = FakeSanlock()
        fs.add_lockspace("lockspace", 1, "path")
        fs.rem_lockspace("lockspace", 1, "path", async=True)

        t = concurrent.thread(fs.complete_async, args=("lockspace",))
        t.start()
        try:
            acquired = fs.inq_lockspace("lockspace", 1, "path", wait=True)
        finally:
            t.join()
        self.assertFalse(acquired, "lockspace not released")

    # Writing and reading resources

    def test_write_read_resource(self):
        fs = FakeSanlock()
        fs.write_resource("lockspace", "resource", [("path", 1048576)])
        info = fs.read_resource("path", 1048576)
        expected = {"resource": "resource",
                    "lockspace": "lockspace",
                    "version": 0,
                    "acquired": False}
        self.assertEqual(info, expected)

    def test_non_existing_resource(self):
        fs = FakeSanlock()
        with self.assertRaises(fs.SanlockException) as e:
            fs.read_resource("path", 1048576)
        self.assertEqual(e.exception.errno, fs.SANLK_LEADER_MAGIC)

    def test_write_resource_failure(self):
        fs = FakeSanlock()
        fs.errors["write_resource"] = ExpectedError
        with self.assertRaises(ExpectedError):
            fs.write_resource("lockspace", "resource", [("path", 1048576)])
        with self.assertRaises(fs.SanlockException) as e:
            fs.read_resource("path", 1048576)
        self.assertEqual(e.exception.errno, fs.SANLK_LEADER_MAGIC)

    def test_read_resource_failure(self):
        fs = FakeSanlock()
        fs.errors["read_resource"] = ExpectedError
        fs.write_resource("lockspace", "resource", [("path", 1048576)])
        with self.assertRaises(ExpectedError):
            fs.read_resource("path", 1048576)

    # Connecting to the sanlock daemon

    def test_register(self):
        fs = FakeSanlock()
        self.assertEqual(fs.register(), 42)

    # Acquiring and releasing resources

    def test_acquire(self):
        fs = FakeSanlock()
        fs.write_resource("lockspace", "resource", [("path", 1048576)])
        fs.add_lockspace("lockspace", 1, "path")
        fd = fs.register()
        fs.acquire("lockspace", "resource", [("path", 1048576)], slkfd=fd)
        res = fs.read_resource("path", 1048576)
        self.assertTrue(res["acquired"], "resource is not acquired")

    def test_acquire_no_lockspace(self):
        fs = FakeSanlock()
        fs.write_resource("lockspace", "resource", [("path", 1048576)])
        fd = fs.register()
        with self.assertRaises(fs.SanlockException) as e:
            fs.acquire("lockspace", "resource", [("path", 1048576)], slkfd=fd)
        self.assertEqual(e.exception.errno, errno.ENOSPC)

    def test_acquire_lockspace_adding(self):
        fs = FakeSanlock()
        fs.write_resource("lockspace", "resource", [("path", 1048576)])
        fs.add_lockspace("lockspace", 1, "path", async=True)
        fd = fs.register()
        with self.assertRaises(fs.SanlockException) as e:
            fs.acquire("lockspace", "resource", [("path", 1048576)], slkfd=fd)
        self.assertEqual(e.exception.errno, errno.ENOSPC)

    def test_acquire_an_acquired_resource(self):
        fs = FakeSanlock()
        fs.write_resource("lockspace", "resource", [("path", 1048576)])
        fs.add_lockspace("lockspace", 1, "path")
        fd = fs.register()
        fs.acquire("lockspace", "resource", [("path", 1048576)], slkfd=fd)
        with self.assertRaises(fs.SanlockException) as e:
            fs.acquire("lockspace", "resource", [("path", 1048576)], slkfd=fd)
        self.assertEqual(e.exception.errno, errno.EEXIST)
        res = fs.read_resource("path", 1048576)
        self.assertTrue(res["acquired"], "resource is not acquired")

    def test_release(self):
        fs = FakeSanlock()
        fs.write_resource("lockspace", "resource", [("path", 1048576)])
        fs.add_lockspace("lockspace", 1, "path")
        fd = fs.register()
        fs.acquire("lockspace", "resource", [("path", 1048576)], slkfd=fd)
        fs.release("lockspace", "resource", [("path", 1048576)], slkfd=fd)
        res = fs.read_resource("path", 1048576)
        self.assertFalse(res["acquired"], "resource is not acquired")

    def test_release_not_acquired(self):
        fs = FakeSanlock()
        fs.write_resource("lockspace", "resource", [("path", 1048576)])
        fs.add_lockspace("lockspace", 1, "path")
        fd = fs.register()
        with self.assertRaises(fs.SanlockException) as e:
            fs.release("lockspace", "resource", [("path", 1048576)], slkfd=fd)
        self.assertEqual(e.exception.errno, errno.EPERM)
