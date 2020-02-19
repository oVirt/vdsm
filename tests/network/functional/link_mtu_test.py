#
# Copyright 2017-2020 Red Hat, Inc.
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

import pytest

from network.nettestlib import dummy_devices

from . import netfunctestlib as nftestlib

from vdsm.network import nmstate


NETWORK_NAME = 'test-network'
NETWORK1_NAME = 'test-network1'
NETWORK2_NAME = 'test-network2'
BOND_NAME = 'bond1'
BOND_NAME2 = 'bond2'
VLAN1 = 10
VLAN2 = 20
DEFAULT_MTU = 1500
MTU_2100 = 2100
MTU_2000 = 2000
MTU_1600 = 1600
MTU_1000 = 1000


adapter = None


@pytest.fixture(scope='module', autouse=True)
def create_adapter(target):
    global adapter
    adapter = nftestlib.NetFuncTestAdapter(target)


parametrize_vlan = pytest.mark.parametrize(
    'vlan', [False, True], ids=['non-vlan', 'vlan']
)


@pytest.mark.nmstate
@nftestlib.parametrize_switch
class TestNetworkMtu(object):
    @nftestlib.parametrize_bridged
    def test_add_net_with_mtu(self, switch, bridged):
        with dummy_devices(1) as (nic,):
            NETCREATE = {
                NETWORK_NAME: {
                    'nic': nic,
                    'bridged': bridged,
                    'mtu': MTU_2000,
                    'switch': switch,
                }
            }
            with adapter.setupNetworks(NETCREATE, {}, nftestlib.NOCHK):
                adapter.assertNetwork(NETWORK_NAME, NETCREATE[NETWORK_NAME])
                adapter.assertLinkMtu(nic, NETCREATE[NETWORK_NAME])

    @nftestlib.parametrize_bridged
    @nftestlib.parametrize_bonded
    @parametrize_vlan
    @pytest.mark.parametrize(
        'mtu', [MTU_2100, MTU_1600], ids=['mtu-higher', 'mtu-lower']
    )
    def test_edit_mtu_on_network(self, switch, bridged, bonded, vlan, mtu):
        with dummy_devices(1) as (nic,):
            net_attrs = {'bridged': bridged, 'mtu': MTU_2000, 'switch': switch}
            if bonded:
                base_iface = BOND_NAME
                net_attrs['bonding'] = BOND_NAME
                BONDBASE = {BOND_NAME: {'nics': [nic], 'switch': switch}}
            else:
                base_iface = nic
                net_attrs['nic'] = nic
                BONDBASE = {}
            if vlan:
                net_attrs['vlan'] = VLAN1
            with adapter.setupNetworks(
                {NETWORK_NAME: net_attrs}, BONDBASE, nftestlib.NOCHK
            ):
                net_attrs['mtu'] = mtu
                adapter.setupNetworks(
                    {NETWORK_NAME: net_attrs}, {}, nftestlib.NOCHK
                )
                adapter.assertNetwork(NETWORK_NAME, net_attrs)
                if bonded:
                    adapter.assertLinkMtu(BOND_NAME, net_attrs)
                if vlan:
                    adapter.assertLinkMtu(f'{base_iface}.{VLAN1}', net_attrs)
                adapter.assertLinkMtu(nic, net_attrs)

    @nftestlib.parametrize_bridged
    @nftestlib.parametrize_bonded
    def test_removing_a_net_updates_the_mtu(self, switch, bridged, bonded):
        with dummy_devices(1) as (nic,):
            NETWORK1_ATTRS = {
                'bridged': bridged,
                'vlan': VLAN1,
                'mtu': MTU_1600,
                'switch': switch,
            }
            NETWORK2_ATTRS = {
                'bridged': bridged,
                'vlan': VLAN2,
                'mtu': MTU_2000,
                'switch': switch,
            }
            NETBASE = {
                NETWORK1_NAME: NETWORK1_ATTRS,
                NETWORK2_NAME: NETWORK2_ATTRS,
            }
            if bonded:
                NETWORK1_ATTRS['bonding'] = BOND_NAME
                NETWORK2_ATTRS['bonding'] = BOND_NAME
                BONDBASE = {BOND_NAME: {'nics': [nic], 'switch': switch}}
                link2monitor = BOND_NAME
            else:
                NETWORK1_ATTRS['nic'] = nic
                NETWORK2_ATTRS['nic'] = nic
                BONDBASE = {}
                link2monitor = nic

            with adapter.setupNetworks(NETBASE, BONDBASE, nftestlib.NOCHK):
                with nftestlib.monitor_stable_link_state(link2monitor):
                    adapter.setupNetworks(
                        {NETWORK2_NAME: {'remove': True}}, {}, nftestlib.NOCHK
                    )
                    adapter.assertNetwork(NETWORK1_NAME, NETWORK1_ATTRS)
                    adapter.assertLinkMtu(nic, NETWORK1_ATTRS)

                    if bonded:
                        vlan = BOND_NAME + '.' + str(NETWORK1_ATTRS['vlan'])
                        adapter.assertLinkMtu(BOND_NAME, NETWORK1_ATTRS)
                    else:
                        vlan = nic + '.' + str(NETWORK1_ATTRS['vlan'])

                    adapter.assertLinkMtu(vlan, NETWORK1_ATTRS)

    @nftestlib.parametrize_bridged
    @nftestlib.parametrize_bonded
    def test_adding_a_net_updates_the_mtu(self, switch, bridged, bonded):
        with dummy_devices(1) as (nic,):
            NETWORK1_ATTRS = {
                'bridged': bridged,
                'vlan': VLAN1,
                'mtu': MTU_1600,
                'switch': switch,
            }
            NETWORK2_ATTRS = {
                'bridged': bridged,
                'vlan': VLAN2,
                'mtu': MTU_2000,
                'switch': switch,
            }
            NETBASE = {NETWORK1_NAME: NETWORK1_ATTRS}
            NETNEW = {NETWORK2_NAME: NETWORK2_ATTRS}

            if bonded:
                NETWORK1_ATTRS['bonding'] = BOND_NAME
                NETWORK2_ATTRS['bonding'] = BOND_NAME
                BONDBASE = {BOND_NAME: {'nics': [nic], 'switch': switch}}
                link2monitor = BOND_NAME
            else:
                NETWORK1_ATTRS['nic'] = nic
                NETWORK2_ATTRS['nic'] = nic
                BONDBASE = {}
                link2monitor = nic

            with adapter.setupNetworks(NETBASE, BONDBASE, nftestlib.NOCHK):
                with nftestlib.monitor_stable_link_state(link2monitor):
                    with adapter.setupNetworks(NETNEW, {}, nftestlib.NOCHK):
                        adapter.assertNetwork(NETWORK2_NAME, NETWORK2_ATTRS)
                        adapter.assertLinkMtu(nic, NETWORK2_ATTRS)

                        if bonded:
                            vlan1 = (
                                BOND_NAME + '.' + str(NETWORK1_ATTRS['vlan'])
                            )
                            vlan2 = (
                                BOND_NAME + '.' + str(NETWORK2_ATTRS['vlan'])
                            )
                            adapter.assertLinkMtu(BOND_NAME, NETWORK2_ATTRS)
                        else:
                            vlan1 = nic + '.' + str(NETWORK1_ATTRS['vlan'])
                            vlan2 = nic + '.' + str(NETWORK2_ATTRS['vlan'])

                        adapter.assertLinkMtu(vlan1, NETWORK1_ATTRS)
                        adapter.assertLinkMtu(vlan2, NETWORK2_ATTRS)

    def test_add_slave_to_a_bonded_network_with_non_default_mtu(self, switch):
        with dummy_devices(2) as (nic1, nic2):
            NETBASE = {
                NETWORK_NAME: {
                    'bonding': BOND_NAME,
                    'bridged': False,
                    'mtu': MTU_2000,
                    'switch': switch,
                }
            }
            BONDBASE = {BOND_NAME: {'nics': [nic1], 'switch': switch}}

            with adapter.setupNetworks(NETBASE, BONDBASE, nftestlib.NOCHK):
                BONDBASE[BOND_NAME]['nics'].append(nic2)
                adapter.setupNetworks({}, BONDBASE, nftestlib.NOCHK)
                adapter.assertLinkMtu(nic2, NETBASE[NETWORK_NAME])

    def test_bond_remove_with_non_default_mtu_resets_slaves(self, switch):
        with dummy_devices(2) as (nic1, nic2):
            net_attrs = {
                'bonding': BOND_NAME,
                'bridged': False,
                'mtu': MTU_2100,
                'switch': switch,
            }
            bond_attrs = {'nics': [nic1, nic2], 'switch': switch}
            remove_attrs = {'remove': True}
            default_mtu = {'mtu': DEFAULT_MTU}

            with adapter.setupNetworks(
                {NETWORK_NAME: net_attrs},
                {BOND_NAME: bond_attrs},
                nftestlib.NOCHK,
            ):
                adapter.assertLinkMtu(BOND_NAME, net_attrs)
                adapter.assertLinkMtu(nic1, net_attrs)
                adapter.assertLinkMtu(nic2, net_attrs)

                adapter.setupNetworks(
                    {NETWORK_NAME: remove_attrs},
                    {BOND_NAME: remove_attrs},
                    nftestlib.NOCHK,
                )
                adapter.assertLinkMtu(nic1, default_mtu)
                adapter.assertLinkMtu(nic2, default_mtu)

    @nftestlib.parametrize_bridged
    @nftestlib.parametrize_bonded
    def test_mtu_default_value_of_base_nic_after_all_nets_are_removed(
        self, switch, bridged, bonded
    ):
        if switch == 'legacy' and bonded and not nmstate.is_nmstate_backend():
            pytest.xfail('BZ#1633528')
        with dummy_devices(1) as (nic,):
            NETWORK1_ATTRS = {
                'bridged': bridged,
                'vlan': VLAN1,
                'mtu': MTU_1600,
                'switch': switch,
            }
            NETBASE = {NETWORK1_NAME: NETWORK1_ATTRS}
            default_mtu = {'mtu': DEFAULT_MTU}
            if bonded:
                NETWORK1_ATTRS['bonding'] = BOND_NAME
                BONDBASE = {BOND_NAME: {'nics': [nic], 'switch': switch}}
            else:
                NETWORK1_ATTRS['nic'] = nic
                BONDBASE = {}

            with adapter.setupNetworks(NETBASE, BONDBASE, nftestlib.NOCHK):
                adapter.setupNetworks(
                    {NETWORK1_NAME: {'remove': True}}, {}, nftestlib.NOCHK
                )

                adapter.assertLinkMtu(nic, default_mtu)
                if bonded:
                    adapter.assertLinkMtu(BOND_NAME, default_mtu)

    @nftestlib.parametrize_bridged
    @nftestlib.parametrize_bonded
    def test_base_iface_mtu_is_preserved_when_not_all_nets_on_top_are_deleted(
        self, switch, bridged, bonded
    ):
        if switch == 'legacy' and bonded and not nmstate.is_nmstate_backend():
            pytest.xfail('BZ#1633528')

        common_net_mtu = MTU_1600
        with dummy_devices(1) as (nic,):
            vlaned_network = {
                'bridged': bridged,
                'vlan': VLAN1,
                'mtu': common_net_mtu,
                'switch': switch,
            }
            non_vlaned_network = {
                'bridged': bridged,
                'mtu': common_net_mtu,
                'switch': switch,
            }
            all_networks = {
                NETWORK1_NAME: vlaned_network,
                NETWORK2_NAME: non_vlaned_network,
            }

            mtu_to_keep = {'mtu': common_net_mtu}
            if bonded:
                vlaned_network['bonding'] = BOND_NAME
                non_vlaned_network['bonding'] = BOND_NAME
                bonds = {BOND_NAME: {'nics': [nic], 'switch': switch}}
            else:
                vlaned_network['nic'] = nic
                non_vlaned_network['nic'] = nic
                bonds = {}

            with adapter.setupNetworks(all_networks, bonds, nftestlib.NOCHK):
                adapter.setupNetworks(
                    {NETWORK1_NAME: {'remove': True}}, {}, nftestlib.NOCHK
                )

                adapter.assertLinkMtu(nic, mtu_to_keep)
                if bonded:
                    adapter.assertLinkMtu(BOND_NAME, mtu_to_keep)

    @nftestlib.parametrize_bridged
    @nftestlib.parametrize_bonded
    def test_adding_a_net_with_mtu_lower_than_base_nic_mtu(
        self, switch, bridged, bonded
    ):
        with dummy_devices(1) as (nic,):
            NETWORK1_ATTRS = {
                'bridged': bridged,
                'vlan': VLAN1,
                'mtu': MTU_1000,
                'switch': switch,
            }
            NETNEW = {NETWORK1_NAME: NETWORK1_ATTRS}

            if bonded:
                NETWORK1_ATTRS['bonding'] = BOND_NAME
                BONDBASE = {BOND_NAME: {'nics': [nic], 'switch': switch}}
                link2monitor = BOND_NAME
            else:
                NETWORK1_ATTRS['nic'] = nic
                BONDBASE = {}
                link2monitor = nic

            with adapter.setupNetworks(NETNEW, BONDBASE, nftestlib.NOCHK):
                with nftestlib.monitor_stable_link_state(link2monitor):
                    adapter.assertLinkMtu(nic, NETWORK1_ATTRS)

                    if bonded:
                        vlan1 = BOND_NAME + '.' + str(NETWORK1_ATTRS['vlan'])
                        adapter.assertLinkMtu(BOND_NAME, NETWORK1_ATTRS)
                    else:
                        vlan1 = nic + '.' + str(NETWORK1_ATTRS['vlan'])

                    adapter.assertLinkMtu(vlan1, NETWORK1_ATTRS)

    @nftestlib.parametrize_bridged
    @nftestlib.parametrize_bonded
    @parametrize_vlan
    def test_move_net_from_one_iface_to_another_with_non_default_mtu(
        self, switch, bridged, bonded, vlan
    ):
        if switch == 'legacy' and bonded and not nmstate.is_nmstate_backend():
            pytest.skip('Not supported with ifcfg BZ#1790761')
        with dummy_devices(2) as (nic1, nic2):
            net_attrs = {'bridged': bridged, 'mtu': MTU_2000, 'switch': switch}
            default_mtu = {'mtu': DEFAULT_MTU}
            if bonded:
                net_attrs['bonding'] = BOND_NAME
                BONDBASE = {
                    BOND_NAME: {'nics': [nic1], 'switch': switch},
                    BOND_NAME2: {'nics': [nic2], 'switch': switch},
                }
            else:
                net_attrs['nic'] = nic1
                BONDBASE = {}

            if vlan:
                net_attrs['vlan'] = VLAN1

            with adapter.setupNetworks(
                {NETWORK_NAME: net_attrs}, BONDBASE, nftestlib.NOCHK
            ):
                if bonded:
                    net_attrs['bonding'] = BOND_NAME2
                else:
                    net_attrs['nic'] = nic2
                adapter.setupNetworks(
                    {NETWORK_NAME: net_attrs}, {}, nftestlib.NOCHK
                )
                adapter.assertNetwork(NETWORK_NAME, net_attrs)
                if bonded:
                    adapter.assertLinkMtu(BOND_NAME, default_mtu)
                    adapter.assertLinkMtu(BOND_NAME2, net_attrs)
                adapter.assertLinkMtu(nic1, default_mtu)
                adapter.assertLinkMtu(nic2, net_attrs)

    @nftestlib.parametrize_bridged
    @nftestlib.parametrize_bonded
    @parametrize_vlan
    def test_move_net_between_bond_and_nic_with_non_default_mtu(
        self, switch, bridged, bonded, vlan
    ):
        if switch == 'legacy' and not nmstate.is_nmstate_backend():
            pytest.skip('Not supported with ifcfg BZ#1790761')
        with dummy_devices(2) as (nic1, nic2):
            net_attrs = {'bridged': bridged, 'mtu': MTU_2000, 'switch': switch}
            BONDBASE = {BOND_NAME: {'nics': [nic1], 'switch': switch}}
            default_mtu = {'mtu': DEFAULT_MTU}
            if bonded:
                net_attrs['bonding'] = BOND_NAME
            else:
                net_attrs['nic'] = nic2

            if vlan:
                net_attrs['vlan'] = VLAN1

            with adapter.setupNetworks(
                {NETWORK_NAME: net_attrs}, BONDBASE, nftestlib.NOCHK
            ):
                if bonded:
                    net_attrs.pop('bonding')
                    net_attrs['nic'] = nic2
                else:
                    net_attrs.pop('nic')
                    net_attrs['bonding'] = BOND_NAME
                adapter.setupNetworks(
                    {NETWORK_NAME: net_attrs}, {}, nftestlib.NOCHK
                )
                adapter.assertNetwork(NETWORK_NAME, net_attrs)
                adapter.assertLinkMtu(
                    BOND_NAME, default_mtu if bonded else net_attrs
                )
                adapter.assertLinkMtu(
                    nic1, default_mtu if bonded else net_attrs
                )
                adapter.assertLinkMtu(
                    nic2, net_attrs if bonded else default_mtu
                )
