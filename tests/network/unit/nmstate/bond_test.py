# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

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

    def test_translate_remove_bonds(self, rconfig_mock):
        rconfig_mock.bonds = {
            TESTBOND0: {'nics': [IFACE0, IFACE1], 'switch': 'legacy'}
        }
        bondings = {TESTBOND0: {'remove': True}}

        state = nmstate.generate_state(networks={}, bondings=bondings)

        expected_state = {
            nmstate.Interface.KEY: [
                {
                    nmstate.Interface.NAME: TESTBOND0,
                    nmstate.Interface.TYPE: nmstate.InterfaceType.BOND,
                    nmstate.Interface.STATE: nmstate.InterfaceState.ABSENT,
                }
            ]
        }
        assert expected_state == state
