#
# Copyright 2013 Red Hat, Inc.
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

from testlib import VdsmTestCase

import monkeypatch


class FakeModule:

    def __init__(self):
        self.olda = self.a
        self.oldb = self.b
        self.oldc = self.c

    def a(self):
        pass

    def b(self):
        pass

    def c(self):
        pass

    def isClean(self):
        return (self.a == self.olda and
                self.b == self.oldb and
                self.c == self.oldc)


def patched(*args, **kw):
    return 'patched'


class TestMonkeyPatch(VdsmTestCase):

    module = FakeModule()

    def setUp(self):
        self.assertTrue(self.module.isClean())

    def tearDown(self):
        self.assertTrue(self.module.isClean())

    # This method uses unpatched module

    def testNotPatched(self):
        self.assertTrue(self.module.isClean())

    # This method patches module in one way

    @monkeypatch.MonkeyPatch(module, 'a', patched)
    def testPatchOne(self):
        self.assertEqual(self.module.a, patched)
        self.assertNotEqual(self.module.b, patched)

    # This method patches module in another way

    @monkeypatch.MonkeyPatch(module, 'a', patched)
    @monkeypatch.MonkeyPatch(module, 'b', patched)
    def testPatchBoth(self):
        self.assertEqual(self.module.a, patched)
        self.assertEqual(self.module.b, patched)


module = FakeModule()


@monkeypatch.MonkeyClass(module, 'a', patched)
class TestMonkeyClass(VdsmTestCase):

    def tearDown(self):
        self.assertTrue(module.isClean())

    def testPatched(self):
        self.assertEqual(module.a, patched)
        self.assertNotEqual(module.b, patched)
        self.assertNotEqual(module.c, patched)


@monkeypatch.MonkeyClass(module, 'a', patched)
@monkeypatch.MonkeyClass(module, 'b', patched)
class TestMonkeyClassChain(VdsmTestCase):

    def tearDown(self):
        self.assertTrue(module.isClean())

    def testPatched(self):
        self.assertEqual(module.a, patched)
        self.assertEqual(module.b, patched)
        self.assertNotEqual(module.c, patched)


class TestMonkeyPatchFixture(VdsmTestCase):

    def __init__(self, *a, **kw):
        super(VdsmTestCase, self).__init__(*a, **kw)
        self.module = FakeModule()
        self.patch = monkeypatch.Patch([
            (self.module, 'a', patched),
            (self.module, 'b', patched),
        ])

    def setUp(self):
        self.assertTrue(self.module.isClean())
        self.patch.apply()

    def tearDown(self):
        self.patch.revert()
        self.assertTrue(self.module.isClean())

    # All methods use patched module

    def testPatched(self):
        self.assertEqual(self.module.a, patched)
        self.assertEqual(self.module.b, patched)
        self.assertNotEqual(self.module.c, patched)


class TestMonkeyPatchFixtureAssertions(VdsmTestCase):

    def testAlreadyApplied(self):
        patch = monkeypatch.Patch([(FakeModule(), 'a', patched)])
        patch.apply()
        self.assertRaises(AssertionError, patch.apply)

    def testNotApplied(self):
        patch = monkeypatch.Patch([(FakeModule(), 'a', patched)])
        self.assertRaises(AssertionError, patch.revert)

    def testAlreadyReverted(self):
        patch = monkeypatch.Patch([(FakeModule(), 'a', patched)])
        patch.apply()
        patch.revert()
        self.assertRaises(AssertionError, patch.revert)


class Class:
    @staticmethod
    def static_method():
        return 'clean'

    @classmethod
    def class_method(cls):
        return 'clean'

    def instance_method(self):
        return 'clean'


class TestMonkeyPatchClass(VdsmTestCase):

    def testInstanceMethodReplacement(self):
        patch = monkeypatch.Patch([(Class, 'instance_method', patched)])
        self.assertEqual(Class().instance_method(), 'clean')
        old = Class.instance_method
        patch.apply()
        try:
            self.assertEqual(Class().instance_method(), 'patched')
        finally:
            patch.revert()
        self.assertEqual(Class().instance_method(), 'clean')
        self.assertEqual(old, Class.instance_method)

    def testStaticMethodReplacement(self):
        patch = monkeypatch.Patch([(Class, 'static_method', patched)])
        self.assertEqual(Class.static_method(), 'clean')
        old = Class.static_method
        patch.apply()
        try:
            self.assertEqual(Class.static_method(), 'patched')
            self.assertFalse(hasattr(Class.static_method, 'im_self'))
        finally:
            patch.revert()
        self.assertEqual(Class.static_method(), 'clean')
        self.assertEqual(old, Class.static_method)
        self.assertFalse(hasattr(Class.static_method, 'im_self'))

    def testClassMethodReplacement(self):
        patch = monkeypatch.Patch([(Class, 'class_method', patched)])
        self.assertEqual(Class.class_method(), 'clean')
        old = Class.class_method
        patch.apply()
        try:
            self.assertEqual(Class.class_method(), 'patched')
            self.assertEqual(getattr(Class.class_method, 'im_self'), Class)
        finally:
            patch.revert()
        self.assertEqual(Class.class_method(), 'clean')
        self.assertEqual(old, Class.class_method)
        self.assertEqual(getattr(Class.class_method, 'im_self'), Class)
