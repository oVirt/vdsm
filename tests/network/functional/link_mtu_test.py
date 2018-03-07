#
# Copyright 2017-2018 Red Hat, Inc.
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

from network.nettestlib import dummy_devices

from . import netfunctestlib as nftestlib


NETWORK_NAME = 'test-network'
NETWORK1_NAME = 'test-network1'
NETWORK2_NAME = 'test-network2'
BOND_NAME = 'bond1'
VLAN1 = 10
VLAN2 = 20


@nftestlib.parametrize_switch
class TestNetworkMtu(nftestlib.NetFuncTestCase):

    @nftestlib.parametrize_bridged
    def test_add_net_with_mtu(self, switch, bridged):
        with dummy_devices(1) as (nic,):
            NETCREATE = {NETWORK_NAME: {'nic': nic,
                                        'bridged': bridged,
                                        'mtu': 2000,
                                        'switch': switch}}
            with self.setupNetworks(NETCREATE, {}, nftestlib.NOCHK):
                self.assertNetwork(NETWORK_NAME, NETCREATE[NETWORK_NAME])
                self.assertLinkMtu(nic, NETCREATE[NETWORK_NAME])

    @nftestlib.parametrize_bridged
    @nftestlib.parametrize_bonded
    def test_removing_a_net_updates_the_mtu(self, switch, bridged, bonded):
        with dummy_devices(1) as (nic,):
            NETWORK1_ATTRS = {'bridged': bridged,
                              'vlan': VLAN1,
                              'mtu': 1600,
                              'switch': switch}
            NETWORK2_ATTRS = {'bridged': bridged,
                              'vlan': VLAN2,
                              'mtu': 2000,
                              'switch': switch}
            NETBASE = {NETWORK1_NAME: NETWORK1_ATTRS,
                       NETWORK2_NAME: NETWORK2_ATTRS}
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

            with self.setupNetworks(NETBASE, BONDBASE, nftestlib.NOCHK):
                with nftestlib.monitor_stable_link_state(link2monitor):
                    self.setupNetworks({NETWORK2_NAME: {'remove': True}},
                                       {},
                                       nftestlib.NOCHK)
                    self.assertNetwork(NETWORK1_NAME, NETWORK1_ATTRS)
                    self.assertLinkMtu(nic, NETWORK1_ATTRS)
                    if bonded:
                        self.assertLinkMtu(BOND_NAME, NETWORK1_ATTRS)

    @nftestlib.parametrize_bridged
    @nftestlib.parametrize_bonded
    def test_adding_a_net_updates_the_mtu(self, switch, bridged, bonded):
        with dummy_devices(1) as (nic,):
            NETWORK1_ATTRS = {'bridged': bridged,
                              'vlan': VLAN1,
                              'mtu': 1600,
                              'switch': switch}
            NETWORK2_ATTRS = {'bridged': bridged,
                              'vlan': VLAN2,
                              'mtu': 2000,
                              'switch': switch}
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

            with self.setupNetworks(NETBASE, BONDBASE, nftestlib.NOCHK):
                with nftestlib.monitor_stable_link_state(link2monitor):
                    with self.setupNetworks(NETNEW, {}, nftestlib.NOCHK):
                        self.assertNetwork(NETWORK2_NAME, NETWORK2_ATTRS)
                        self.assertLinkMtu(nic, NETWORK2_ATTRS)
                        if bonded:
                            self.assertLinkMtu(BOND_NAME, NETWORK2_ATTRS)

    def test_add_slave_to_a_bonded_network_with_non_default_mtu(self, switch):
        with dummy_devices(2) as (nic1, nic2):
            NETBASE = {NETWORK_NAME: {'bonding': BOND_NAME,
                                      'bridged': False,
                                      'mtu': 2000,
                                      'switch': switch}}
            BONDBASE = {BOND_NAME: {'nics': [nic1], 'switch': switch}}

            with self.setupNetworks(NETBASE, BONDBASE, nftestlib.NOCHK):
                BONDBASE[BOND_NAME]['nics'].append(nic2)
                self.setupNetworks({}, BONDBASE, nftestlib.NOCHK)
                self.assertLinkMtu(nic2, NETBASE[NETWORK_NAME])
