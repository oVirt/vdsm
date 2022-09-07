# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import
from __future__ import division

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
