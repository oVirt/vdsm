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

from testlib import VdsmTestCase
from testlib import expandPermutations
from testlib import permutations
from testValidation import xfail

from vdsm.virt.vmdevices import drivename


@expandPermutations
class TestDriveNameFunctions(VdsmTestCase):

    @xfail('drivename.make needs to be fixed')
    @permutations(drivename._DEVIFACES.items())
    def test_make_name(self, prefix, iface):
        for index, value in _CONVERTED_VALUES:
            computed = drivename.make(iface, index)
            expected = prefix + value
            self.assertEqual(
                computed, expected,
                "mismatch for %s: computed=%s expected=%s" % (
                    (iface, index), computed, expected))

    @permutations(drivename._DEVIFACES.items())
    def test_split_name(self, prefix, iface):
        for index, value in _CONVERTED_VALUES:
            computed = drivename.split(prefix + value)
            expected = (iface, index)
            self.assertEqual(
                computed, expected,
                "mismatch for %s: computed=%s expected=%s" % (
                    prefix + value, computed, expected))

    @permutations([
        # device_name
        ['foobar_a'],
        ['qda'],
        ['sd$'],
        ['hdB'],
        ['fd0'],
    ])
    def test_split_name_invalid_device(self, device_name):
        self.assertRaises(ValueError, drivename.split, device_name)


_CONVERTED_VALUES = (
    # index, value
    (0, 'a'),
    (25, 'z'),

    (26, 'aa'),
    (27, 'ab'),

    (51, 'az'),
    (52, 'ba'),

    (77, 'bz'),
    (78, 'ca'),

    (103, 'cz'),
    (104, 'da'),

    (701, 'zz'),
    (702, 'aaa'),

    (999, 'all'),
    (1000, 'alm'),

    (9999, 'ntp'),
    (10000, 'ntq'),

    (18277, 'zzz'),
)
