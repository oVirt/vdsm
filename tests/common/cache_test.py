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

import collections

from testlib import VdsmTestCase as TestCaseBase
from testlib import permutations, expandPermutations

from vdsm.common import cache


@expandPermutations
class TestMemoized(TestCaseBase):

    def setUp(self):
        self.values = {}
        self.accessed = collections.defaultdict(int)

    @permutations([[()], [("a",)], [("a", "b")]])
    def test_memoized_method(self, args):
        self.values[args] = 42
        self.assertEqual(self.accessed[args], 0)
        self.assertEqual(self.memoized_method(*args), 42)
        self.assertEqual(self.accessed[args], 1)
        self.assertEqual(self.memoized_method(*args), 42)
        self.assertEqual(self.accessed[args], 1)

    @permutations([[()], [("a",)], [("a", "b")]])
    def test_memoized_function(self, args):
        self.values[args] = 42
        self.assertEqual(self.accessed[args], 0)
        self.assertEqual(memoized_function(self, *args), 42)
        self.assertEqual(self.accessed[args], 1)
        self.assertEqual(memoized_function(self, *args), 42)
        self.assertEqual(self.accessed[args], 1)

    def test_key_error(self):
        self.assertRaises(KeyError, self.memoized_method)
        self.assertRaises(KeyError, self.memoized_method, "a")
        self.assertRaises(KeyError, self.memoized_method, "a", "b")

    def test_invalidate_method(self):
        args = ("a",)
        self.values[args] = 42
        self.assertEqual(self.memoized_method(*args), 42)
        self.memoized_method.invalidate()
        self.assertEqual(self.memoized_method(*args), 42)
        self.assertEqual(self.accessed[args], 2)

    def test_invalidate_function(self):
        args = ("a",)
        self.values[args] = 42
        self.assertEqual(memoized_function(self, *args), 42)
        memoized_function.invalidate()
        self.assertEqual(memoized_function(self, *args), 42)
        self.assertEqual(self.accessed[args], 2)

    @cache.memoized
    def memoized_method(self, *args):
        return self.get(args)

    def get(self, key):
        self.accessed[key] += 1
        return self.values[key]


@cache.memoized
def memoized_function(test, *args):
    return test.get(args)
