#
# Copyright 2012-2014 Red Hat, Inc.
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

from storage.securable import secured, SecureError, unsecured
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
