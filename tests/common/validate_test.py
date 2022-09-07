# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import
from __future__ import division

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
