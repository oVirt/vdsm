# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

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
