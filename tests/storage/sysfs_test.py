#
# Copyright 2017 Red Hat, Inc.
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

import os

from testlib import VdsmTestCase
from testlib import temporaryPath

from vdsm.common import password
from vdsm.storage import sysfs


class TestSysfs(VdsmTestCase):

    # read

    def test_read_str(self):
        with temporaryPath(data=b"value") as path:
            self.assertEqual(sysfs.read(path), "value")

    def test_read_str_strip(self):
        with temporaryPath(data=b" \t\n1\n2\n\t ") as path:
            self.assertEqual(sysfs.read(path), "1\n2")

    def test_read_str_missing_default(self):
        self.assertEqual(sysfs.read("/no/such/path", "default"), "default")

    def test_read_str_missing_no_default(self):
        with self.assertRaises(EnvironmentError):
            sysfs.read("/no/such/path")

    def test_read_str_error(self):
        with self.assertRaises(EnvironmentError):
            sysfs.read(os.getcwd())

    # read_int

    def test_read_int(self):
        with temporaryPath(data=b"42") as path:
            self.assertEqual(sysfs.read_int(path), 42)

    def test_read_int_strip(self):
        with temporaryPath(data=b" 42\n ") as path:
            self.assertEqual(sysfs.read_int(path), 42)

    def test_read_int_missing_default(self):
        self.assertEqual(sysfs.read_int("/no/such/path", 7), 7)

    def test_read_int_missing_no_default(self):
        with self.assertRaises(EnvironmentError):
            sysfs.read_int("/no/such/path")

    def test_read_int_error(self):
        with self.assertRaises(EnvironmentError):
            sysfs.read_int(os.getcwd())

    # read_password

    def test_read_password(self):
        with temporaryPath(data=b"password") as path:
            self.assertEqual(sysfs.read_password(path),
                             password.ProtectedPassword("password"))

    def test_read_password_strip(self):
        with temporaryPath(data=b" password\n ") as path:
            self.assertEqual(sysfs.read_password(path),
                             password.ProtectedPassword("password"))

    def test_read_password_missing_default(self):
        self.assertEqual(sysfs.read_password("/no/such/path", ""),
                         password.ProtectedPassword(""))

    def test_read_password_missing_no_default(self):
        with self.assertRaises(EnvironmentError):
            sysfs.read_password("/no/such/path")

    def test_read_password_error(self):
        with self.assertRaises(EnvironmentError):
            sysfs.read_password(os.getcwd())
