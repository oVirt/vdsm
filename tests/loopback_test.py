#
# Copyright 2016-2017 Red Hat, Inc.
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
import io
import os

from testlib import VdsmTestCase
from testlib import namedTemporaryDir
from testValidation import ValidateRunningAsRoot, stresstest
from testValidation import broken_on_ci

import loopback


class TestDevice(VdsmTestCase):

    BEFORE = b"a" * 10
    AFTER = b"b" * 10

    @broken_on_ci("Fails randomly on oVirt CI", name="OVIRT_CI")
    @ValidateRunningAsRoot
    def test_with_device(self):
        with namedTemporaryDir(dir="/tmp") as tmpdir:
            filename = os.path.join(tmpdir, "file")
            self.prepare_backing_file(filename)
            with loopback.Device(filename) as device:
                self.assertTrue(device.is_attached())
                self.check_device(device)
            self.assertFalse(device.is_attached())
            self.check_backing_file(filename)

    @ValidateRunningAsRoot
    def test_attach_detach_manually(self):
        with namedTemporaryDir(dir="/tmp") as tmpdir:
            filename = os.path.join(tmpdir, "file")
            self.prepare_backing_file(filename)
            device = loopback.Device(filename)
            device.attach()
            try:
                self.assertTrue(device.is_attached())
                self.check_device(device)
            finally:
                device.detach()
            self.assertFalse(device.is_attached())
            self.check_backing_file(filename)

    @ValidateRunningAsRoot
    @stresstest
    def test_many_devices(self):
        with namedTemporaryDir(dir="/tmp") as tmpdir:
            filename = os.path.join(tmpdir, "file")
            self.prepare_backing_file(filename)
            for i in range(300):
                with loopback.Device(filename) as device:
                    self.assertTrue(device.is_attached())
                self.assertFalse(device.is_attached())

    def prepare_backing_file(self, filename):
        with io.open(filename, "wb") as f:
            f.truncate(1024**3)
            f.write(self.BEFORE)

    def check_device(self, device):
        with io.open(device.path, "r+b", buffering=0) as f:
            self.assertEqual(f.read(len(self.BEFORE)), self.BEFORE)
            f.write(self.AFTER)
            os.fsync(f.fileno())

    def check_backing_file(self, filename):
        with io.open(filename, "rb") as f:
            self.assertEqual(f.read(len(self.BEFORE + self.AFTER)),
                             self.BEFORE + self.AFTER)
