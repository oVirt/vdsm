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

from fakesanlock import FakeSanlock
from testlib import VdsmTestCase


class ExpectedError(Exception):
    pass


class TestFakeSanlock(VdsmTestCase):

    def test_write_read_resource(self):
        fs = FakeSanlock()
        fs.write_resource("lockspace", "resource", [("path", 1048576)])
        info = fs.read_resource("path", 1048576)
        expected = {"resource": "resource",
                    "lockspace": "lockspace",
                    "version": 0}
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
