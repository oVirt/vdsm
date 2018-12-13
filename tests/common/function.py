#
# Copyright 2017 Red Hat, Inc.
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
from __future__ import division

import gc

import six

from vdsm.common import function

from testlib import VdsmTestCase as TestCaseBase
from testValidation import skipif


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

    @skipif(six.PY3, "https://docs.python.org/3/library/gc.html#gc.garbage")
    def test_with_reference_cycle(self):
        def _leaking_wrapper(meth):
            def wrapper(*args, **kwargs):
                return meth(*args, **kwargs)
            return wrapper

        obj = ObjectWithDel()
        obj.public = _leaking_wrapper(obj.public)
        self.assertEqual(obj.public(), ("public", (), {}))
        del obj
        gc.collect()
        self.assertIn(ObjectWithDel, [type(obj) for obj in gc.garbage])

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
