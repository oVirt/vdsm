# Copyright 2016-2020 Red Hat, Inc.
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

from network.compat import mock

from vdsm.network.link import setup as linksetup

BOND1_NAME = 'bond1'


@mock.patch('vdsm.network.netconfpersistence.RunningConfig')
@mock.patch.object(linksetup, 'address')
@mock.patch.object(linksetup, 'dhclient')
@mock.patch.object(linksetup, 'Bond')
class TestLinkSetupBond(object):
    def test_add_bonds(self, BondMock, dhclient_mock, address_mock, ConfMock):
        config_mock = ConfMock()

        bond_slaves = ['nic1', 'nic2']
        bond_options = 'mode=1 miimon=120'
        bond_attrs = {
            'nics': bond_slaves,
            'options': bond_options,
            'switch': 'foo',
        }
        setup_new_bond = {BOND1_NAME: bond_attrs}
        setup_bonds = linksetup.SetupBonds(setup_new_bond, {}, {}, config_mock)
        setup_bonds.add_bonds()

        expected_ifaces = set(bond_slaves + [BOND1_NAME])
        assert expected_ifaces == setup_bonds.ifaces_for_acquirement
        BondMock.assert_called_once_with(
            BOND1_NAME,
            slaves=set(bond_slaves),
            options={'mode': '1', 'miimon': '120'},
        )
        config_mock.setBonding.assert_called_once_with(BOND1_NAME, bond_attrs)
        self._assert_ip_flush_called(bond_slaves, dhclient_mock, address_mock)

    def test_remove_bonds(
        self, BondMock, dhclient_mock, address_mock, ConfMock
    ):
        config_mock = ConfMock()

        setup_remove_bond = {BOND1_NAME: {'remove': True}}
        setup_bonds = linksetup.SetupBonds(
            {}, {}, setup_remove_bond, config_mock
        )
        setup_bonds.remove_bonds()

        BondMock.assert_called_once_with(BOND1_NAME)
        config_mock.removeBonding.assert_called_once_with(BOND1_NAME)

    def test_edit_by_adding_slaves_to_bond(
        self, BondMock, dhclient_mock, address_mock, ConfMock
    ):
        config_mock = ConfMock()

        bond_slaves = {'nic1', 'nic2'}
        bond_options = 'mode=1 miimon=120'
        bond_attrs = {
            'nics': list(bond_slaves),
            'options': bond_options,
            'switch': 'foo',
        }
        setup_edit_bond = {BOND1_NAME: bond_attrs}

        setup_bonds = linksetup.SetupBonds(
            {}, setup_edit_bond, {}, config_mock
        )
        # Initial state: Bond exists with no slaves.
        BondMock.return_value.master = BOND1_NAME
        BondMock.return_value.slaves = set()

        setup_bonds.edit_bonds()

        assert bond_slaves | {BOND1_NAME} == setup_bonds.ifaces_for_acquirement
        BondMock.assert_called_with(BOND1_NAME)
        BondMock.return_value.add_slaves.assert_called_once_with(bond_slaves)
        config_mock.setBonding.assert_called_with(BOND1_NAME, bond_attrs)

    @staticmethod
    def _assert_ip_flush_called(bond_slaves, dhclient_mock, address_mock):
        for slave in bond_slaves:
            dhclient_mock.kill.assert_any_call(slave, family=4)
            dhclient_mock.kill.assert_any_call(slave, family=6)
            address_mock.flush.assert_any_call(slave)


def test_parse_nets_bridge_opts():
    nets = {
        'br1': {
            'custom': {
                'bridge_opts': 'multicast_router=0 multicast_snooping=0'
            }
        },
        'br2': {
            'custom': {
                'bridge_opts': 'multicast_router=1 multicast_snooping=1'
            }
        },
    }
    expected = {
        'br1': {'multicast_router': '0', 'multicast_snooping': '0'},
        'br2': {'multicast_router': '1', 'multicast_snooping': '1'},
    }

    for name, opts in linksetup.parse_nets_bridge_opts(nets):
        assert expected[name] == opts
