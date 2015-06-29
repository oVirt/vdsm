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

from testlib import AssertingLock
from testlib import VdsmTestCase
from testlib import recorded


class AssertNotRaisesTests(VdsmTestCase):

    def test_contextmanager_fail(self):
        with self.assertRaises(self.failureException):
            with self.assertNotRaises():
                raise Exception("test failure")

    def test_contextmanager_pass(self):
        with self.assertNotRaises():
            pass

    def test_inline_fail(self):
        def func():
            raise Exception("test failure")
        with self.assertRaises(self.failureException):
            self.assertNotRaises(func)

    def test_inline_pass(self):
        def func():
            pass
        self.assertNotRaises(func)


class AssertingLockTests(VdsmTestCase):

    def test_free(self):
        lock = AssertingLock()
        with lock:
            pass

    def test_locked(self):
        lock = AssertingLock()
        with self.assertRaises(AssertionError):
            with lock:
                with lock:
                    pass


class RecordedTests(VdsmTestCase):

    def test_no_args(self):
        obj = Recorded()
        obj.no_args()
        self.assertEqual(obj.__recording__, [("no_args", (), {})])

    def test_args(self):
        obj = Recorded()
        obj.args(1, 2)
        self.assertEqual(obj.__recording__, [("args", (1, 2), {})])

    def test_kwargs(self):
        obj = Recorded()
        obj.kwargs(a=1, b=2)
        self.assertEqual(obj.__recording__, [("kwargs", (), {"a": 1, "b": 2})])

    def test_kwargs_as_args(self):
        obj = Recorded()
        obj.kwargs(1, 2)
        self.assertEqual(obj.__recording__, [("kwargs", (1, 2), {})])

    def test_no_kwargs(self):
        obj = Recorded()
        obj.args_and_kwargs(1, 2)
        self.assertEqual(obj.__recording__, [("args_and_kwargs", (1, 2), {})])

    def test_some_kwargs(self):
        obj = Recorded()
        obj.args_and_kwargs(1, 2, c=3)
        self.assertEqual(obj.__recording__,
                         [("args_and_kwargs", (1, 2), {"c": 3})])

    def test_args_as_kwargs(self):
        obj = Recorded()
        obj.args_and_kwargs(a=1, b=2)
        self.assertEqual(obj.__recording__,
                         [("args_and_kwargs", (), {"a": 1, "b": 2})])

    def test_flow(self):
        obj = Recorded()
        obj.no_args()
        obj.kwargs(a=1)
        self.assertEqual(obj.__recording__, [
            ("no_args", (), {}),
            ("kwargs", (), {"a": 1}),
        ])


class Recorded(object):

    @recorded
    def args_and_kwargs(self, a, b, c=3, d=4):
        pass

    @recorded
    def args(self, a, b):
        pass

    @recorded
    def kwargs(self, a=1, b=2):
        pass

    @recorded
    def no_args(self):
        pass
