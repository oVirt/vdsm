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

from __future__ import absolute_import
from __future__ import division

"""
This isn't really integral to the way VDSM works this is just an example on how
to do permutations.
"""

from testlib import permutations, expandPermutations
from testlib import VdsmTestCase as TestCaseBase


def recSum(lst):
    if not lst:
        return 0

    return lst[0] + recSum(lst[1:])


def mysum(lst, strategy):
    if strategy == "recursive":
        return recSum(lst)

    if strategy == "builtin":
        return sum(lst)

    if strategy == "loop":
        s = 0
        for i in lst:
            s += i

        return s


SUM_PREMUTATIONS = (("recursive",),
                    ("builtin",),
                    ("loop",))


@expandPermutations
class SumTests(TestCaseBase):
    @permutations(SUM_PREMUTATIONS)
    def test(self, strategy):
        self.assertEqual(mysum((1, 2, 3), strategy), 6)
