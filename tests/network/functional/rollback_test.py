#
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

import pytest

from vdsm.network import errors as ne

from . import netfunctestlib as nftestlib
from .netfunctestlib import SetupNetworksError
from .netfunctestlib import NetFuncTestAdapter
from .netfunctestlib import NOCHK
from .netfunctestlib import TIMEOUT_CHK
from network.nettestlib import dummy_device, dummy_devices

NETWORK_NAME = 'test-network'
BOND_NAME = 'bond10'
VLAN = 10

IPv4_ADDRESS = '192.0.2.1'
IPv4_NETMASK = '255.255.255.0'


adapter = None


@pytest.fixture(scope='module', autouse=True)
def create_adapter(target):
    global adapter
    adapter = NetFuncTestAdapter(target)


@nftestlib.parametrize_switch
@pytest.mark.nmstate
class TestNetworkRollback(object):
    def test_remove_broken_network(self, switch):
        with dummy_devices(2) as (nic1, nic2):
            BROKEN_NETCREATE = {
                NETWORK_NAME: {
                    'bonding': BOND_NAME,
                    'bridged': True,
                    'vlan': VLAN,
                    'netmask': '300.300.300.300',
                    'ipaddr': '300.300.300.300',
                    'switch': switch,
                }
            }
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
            switch, ipaddr=IPv4_ADDRESS, netmask=IPv4_NETMASK
        )

    def test_setup_network_fails_on_existing_bond(self, switch):
        with dummy_device() as nic:
            NETCREATE = {
                NETWORK_NAME: {
                    'bridged': True,
                    'bonding': BOND_NAME,
                    'switch': switch,
                }
            }

            BONDCREATE = {BOND_NAME: {'nics': [nic], 'switch': switch}}

            with adapter.setupNetworks({}, BONDCREATE, NOCHK):
                with pytest.raises(SetupNetworksError) as e:
                    adapter.setupNetworks(NETCREATE, {}, TIMEOUT_CHK)
                assert e.value.status == ne.ERR_LOST_CONNECTION

                adapter.assertNoNetwork(NETWORK_NAME)
                adapter.assertBond(BOND_NAME, BONDCREATE[BOND_NAME])

    @nftestlib.parametrize_bonded
    def test_setup_new_network_fails(self, switch, bonded):
        with dummy_device() as nic:
            NETCREATE = {NETWORK_NAME: {'bridged': True, 'switch': switch}}
            if bonded:
                NETCREATE[NETWORK_NAME]['bonding'] = BOND_NAME
                BONDBASE = {BOND_NAME: {'nics': [nic], 'switch': switch}}
            else:
                NETCREATE[NETWORK_NAME]['nic'] = nic
                BONDBASE = {}

            with pytest.raises(SetupNetworksError) as e:
                adapter.setupNetworks(NETCREATE, BONDBASE, TIMEOUT_CHK)
            assert e.value.status == ne.ERR_LOST_CONNECTION

            adapter.assertNoNetwork(NETWORK_NAME)
            if bonded:
                adapter.assertNoBond(BOND_NAME)

    @nftestlib.parametrize_bonded
    def test_edit_network_fails(self, switch, bonded):
        with dummy_device() as nic:
            NETCREATE = {
                NETWORK_NAME: {'bridged': True, 'mtu': 1500, 'switch': switch}
            }
            NETEDIT = {
                NETWORK_NAME: {'bridged': True, 'mtu': 1600, 'switch': switch}
            }

            if bonded:
                NETCREATE[NETWORK_NAME]['bonding'] = BOND_NAME
                NETEDIT[NETWORK_NAME]['bonding'] = BOND_NAME

                BONDBASE = {
                    BOND_NAME: {
                        'nics': [nic],
                        'switch': switch,
                        'options': 'mode=4',
                    }
                }
                BONDEDIT = {
                    BOND_NAME: {
                        'nics': [nic],
                        'switch': switch,
                        'options': 'mode=0',
                    }
                }
            else:
                NETCREATE[NETWORK_NAME]['nic'] = nic
                NETEDIT[NETWORK_NAME]['nic'] = nic

                BONDBASE = {}
                BONDEDIT = {}

            with adapter.setupNetworks(NETCREATE, BONDBASE, NOCHK):
                with pytest.raises(SetupNetworksError) as e:
                    adapter.setupNetworks(NETEDIT, BONDEDIT, TIMEOUT_CHK)
                assert e.value.status == ne.ERR_LOST_CONNECTION

                adapter.assertNetwork(NETWORK_NAME, NETCREATE[NETWORK_NAME])
                if bonded:
                    adapter.assertBond(BOND_NAME, BONDBASE[BOND_NAME])

    def test_setup_two_networks_second_fails(self, switch):
        with dummy_devices(3) as (nic1, nic2, nic3):
            NET1_NAME = NETWORK_NAME + '1'
            NET2_NAME = NETWORK_NAME + '2'

            NETCREATE = {
                NET1_NAME: {
                    'bonding': BOND_NAME,
                    'bridged': True,
                    'switch': switch,
                }
            }
            NETFAIL = {
                NET2_NAME: {
                    'nic': nic3,
                    'bridged': True,
                    'vlan': VLAN,
                    'switch': switch,
                }
            }
            BONDCREATE = {BOND_NAME: {'nics': [nic1, nic2], 'switch': switch}}

            with adapter.setupNetworks(NETCREATE, BONDCREATE, NOCHK):
                with pytest.raises(SetupNetworksError):
                    adapter.setupNetworks(NETFAIL, {}, TIMEOUT_CHK)

                adapter.assertNoNetwork(NET2_NAME)
                adapter.assertNetwork(NET1_NAME, NETCREATE[NET1_NAME])
                adapter.assertBond(BOND_NAME, BONDCREATE[BOND_NAME])

    def _test_rollback_to_initial_network(self, switch, **kwargs):
        with dummy_devices(2) as (nic1, nic2):
            NETCREATE = {
                NETWORK_NAME: {'nic': nic1, 'bridged': False, 'switch': switch}
            }
            NETCREATE[NETWORK_NAME].update(kwargs)

            BROKEN_NETCREATE = {
                NETWORK_NAME: {
                    'bonding': BOND_NAME,
                    'bridged': True,
                    'vlan': VLAN,
                    'netmask': '300.300.300.300',
                    'ipaddr': '300.300.300.300',
                    'switch': switch,
                }
            }
            BONDCREATE = {BOND_NAME: {'nics': [nic1, nic2], 'switch': switch}}

            with adapter.setupNetworks(NETCREATE, {}, NOCHK):

                with pytest.raises(SetupNetworksError):
                    adapter.setupNetworks(BROKEN_NETCREATE, BONDCREATE, NOCHK)

                adapter.update_netinfo()
                adapter.assertNetwork(NETWORK_NAME, NETCREATE[NETWORK_NAME])
                adapter.assertNoBond(BOND_NAME)


@pytest.mark.legacy_switch
@pytest.mark.nmstate
def test_setup_invalid_bridge_opts_fails():
    with dummy_devices(1) as (nic,):
        net_attrs = {
            'nic': nic,
            'switch': 'legacy',
            'custom': {'bridge_opts': 'foo=0'},
        }

        with pytest.raises(SetupNetworksError):
            adapter.setupNetworks({NETWORK_NAME: net_attrs}, {}, NOCHK)

        adapter.update_netinfo()
        adapter.assertNoNetwork(NETWORK_NAME)
