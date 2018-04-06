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

import math

from vdsm.virt import collectd

from testlib import VdsmTestCase
from testlib import expandPermutations
from testlib import permutations

from . import collectdlib


@expandPermutations
class CollectdTests(VdsmTestCase):

    @permutations([
        # method, args
        ('list', []),
        ('get', ['error/random_key']),
    ])
    def test_list_without_open(self, method, args):
        with collectdlib.run_server() as server:
            client = collectd.Client(server.path)
            self.assertRaises(
                collectd.NotConnected,
                getattr(client, method),
                *args
            )
            server.stop()  # ensure cleanup

    @permutations([
        # key, value
        ('z=42', [42]),
        ('t=-3', [-3]),
        ('q=7.77', [7.77]),
        ('p=-2.12', [-2.12]),
        ('a=1.1,b=2.2', [1.1, 2.2])
    ])
    def test_get_value(self, key, value):
        with collectdlib.run_server() as server:
            with collectd.Client(server.path) as client:
                x = client.get('success/{key}'.format(key=key))
                self.assertEqual(x, value)

    def test_get_value_nan(self):
        with collectdlib.run_server() as server:
            with collectd.Client(server.path) as client:
                x = client.get(
                    'success/value=NaN',
                    raise_if_nan=False
                )
                self.assertTrue(math.isnan(x[0]))

    def test_raise_valueerror_if_nan(self):
        with collectdlib.run_server() as server:
            with collectd.Client(server.path) as client:
                self.assertRaises(
                    ValueError,
                    client.get,
                    'success/value=NaN',
                )

    def test_get_error(self):
        with collectdlib.run_server() as server:
            with collectd.Client(server.path) as client:
                self.assertRaises(
                    collectd.Error,
                    client.get,
                    'error/error'
                )

    def test_get_not_existent_value(self):
        with collectdlib.run_server() as server:
            with collectd.Client(server.path) as client:
                self.assertRaises(
                    collectd.Error,
                    client.get,
                    'missing/foobar'
                )
