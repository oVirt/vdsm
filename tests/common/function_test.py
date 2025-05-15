# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import gc

from vdsm.common import function

from testlib import VdsmTestCase as TestCaseBase


class TestWeakmethod(TestCaseBase):

    def setUp(self):
        self.saved_flags = gc.get_debug()
        gc.disable()
        gc.set_debug(0)

    def tearDown(self):
        gc.collect()
        for obj in gc.garbage:
            if type(obj) is ObjectWithDel:
                obj.public = None
                gc.garbage.remove(obj)
        gc.set_debug(self.saved_flags)
        gc.enable()

    def test_without_reference_cycle(self):
        obj = ObjectWithDel()
        obj.public = function.weakmethod(obj.public)
        self.assertEqual(obj.public(), ("public", (), {}))
        del obj
        gc.collect()
        self.assertNotIn(ObjectWithDel, [type(obj) for obj in gc.garbage])

    def test_raise_on_invalid_weakref(self):
        obj = ObjectWithDel()
        method = function.weakmethod(obj.public)
        obj.public = method
        self.assertEqual(obj.public(), ("public", (), {}))
        del obj
        self.assertRaises(function.InvalidatedWeakRef, method)


class ObjectWithDel(object):

    def public(self, *args, **kw):
        return 'public', args, kw

    def __del__(self):
        print('__del__', self.__class__.__name__)
