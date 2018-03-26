#
# Copyright 2016-2018 Red Hat, Inc.
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

import pytest

from . import netfunctestlib as nftestlib
from .netfunctestlib import SetupNetworksError, NetFuncTestAdapter, NOCHK
from network.nettestlib import dummy_devices

NETWORK_NAME = 'test-network'
BOND_NAME = 'bond10'
VLAN = 10

IPv4_ADDRESS = '192.0.2.1'
IPv4_NETMASK = '255.255.255.0'


adapter = NetFuncTestAdapter()


@nftestlib.parametrize_switch
class TestNetworkRollback(object):

    def test_remove_broken_network(self, switch):
        with dummy_devices(2) as (nic1, nic2):
            BROKEN_NETCREATE = {NETWORK_NAME: {
                'bonding': BOND_NAME, 'bridged': True, 'vlan': VLAN,
                'netmask': '300.300.300.300', 'ipaddr': '300.300.300.300',
                'switch': switch}}
            BONDCREATE = {BOND_NAME: {'nics': [nic1, nic2], 'switch': switch}}

            with pytest.raises(SetupNetworksError):
                adapter.setupNetworks(BROKEN_NETCREATE, BONDCREATE, NOCHK)

                adapter.update_netinfo()
                adapter.assertNoNetwork(NETWORK_NAME)
                adapter.assertNoBond(BOND_NAME)

    def test_rollback_to_initial_basic_network(self, switch):
        self._test_rollback_to_initial_network(switch)

    def test_rollback_to_initial_network_with_static_ip(self, switch):
        self._test_rollback_to_initial_network(
            switch, ipaddr=IPv4_ADDRESS, netmask=IPv4_NETMASK)

    def _test_rollback_to_initial_network(self, switch, **kwargs):
        with dummy_devices(2) as (nic1, nic2):
            NETCREATE = {NETWORK_NAME: {
                'nic': nic1, 'bridged': False, 'switch': switch}}
            NETCREATE[NETWORK_NAME].update(kwargs)

            BROKEN_NETCREATE = {NETWORK_NAME: {
                'bonding': BOND_NAME, 'bridged': True, 'vlan': VLAN,
                'netmask': '300.300.300.300', 'ipaddr': '300.300.300.300',
                'switch': switch}}
            BONDCREATE = {BOND_NAME: {'nics': [nic1, nic2], 'switch': switch}}

            with adapter.setupNetworks(NETCREATE, {}, NOCHK):

                with pytest.raises(SetupNetworksError):
                    adapter.setupNetworks(BROKEN_NETCREATE, BONDCREATE,
                                          NOCHK)

                    adapter.update_netinfo()
                    adapter.assertNetwork(NETWORK_NAME,
                                          NETCREATE[NETWORK_NAME])
                    adapter.assertNoBond(BOND_NAME)
