# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import
from __future__ import division

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
        return (cls, 'clean')

    def instance_method(self):
        return (self, 'clean')


def patched_static():
    return 'patched'


def patched_method(self):
    return (self, 'patched')


def patched_clsmethod(cls):
    return (cls, 'patched')


class TestMonkeyPatchClass(VdsmTestCase):

    def testInstanceMethodReplacement(self):
        patch = monkeypatch.Patch([(Class, 'instance_method', patched_method)])
        instance = Class()
        self.assertEqual(instance.instance_method(), (instance, 'clean'))
        self.assertEqual(Class().instance_method()[1], 'clean')
        old = Class.instance_method
        patch.apply()
        try:
            self.assertEqual(instance.instance_method(), (instance, 'patched'))
            self.assertEqual(Class().instance_method()[1], 'patched')
        finally:
            patch.revert()
        self.assertEqual(instance.instance_method(), (instance, 'clean'))
        self.assertEqual(Class().instance_method()[1], 'clean')
        self.assertEqual(old, Class.instance_method)

    def testStaticMethodReplacement(self):
        patch = monkeypatch.Patch([(Class, 'static_method', patched_static)])
        instance = Class()
        self.assertEqual(instance.static_method(), 'clean')
        self.assertEqual(Class.static_method(), 'clean')
        self.assertEqual(Class().static_method(), 'clean')
        old = Class.static_method
        patch.apply()
        try:
            self.assertEqual(instance.static_method(), 'patched')
            self.assertEqual(Class.static_method(), 'patched')
            self.assertEqual(Class().static_method(), 'patched')
            self.assertFalse(hasattr(Class.static_method, '__self__'))
        finally:
            patch.revert()
        self.assertEqual(instance.static_method(), 'clean')
        self.assertEqual(Class.static_method(), 'clean')
        self.assertEqual(Class().static_method(), 'clean')
        self.assertEqual(old, Class.static_method)
        self.assertFalse(hasattr(Class.static_method, '__self__'))

    def testClassMethodReplacement(self):
        patch = monkeypatch.Patch([(Class, 'class_method', patched_clsmethod)])
        instance = Class()
        self.assertEqual(instance.class_method(), (Class, 'clean'))
        self.assertEqual(Class.class_method(), (Class, 'clean'))
        self.assertEqual(Class().class_method(), (Class, 'clean'))
        old = Class.class_method
        patch.apply()
        try:
            self.assertEqual(instance.class_method(), (Class, 'patched'))
            self.assertEqual(Class.class_method(), (Class, 'patched'))
            self.assertEqual(Class().class_method(), (Class, 'patched'))
            self.assertEqual(getattr(Class.class_method, '__self__'), Class)
        finally:
            patch.revert()
        self.assertEqual(instance.class_method(), (Class, 'clean'))
        self.assertEqual(Class.class_method(), (Class, 'clean'))
        self.assertEqual(Class().class_method(), (Class, 'clean'))
        self.assertEqual(old, Class.class_method)
        self.assertEqual(getattr(Class.class_method, '__self__'), Class)
