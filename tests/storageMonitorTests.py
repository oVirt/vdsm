#
# Copyright 2014 Red Hat, Inc.
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

from storage import monitor
from testlib import VdsmTestCase


class FrozenStatusTests(VdsmTestCase):

    def setUp(self):
        self.status = monitor.Status()
        self.frozen = monitor.FrozenStatus(self.status)

    def test_copy_attributes(self):
        for name in self.status.__slots__:
            value = getattr(self.status, name)
            expected = getattr(self.frozen, name)
            self.assertEquals(value, expected)

    def test_setting_attribute_raises(self):
        for name in self.status.__slots__:
            self.assertRaises(AssertionError, setattr, self.frozen, name, 1)

    def test_deleting_attribute_raises(self):
        for name in self.status.__slots__:
            self.assertRaises(AssertionError, delattr, self.frozen, name)

    def test_valid(self):
        self.assertEqual(self.frozen.valid, self.status.valid)


class StatusValidTests(VdsmTestCase):

    def test_valid(self):
        s = monitor.Status()
        self.assertIsNone(s.error)
        self.assertTrue(s.valid)

    def test_invalid(self):
        s = monitor.Status()
        s.error = Exception()
        self.assertFalse(s.valid)
