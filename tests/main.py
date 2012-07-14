#
# Copyright 2012 Red Hat, Inc.
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

import types
import unittest
from nose.plugins.skip import SkipTest
from storage import storage_exception
from storage import securable
from testrunner import VdsmTestCase as TestCaseBase

# If the test is run under the installed vdsm, vdsm-cluster may not be always
# installed, so just ignore the exception.
# In the individual test, setUp() will skip test if  gluster is not imported
try:
    from gluster import exception as gluster_exception
except ImportError:
    pass


class TestSecurable(TestCaseBase):
    class MySecureClass(securable.Securable):
        pass

    def test_safety(self):
        objA = TestSecurable.MySecureClass()
        objB = TestSecurable.MySecureClass()
        objA._setSafe()
        objB._setUnsafe()
        self.assertTrue(objA._isSafe())


class TestStorageExceptions(TestCaseBase):
    def test_collisions(self):
        codes = {}

        for name in dir(storage_exception):
            obj = getattr(storage_exception, name)

            if not isinstance(obj, types.TypeType):
                continue

            if not issubclass(obj, storage_exception.GeneralException):
                continue

            self.assertFalse(obj.code in codes)
            self.assertTrue(obj.code < 5000)


class TestGlusterExceptions(TestCaseBase):
    def setUp(self):
        if not "gluster_exception" in globals().keys():
            raise SkipTest("vdsm-gluster not found")

    def test_collisions(self):
        codes = {}

        for name in dir(gluster_exception):
            obj = getattr(gluster_exception, name)

            if not isinstance(obj, types.TypeType):
                continue

            if not issubclass(obj, gluster_exception.GlusterException):
                continue

            self.assertFalse(obj.code in codes)
            # gluster exception range: 4100-4800
            self.assertTrue(obj.code >= 4100)
            self.assertTrue(obj.code <= 4800)


if __name__ == '__main__':
    unittest.main()
