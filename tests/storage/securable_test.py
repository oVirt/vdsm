# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import
from __future__ import division

from vdsm.storage.securable import secured, SecureError, unsecured
from testlib import VdsmTestCase


@secured
class SecureClass(object):

    class InnerClass(object):
        pass

    classVariable = 42

    def __init__(self):
        self.secured = False

    def __is_secure__(self):
        return self.secured

    @staticmethod
    def staticMethod():
        pass

    @classmethod
    def classMethod(cls):
        pass

    def securedMethod(self):
        "securedMethod docstring"
        pass

    @unsecured
    def unsecuredMethod(self):
        "unsecuredMethod docstring"
        pass


class ClassWithoutIsSecureMethod(object):
    pass


class ClassIsSecureClassMethod(object):

    @classmethod
    def __is_secure__(cls):
        return True


class TestSecurable(VdsmTestCase):

    def assertUnsecured(self, secureObject):
        self.assertRaises(SecureError, secureObject.securedMethod)
        secureObject.unsecuredMethod()

    def assertSecured(self, secureObject):
        secureObject.securedMethod()
        secureObject.unsecuredMethod()

    def testIsSecureMethodCheck(self):
        self.assertRaises(NotImplementedError, secured,
                          ClassWithoutIsSecureMethod)
        self.assertRaises(NotImplementedError, secured,
                          ClassIsSecureClassMethod)

    def testSecurable(self):
        secureObject = SecureClass()
        self.assertUnsecured(secureObject)

        secureObject.secured = True
        self.assertSecured(secureObject)

        secureObject.secured = False
        self.assertUnsecured(secureObject)

    def testSecurityOverride(self):
        secureObject = SecureClass()
        secureObject.securedMethod(__securityOverride=True)

    def testDocstringWrapping(self):
        secureObject = SecureClass()

        self.assertEqual(secureObject.securedMethod.__doc__,
                         "securedMethod docstring")
        self.assertEqual(secureObject.unsecuredMethod.__doc__,
                         "unsecuredMethod docstring")

    def testInnerClass(self):
        obj = SecureClass.InnerClass()
        self.assertEqual(type(obj), SecureClass.InnerClass)

    def testClassVariable(self):
        self.assertEqual(SecureClass.classVariable, 42)

    def testStaticMethod(self):
        SecureClass.staticMethod()

    def testClassMethod(self):
        SecureClass.classMethod()
        secureObject = SecureClass()
        secureObject.classMethod()
