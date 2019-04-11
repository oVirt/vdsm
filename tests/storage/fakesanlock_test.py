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
import pytest
from .fakesanlock import FakeSanlock
from vdsm.common import concurrent
from vdsm.storage.compat import sanlock
from testlib import VdsmTestCase


class ExpectedError(Exception):
    pass


class TestFakeSanlock(VdsmTestCase):

    # Managing lockspaces

    def test_add_lockspace_sync(self):
        fs = FakeSanlock()
        fs.init_lockspace("lockspace", "path")
        fs.add_lockspace("lockspace", 1, "path")
        ls = fs.spaces["lockspace"]
        self.assertEqual(ls["host_id"], 1)
        self.assertEqual(ls["path"], "path")
        self.assertEqual(ls["offset"], 0)
        self.assertEqual(ls["iotimeout"], 0)
        self.assertTrue(ls["ready"].is_set(), "lockspace is not ready")

    def test_add_lockspace_options(self):
        fs = FakeSanlock()
        fs.init_lockspace("lockspace", "path", offset=42)
        fs.add_lockspace("lockspace", 1, "path", offset=42, iotimeout=10)
        ls = fs.spaces["lockspace"]
        self.assertEqual(ls["offset"], 42)
        self.assertEqual(ls["iotimeout"], 10)

    def test_add_lockspace_async(self):
        fs = FakeSanlock()
        fs.init_lockspace("lockspace", "path")
        fs.add_lockspace("lockspace", 1, "path", **{'async': True})
        ls = fs.spaces["lockspace"]
        self.assertEqual(ls["iotimeout"], 0)
        self.assertFalse(ls["ready"].is_set(), "lockspace is ready")

    def test_rem_lockspace_sync(self):
        fs = FakeSanlock()
        fs.init_lockspace("lockspace", "path")
        fs.add_lockspace("lockspace", 1, "path")
        fs.rem_lockspace("lockspace", 1, "path")
        self.assertNotIn("host_id", fs.spaces["lockspace"])

    def test_rem_lockspace_async(self):
        fs = FakeSanlock()
        fs.init_lockspace("lockspace", "path")
        fs.add_lockspace("lockspace", 1, "path")
        fs.rem_lockspace("lockspace", 1, "path", **{'async': True})
        ls = fs.spaces["lockspace"]
        self.assertFalse(ls["ready"].is_set(), "lockspace is ready")

    def test_rem_lockspace_while_holding_lock(self):
        fs = FakeSanlock()
        fs.init_lockspace("lockspace", "path")
        fs.write_resource("lockspace", "resource", [("path", 1048576)])
        fs.add_lockspace("lockspace", 1, "path")
        fd = fs.register()
        fs.acquire("lockspace", "resource", [("path", 1048576)], slkfd=fd)
        fs.rem_lockspace("lockspace", 1, "path", **{"async": True})

        # Fake sanlock return special None value when sanlock is in process of
        # releasing host_id.
        acquired = fs.inq_lockspace("lockspace", 1, "path")
        self.assertIsNone(acquired, "locksapce is not being released")

        # Finish rem_lockspace.
        fs.complete_async("lockspace")

        # Lock shouldn't be hold any more.
        acquired = fs.inq_lockspace("lockspace", 1, "path")
        self.assertIsNotNone(acquired, "lockspace still acquired")
        self.assertFalse(acquired, "lockspace still acquired")

    def test_inq_lockspace_acquired(self):
        fs = FakeSanlock()
        fs.init_lockspace("lockspace", "path")
        fs.add_lockspace("lockspace", 1, "path")
        acquired = fs.inq_lockspace("lockspace", 1, "path")
        self.assertTrue(acquired, "lockspace not acquired")

    def test_inq_lockspace_acquring_no_wait(self):
        fs = FakeSanlock()
        fs.init_lockspace("lockspace", "path")
        fs.add_lockspace("lockspace", 1, "path", **{'async': True})
        acquired = fs.inq_lockspace("lockspace", 1, "path")
        self.assertIsNone(acquired, "lockspace is ready")

    def test_inq_lockspace_acquiring_wait(self):
        fs = FakeSanlock()
        fs.init_lockspace("lockspace", "path")
        fs.add_lockspace("lockspace", 1, "path", **{'async': True})

        t = concurrent.thread(fs.complete_async, args=("lockspace",))
        t.start()
        try:
            acquired = fs.inq_lockspace("lockspace", 1, "path", wait=True)
        finally:
            t.join()
        self.assertTrue(acquired, "lockspace not acquired")

    def test_inq_lockspace_released(self):
        fs = FakeSanlock()
        fs.init_lockspace("lockspace", "path")
        fs.add_lockspace("lockspace", 1, "path")
        fs.rem_lockspace("lockspace", 1, "path")
        acquired = fs.inq_lockspace("lockspace", 1, "path")
        self.assertFalse(acquired, "lockspace not released")

    def test_inq_lockspace_releasing_no_wait(self):
        fs = FakeSanlock()
        fs.init_lockspace("lockspace", "path")
        fs.add_lockspace("lockspace", 1, "path")
        fs.rem_lockspace("lockspace", 1, "path", **{'async': True})
        acquired = fs.inq_lockspace("lockspace", 1, "path")
        self.assertFalse(acquired, "lockspace not released")

    def test_inq_lockspace_releasing_wait(self):
        fs = FakeSanlock()
        fs.init_lockspace("lockspace", "path")
        fs.add_lockspace("lockspace", 1, "path")
        fs.rem_lockspace("lockspace", 1, "path", **{'async': True})

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
        fs.init_lockspace("lockspace", "path")
        fs.write_resource("lockspace", "resource", [("path", 1048576)])
        fs.add_lockspace("lockspace", 1, "path")
        fd = fs.register()
        fs.acquire("lockspace", "resource", [("path", 1048576)], slkfd=fd)
        res = fs.read_resource("path", 1048576)
        self.assertTrue(res["acquired"], "resource is not acquired")
        self.assertEqual(fs.spaces["lockspace"]["host_id"], res["host_id"])
        self.assertEqual(fs.hosts[1]["generation"], res["generation"])

    def test_acquire_no_lockspace(self):
        fs = FakeSanlock()
        fs.write_resource("lockspace", "resource", [("path", 1048576)])
        fd = fs.register()
        with self.assertRaises(fs.SanlockException) as e:
            fs.acquire("lockspace", "resource", [("path", 1048576)], slkfd=fd)
        self.assertEqual(e.exception.errno, errno.ENOSPC)

    def test_acquire_lockspace_adding(self):
        fs = FakeSanlock()
        fs.init_lockspace("lockspace", "path")
        fs.write_resource("lockspace", "resource", [("path", 1048576)])
        fs.add_lockspace("lockspace", 1, "path", **{'async': True})
        fd = fs.register()
        with self.assertRaises(fs.SanlockException) as e:
            fs.acquire("lockspace", "resource", [("path", 1048576)], slkfd=fd)
        self.assertEqual(e.exception.errno, errno.ENOSPC)

    def test_acquire_an_acquired_resource(self):
        fs = FakeSanlock()
        fs.init_lockspace("lockspace", "path")
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
        fs.init_lockspace("lockspace", "path")
        fs.write_resource("lockspace", "resource", [("path", 1048576)])
        fs.add_lockspace("lockspace", 1, "path")
        fd = fs.register()
        fs.acquire("lockspace", "resource", [("path", 1048576)], slkfd=fd)
        fs.release("lockspace", "resource", [("path", 1048576)], slkfd=fd)
        res = fs.read_resource("path", 1048576)
        self.assertFalse(res["acquired"], "resource is not acquired")
        # The resource has been released and the owner is zeroed
        self.assertEqual(res["host_id"], 0)
        self.assertEqual(fs.hosts[1]["generation"], res["generation"])

    def test_release_not_acquired(self):
        fs = FakeSanlock()
        fs.init_lockspace("lockspace", "path")
        fs.write_resource("lockspace", "resource", [("path", 1048576)])
        fs.add_lockspace("lockspace", 1, "path")
        fd = fs.register()
        with self.assertRaises(fs.SanlockException) as e:
            fs.release("lockspace", "resource", [("path", 1048576)], slkfd=fd)
        self.assertEqual(e.exception.errno, errno.EPERM)

    def test_release_no_lockspace(self):
        fs = FakeSanlock()
        with pytest.raises(fs.SanlockException) as e:
            fs.release("lockspace", "resource", [("path", 1048576)])
        assert e.value.errno == errno.ENOSPC

    def test_read_resource_owners_no_owner(self):
        fs = FakeSanlock()
        fs.init_lockspace("lockspace", "path")
        fs.write_resource("lockspace", "resource", [("path", 1048576)])
        fs.add_lockspace("lockspace", 1, "path")
        owners = fs.read_resource_owners("lockspace",
                                         "resource",
                                         [("path", 1048576)])
        assert len(owners) == 0

    def test_read_resource_owners(self):
        fs = FakeSanlock()
        fs.init_lockspace("lockspace", "path")
        fs.write_resource("lockspace", "resource", [("path", 1048576)])
        fs.add_lockspace("lockspace", 1, "path")
        fd = fs.register()
        fs.acquire("lockspace", "resource", [("path", 1048576)], slkfd=fd)
        owners = fs.read_resource_owners("lockspace",
                                         "resource",
                                         [("path", 1048576)])
        assert len(owners) == 1
        assert owners[0]["host_id"] == 1
        assert owners[0]["generation"] == 0

    def test_read_resource_owners_resource_released(self):
        fs = FakeSanlock()
        fs.init_lockspace("lockspace", "path")
        fs.write_resource("lockspace", "resource", [("path", 1048576)])
        fs.add_lockspace("lockspace", 1, "path")
        fd = fs.register()
        fs.acquire("lockspace", "resource", [("path", 1048576)], slkfd=fd)
        fs.release("lockspace", "resource", [("path", 1048576)], slkfd=fd)
        owners = fs.read_resource_owners(
            "lockspace", "resource", [("path", 1048576)])
        self.assertEqual(owners, [], "resource still has owners")

    def test_read_resource_owners_lockspace_removed(self):
        fs = FakeSanlock()
        fs.init_lockspace("lockspace", "path")
        fs.write_resource("lockspace", "resource", [("path", 1048576)])
        fs.add_lockspace("lockspace", 1, "path")
        fd = fs.register()
        fs.acquire("lockspace", "resource", [("path", 1048576)], slkfd=fd)
        fs.release("lockspace", "resource", [("path", 1048576)], slkfd=fd)
        fs.rem_lockspace("lockspace", 1, "path")
        owners = fs.read_resource_owners(
            "lockspace", "resource", [("path", 1048576)])
        self.assertEqual(owners, [], "resource still has owners")

    def test_get_hosts(self):
        fs = FakeSanlock()
        fs.init_lockspace("lockspace", "path")
        fs.add_lockspace("lockspace", 1, "path")
        host = fs.get_hosts("lockspace", 1)
        assert host[0]["id"] == 1
        assert host[0]["generation"] == 0

    def test_get_hosts_no_lockspace(self):
        fs = FakeSanlock()
        with pytest.raises(fs.SanlockException) as e:
            fs.get_hosts("lockspace", 1)
        assert e.value.errno == errno.ENOSPC

    def test_add_lockspace_generation_increase(selfs):
        fs = FakeSanlock()
        fs.init_lockspace("lockspace", "path")
        fs.write_resource("lockspace", "resource", [("path", 1048576)])
        fs.add_lockspace("lockspace", 1, "path")
        fs.rem_lockspace("lockspace", 1, "path")
        fs.add_lockspace("lockspace", 1, "path")
        host = fs.get_hosts("lockspace", 1)
        assert host[0]["id"] == 1
        assert host[0]["generation"] == 1
        assert host[0]["flags"] == sanlock.HOST_LIVE

    def test_init_lockspace(self):
        lockspace = "lockspace"
        fs = FakeSanlock()

        assert lockspace not in fs.spaces

        fs.init_lockspace(lockspace, "/var/tmp/test", offset=0, max_hosts=1)

        expected = {
            "path": "/var/tmp/test",
            "offset": 0,
            "max_hosts": 1,
            "num_hosts": 0,
            "use_aio": False,
        }
        assert expected == fs.spaces[lockspace]

    def test_init_resource(self):
        fs = FakeSanlock()
        fs.init_lockspace("lockspace", "path")
        fs.init_resource("lockspace", "resource", [("path", 1048576)])
        info = fs.read_resource("path", 1048576)
        expected = {"resource": "resource",
                    "lockspace": "lockspace",
                    "version": 0,
                    "acquired": False}
        self.assertEqual(info, expected)

    def test_add_without_init_lockpsace(self):
        fs = FakeSanlock()
        with pytest.raises(fs.SanlockException) as e:
            fs.add_lockspace("lockspace", 1, "path")
        assert e.value.errno == fs.SANLK_LEADER_MAGIC

    def test_add_lockspace_twice(self):
        fs = FakeSanlock()
        fs.init_lockspace("lockspace", "path")
        fs.add_lockspace("lockspace", 1, "path")
        with pytest.raises(fs.SanlockException) as e:
            fs.add_lockspace("lockspace", 1, "path")
        assert e.value.errno == errno.EEXIST

    def test_add_lockspace_wrong_path(self):
        fs = FakeSanlock()
        fs.init_lockspace("lockspace", "path")
        with pytest.raises(fs.SanlockException) as e:
            fs.add_lockspace("lockspace", 1, "path2", )
        assert e.value.errno == errno.EINVAL

    def test_add_lockspace_wrong_offset(self):
        fs = FakeSanlock()
        fs.init_lockspace("lockspace", "path")
        with pytest.raises(fs.SanlockException) as e:
            fs.add_lockspace("lockspace", 1, "path", offset=42)
        assert e.value.errno == errno.EINVAL
