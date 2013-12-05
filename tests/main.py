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

from gluster import exception as gluster_exception
from storage import storage_exception
from storage.securable import secured, SecureError, unsecured
from vdsm.utils import GeneralException

from testrunner import VdsmTestCase as TestCaseBase


class TestSecurable(TestCaseBase):

    @secured
    class MySecureClass(object):

        def __init__(self):
            self.secured = False

        def __is_secure__(self):
            return self.secured

        def securedMethod(self):
            "securedMethod docstring"
            pass

        @unsecured
        def unsecuredMethod(self):
            "unsecuredMethod docstring"
            pass

    class MyClassWithoutIsSecureMethod(object):
        pass

    class MyClassIsSecureClassMethod(object):

        @classmethod
        def __is_secure__(self):
            pass

    def assertUnsecured(self, secureObject):
        self.assertRaises(SecureError, secureObject.securedMethod)
        secureObject.unsecuredMethod()

    def assertSecured(self, secureObject):
        secureObject.securedMethod()
        secureObject.unsecuredMethod()

    def testIsSecureMethodCheck(self):
        self.assertRaises(NotImplementedError, secured,
                          TestSecurable.MyClassWithoutIsSecureMethod)
        self.assertRaises(NotImplementedError, secured,
                          TestSecurable.MyClassIsSecureClassMethod)

    def testSecurable(self):
        secureObject = TestSecurable.MySecureClass()
        self.assertUnsecured(secureObject)

        secureObject.secured = True
        self.assertSecured(secureObject)

        secureObject.secured = False
        self.assertUnsecured(secureObject)

    def testSecurityOverride(self):
        secureObject = TestSecurable.MySecureClass()
        secureObject.securedMethod(__securityOverride=True)

    def testDocstringWrapping(self):
        secureObject = TestSecurable.MySecureClass()

        self.assertEqual(secureObject.securedMethod.__doc__,
                         "securedMethod docstring")
        self.assertEqual(secureObject.unsecuredMethod.__doc__,
                         "unsecuredMethod docstring")


class TestStorageExceptions(TestCaseBase):
    def test_collisions(self):
        codes = {}

        for name in dir(storage_exception):
            obj = getattr(storage_exception, name)

            if not isinstance(obj, types.TypeType):
                continue

            if not issubclass(obj, GeneralException):
                continue

            self.assertFalse(obj.code in codes)
            self.assertTrue(obj.code < 5000)


class TestGlusterExceptions(TestCaseBase):
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
