# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import
from __future__ import division

from testlib import VdsmTestCase
from testlib import expandPermutations
from testlib import permutations

from vdsm.virt.vmdevices import drivename
import pytest


_ITEMS = list(drivename._DEVIFACES.items())


@expandPermutations
class TestDriveNameFunctions(VdsmTestCase):

    @permutations(_ITEMS)
    def test_make_name(self, prefix, iface):
        for index, value in _CONVERTED_VALUES:
            computed = drivename.make(iface, index)
            expected = prefix + value
            assert computed == expected, \
                "mismatch for %s: computed=%s expected=%s" % (
                    (iface, index), computed, expected)

    @permutations(_ITEMS)
    def test_split_name(self, prefix, iface):
        for index, value in _CONVERTED_VALUES:
            computed = drivename.split(prefix + value)
            expected = (iface, index)
            assert computed == expected, \
                "mismatch for %s: computed=%s expected=%s" % (
                    prefix + value, computed, expected)

    @permutations([
        (iface, -1) for iface in drivename._DEVIFACES
    ])
    def test_make_name_invalid_parameters(self, iface, index):
        with pytest.raises(ValueError):
            drivename.make(iface, index)

    @permutations([
        # device_name
        ['foobar_a'],
        ['qda'],
        ['sd$'],
        ['hdB'],
        ['fd0'],
    ])
    def test_split_name_invalid_device(self, device_name):
        with pytest.raises(ValueError):
            drivename.split(device_name)


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
