#
# Copyright 2020 Red Hat, Inc.
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

from vdsm.network import nmstate

from .testlib import (
    IFACE0,
    IFACE1,
    TESTBOND0,
    create_bond_iface_state,
    disable_iface_ip,
)


class TestBond(object):
    def test_translate_new_bond_with_two_slaves(self):
        bondings = {TESTBOND0: {'nics': [IFACE0, IFACE1], 'switch': 'legacy'}}
        state = nmstate.generate_state(networks={}, bondings=bondings)

        bond0_state = create_bond_iface_state(
            TESTBOND0, 'balance-rr', [IFACE0, IFACE1], mtu=None
        )

        disable_iface_ip(bond0_state)

        expected_state = {nmstate.Interface.KEY: [bond0_state]}
        assert expected_state == state

    def test_translate_edit_bond_with_slaves(self, rconfig_mock):
        bondings = {TESTBOND0: {'nics': [IFACE0, IFACE1], 'switch': 'legacy'}}
        rconfig_mock.bonds = bondings

        state = nmstate.generate_state(networks={}, bondings=bondings)

        bond0_state = create_bond_iface_state(
            TESTBOND0, 'balance-rr', [IFACE0, IFACE1], mtu=None
        )

        expected_state = {nmstate.Interface.KEY: [bond0_state]}
        assert expected_state == state

    def test_translate_new_bond_with_two_slaves_and_options(self):
        bondings = {
            TESTBOND0: {
                'nics': [IFACE0, IFACE1],
                'options': 'mode=4 miimon=150',
                'switch': 'legacy',
            }
        }
        state = nmstate.generate_state(networks={}, bondings=bondings)

        bond0_state = create_bond_iface_state(
            TESTBOND0, '802.3ad', [IFACE0, IFACE1], mtu=None, miimon='150'
        )

        disable_iface_ip(bond0_state)

        expected_state = {nmstate.Interface.KEY: [bond0_state]}
        assert expected_state == state

    def test_translate_remove_bonds(self):
        bondings = {TESTBOND0: {'remove': True}}

        state = nmstate.generate_state(networks={}, bondings=bondings)

        expected_state = {
            nmstate.Interface.KEY: [
                {'name': TESTBOND0, 'type': 'bond', 'state': 'absent'}
            ]
        }
        assert expected_state == state
