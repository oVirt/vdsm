# Copyright 2016-2017 Red Hat, Inc.
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

import six

import pytest

from vdsm.network import errors as ne
from vdsm.network.configurators.ifcfg import ifup, ifdown
from vdsm.network.link.bond import Bond
from vdsm.network.link.iface import iface

from . import netfunctestlib as nftestlib
from .netfunctestlib import NetFuncTestCase, SetupNetworksError, NOCHK
from network.nettestlib import dummy_device, dummy_devices

NETWORK1_NAME = 'test-network1'
NETWORK2_NAME = 'test-network2'
BOND_NAME = 'bond1'
VLAN1 = 10
VLAN2 = 20


@nftestlib.parametrize_switch
class TestNetworkWithBond(NetFuncTestCase):

    def test_add_the_same_nic_to_net_and_bond_in_one_step(self, switch):
        with dummy_device() as nic:
            NETCREATE = {NETWORK1_NAME: {'nic': nic, 'switch': switch}}
            BONDCREATE = {BOND_NAME: {'nics': [nic], 'switch': switch}}

            with pytest.raises(SetupNetworksError) as e:
                self.setupNetworks(NETCREATE, BONDCREATE, NOCHK)
            assert e.value.status == ne.ERR_USED_NIC

    def test_add_bond_with_nic_that_is_already_used_by_network(self, switch):
        with dummy_device() as nic:
            NETCREATE = {NETWORK1_NAME: {'nic': nic, 'switch': switch}}
            BONDCREATE = {BOND_NAME: {'nics': [nic], 'switch': switch}}

            with self.setupNetworks(NETCREATE, {}, NOCHK):
                with pytest.raises(SetupNetworksError) as e:
                    self.setupNetworks({}, BONDCREATE, NOCHK)
                assert e.value.status == ne.ERR_USED_NIC

    def test_add_network_with_nic_that_is_already_used_by_bond(self, switch):
        with dummy_device() as nic:
            NETCREATE = {NETWORK1_NAME: {'nic': nic, 'switch': switch}}
            BONDCREATE = {BOND_NAME: {'nics': [nic], 'switch': switch}}

            with self.setupNetworks({}, BONDCREATE, NOCHK):
                with pytest.raises(SetupNetworksError) as e:
                    self.setupNetworks(NETCREATE, {}, NOCHK)
                assert e.value.status == ne.ERR_USED_NIC

    @nftestlib.parametrize_bridged
    def test_given_bonded_net_transfer_one_slave_to_new_net(self,
                                                            switch,
                                                            bridged):
        with dummy_devices(3) as (nic1, nic2, nic3):
            NETBASE = {NETWORK1_NAME: {'bonding': BOND_NAME,
                                       'bridged': bridged,
                                       'switch': switch}}
            BONDBASE = {BOND_NAME: {'nics': [nic1, nic2, nic3],
                                    'switch': switch}}

            with self.setupNetworks(NETBASE, BONDBASE, NOCHK):
                NETNEW = {NETWORK2_NAME: {'nic': nic3,
                                          'bridged': bridged,
                                          'switch': switch}}
                BONDEDIT = {BOND_NAME: {'nics': [nic1, nic2],
                                        'switch': switch}}
                with nftestlib.monitor_stable_link_state(BOND_NAME):
                    self.setupNetworks({}, BONDEDIT, NOCHK)
                with self.setupNetworks(NETNEW, {}, NOCHK):
                    self.assertNetwork(NETWORK1_NAME, NETBASE[NETWORK1_NAME])
                    self.assertNetwork(NETWORK2_NAME, NETNEW[NETWORK2_NAME])
                    self.assertBond(BOND_NAME, BONDEDIT[BOND_NAME])

    @nftestlib.parametrize_bridged
    def test_given_bonded_net_replace_bond_with_a_slave(self, switch, bridged):
        with dummy_devices(2) as (nic1, nic2):
            NETBASE = {NETWORK1_NAME: {'bonding': BOND_NAME,
                                       'bridged': bridged,
                                       'switch': switch}}
            BONDBASE = {BOND_NAME: {'nics': [nic1, nic2], 'switch': switch}}

            with self.setupNetworks(NETBASE, BONDBASE, NOCHK):
                NETBASE[NETWORK1_NAME] = {'nic': nic1,
                                          'bridged': bridged,
                                          'switch': switch}
                BONDBASE[BOND_NAME] = {'remove': True}
                self.setupNetworks(NETBASE, BONDBASE, NOCHK)

                self.assertNetwork(NETWORK1_NAME, NETBASE[NETWORK1_NAME])
                self.assertNoBond(BOND_NAME)

    def test_add_net_with_invalid_bond_name_fails(self, switch):
        INVALID_BOND_NAMES = ('bond',
                              'bonda',
                              'bond0a',
                              'jamesbond007')

        for bond_name in INVALID_BOND_NAMES:
            NETCREATE = {NETWORK1_NAME: {'bonding': bond_name,
                                         'switch': switch}}
            with pytest.raises(SetupNetworksError) as cm:
                with self.setupNetworks(NETCREATE, {}, NOCHK):
                    pass
            assert cm.value.status == ne.ERR_BAD_BONDING

    @nftestlib.parametrize_bridged
    def test_add_net_with_multi_vlans_over_a_bond(self, switch, bridged):
        with dummy_devices(2) as nics:
            netsetup = {}
            VLAN_COUNT = 3
            for tag in range(VLAN_COUNT):
                net_name = '{}{}'.format(NETWORK1_NAME, tag)
                netsetup[net_name] = {'vlan': tag,
                                      'bonding': BOND_NAME,
                                      'bridged': bridged,
                                      'switch': switch}
            BONDCREATE = {BOND_NAME: {'nics': nics, 'switch': switch}}

            with self.setupNetworks(netsetup, BONDCREATE, NOCHK):
                for netname, netattrs in six.iteritems(netsetup):
                    self.assertNetwork(netname, netattrs)


@nftestlib.parametrize_switch
class TestReuseBond(NetFuncTestCase):

    def test_detach_used_bond_from_bridge(self, switch):
        with dummy_device() as nic:
            NETCREATE = {
                NETWORK1_NAME: {'bonding': BOND_NAME, 'switch': switch},
                NETWORK2_NAME: {'bonding': BOND_NAME, 'vlan': VLAN2,
                                'switch': switch}}
            BONDCREATE = {BOND_NAME: {'nics': [nic], 'switch': switch}}

            with self.setupNetworks(NETCREATE, BONDCREATE, NOCHK):
                NETEDIT = {
                    NETWORK1_NAME: {'bonding': BOND_NAME, 'vlan': VLAN1,
                                    'switch': switch}}
                self.setupNetworks(NETEDIT, {}, NOCHK)

                # For the legacy bridge, add an explicit ifdown/up step.
                if switch == 'legacy':
                    ifdown(BOND_NAME)
                    ifup(BOND_NAME)
                    # netinfo must be updated explicitly after non-API changes
                    self.update_netinfo()

                self.assertBond(BOND_NAME, BONDCREATE[BOND_NAME])

    @nftestlib.parametrize_bridged
    def test_add_vlaned_network_on_existing_bond(self, switch, bridged):
        if switch == 'ovs':
            pytest.xfail('Link is not stable when using OVS switch.')
        with dummy_device() as nic:
            NETBASE = {NETWORK1_NAME: {'bonding': BOND_NAME,
                                       'bridged': False,
                                       'switch': switch}}
            BONDBASE = {BOND_NAME: {'nics': [nic], 'switch': switch}}

            with self.setupNetworks(NETBASE, BONDBASE, NOCHK):
                with nftestlib.monitor_stable_link_state(BOND_NAME):
                    NETVLAN = {NETWORK2_NAME: {'bonding': BOND_NAME,
                                               'bridged': bridged,
                                               'vlan': VLAN1,
                                               'switch': switch}}
                    with self.setupNetworks(NETVLAN, {}, NOCHK):
                        self.assertNetwork(NETWORK1_NAME,
                                           NETBASE[NETWORK1_NAME])
                        self.assertNetwork(NETWORK2_NAME,
                                           NETVLAN[NETWORK2_NAME])

    def test_add_net_on_existing_external_bond_preserving_mac(self, switch):
        if switch == 'ovs':
            pytest.xfail('Preserving bond mac is not supported on OVS switch.')
        HWADDRESS = 'ce:0c:46:59:c9:d1'
        with dummy_devices(2) as (nic1, nic2):
            with Bond(BOND_NAME, slaves=(nic1, nic2)) as bond:
                bond.create()
                iface(BOND_NAME).set_address(HWADDRESS)

                NETBASE = {NETWORK1_NAME: {'bonding': BOND_NAME,
                                           'bridged': False,
                                           'switch': switch}}
                with self.setupNetworks(NETBASE, {}, NOCHK):
                    self.assertNetwork(NETWORK1_NAME,
                                       NETBASE[NETWORK1_NAME])
                    self.assertBond(BOND_NAME,
                                    {'nics': [nic1, nic2],
                                     'hwaddr': HWADDRESS,
                                     'switch': switch})
            self.setupNetworks({}, {BOND_NAME: {'remove': True}}, NOCHK)
