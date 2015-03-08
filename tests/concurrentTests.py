#
# Copyright 2015 Red Hat, Inc.
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
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA
#
# Refer to the README and COPYING files for full details of the license
#

import time
import random

from testlib import VdsmTestCase

from vdsm import concurrent


class TMapTests(VdsmTestCase):

    def test_results(self):
        values = tuple(range(10))
        results = concurrent.tmap(lambda x: x, values)
        self.assertEqual(results, values)

    def test_results_order(self):
        def func(x):
            time.sleep(x)
            return x
        values = tuple(random.random() * 0.1 for x in range(10))
        results = concurrent.tmap(func, values)
        self.assertEqual(results, values)

    def test_concurrency(self):
        start = time.time()
        concurrent.tmap(time.sleep, [0.1] * 10)
        elapsed = time.time() - start
        self.assertTrue(0.1 < elapsed < 0.2)

    def test_error(self):
        def func(x):
            raise RuntimeError()
        self.assertRaises(RuntimeError, concurrent.tmap, func, range(10))
