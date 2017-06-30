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
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA
# 02110-1301  USA
#
# Refer to the README and COPYING files for full details of the license
#
from __future__ import absolute_import

from vdsm.common import validate

from testlib import VdsmTestCase


class NormalizePCIAddressTests(VdsmTestCase):

    ADDR = {
        'domain': '0x0000',
        'bus': '0x05',
        'slot': '0x10',
        'function': '0x4',
    }

    def test_raise_mixed_format(self):
        src = {
            'domain': '0x0000', 'bus': '5', 'slot': '0x10', 'function': '4'
        }

        self.assertRaises(
            ValueError,
            validate.normalize_pci_address,
            **src
        )

    def test_input_hex(self):
        self._verify_addr(self.ADDR.copy())

    def test_input_hex_no_padding(self):
        self._verify_addr({
            'domain': '0x0', 'bus': '0x5', 'slot': '0x10', 'function': '0x4'
        })

    def test_input_hex_mixed_padding(self):
        self._verify_addr({
            'domain': '0x0000', 'bus': '0x5', 'slot': '0x10', 'function': '0x4'
        })

    def test_input_dec(self):
        self._verify_addr({
            'domain': '0', 'bus': '5', 'slot': '16', 'function': '4'
        })

    def _verify_addr(self, src):
        self.assertEqual(
            validate.normalize_pci_address(**src),
            self.ADDR
        )
