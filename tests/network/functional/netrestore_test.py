# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

import pytest

from .netfunctestlib import NOCHK
from .netfunctestlib import parametrize_bridged
from .netfunctestlib import parametrize_switch
from network.nettestlib import dummy_device
from network.nettestlib import veth_pair

BOND_NAME = 'bond1'
IPv4_ADDRESS = '192.0.2.1'
IPv4_PREFIX_LEN = '24'
NETWORK_NAME = 'test-network'


@pytest.fixture
def nic0():
    with dummy_device() as nic:
        yield nic


@pytest.fixture
def nic1():
    with dummy_device() as nic:
        yield nic


@pytest.mark.legacy_switch
class TestRestoreLegacyBridge(object):
    def test_restore_bridge_with_custom_opts(self, adapter, nic0):
        CUSTOM_OPTS1 = {
            'bridge_opts': 'multicast_router=0 multicast_snooping=0'
        }
        CUSTOM_OPTS2 = {
            'bridge_opts': 'multicast_router=0 multicast_snooping=1'
        }
        NETCREATE = {
            NETWORK_NAME: {
                'nic': nic0,
                'switch': 'legacy',
                'custom': CUSTOM_OPTS1,
            }
        }
        NETEDIT = {
            NETWORK_NAME: {
                'nic': nic0,
                'switch': 'legacy',
                'custom': CUSTOM_OPTS2,
            }
        }
        with adapter.reset_persistent_config():
            with adapter.setupNetworks(NETCREATE, {}, NOCHK):
                adapter.setSafeNetworkConfig()
                adapter.setupNetworks(NETEDIT, {}, NOCHK)
                adapter.assertBridgeOpts(NETWORK_NAME, NETEDIT[NETWORK_NAME])

                adapter.restore_nets()
                adapter.assertBridgeOpts(NETWORK_NAME, NETCREATE[NETWORK_NAME])


@parametrize_switch
class TestRestore(object):
    @parametrize_bridged
    def test_restore_missing_network_from_config(
        self, adapter, switch, bridged, nic0
    ):
        SETUP_NET = {
            NETWORK_NAME: {'nic': nic0, 'bridged': bridged, 'switch': switch}
        }
        REMOVE_NET = {NETWORK_NAME: {'remove': True}}

        with adapter.reset_persistent_config():
            with adapter.setupNetworks(SETUP_NET, {}, NOCHK):
                adapter.setSafeNetworkConfig()
                adapter.setupNetworks(REMOVE_NET, {}, NOCHK)

                adapter.assertNoNetworkExists(NETWORK_NAME)

                adapter.restore_nets()

                adapter.assertNetworkExists(NETWORK_NAME)

    @parametrize_bridged
    def test_restore_missing_dynamic_ipv4_network(
        self, adapter, switch, bridged
    ):
        with veth_pair() as (server, client):
            SETUP_NET = {
                NETWORK_NAME: {
                    'nic': client,
                    'bridged': bridged,
                    'bootproto': 'dhcp',
                    'switch': switch,
                }
            }
            REMOVE_NET = {NETWORK_NAME: {'remove': True}}

            with adapter.reset_persistent_config():
                with adapter.setupNetworks(SETUP_NET, {}, NOCHK):
                    adapter.setSafeNetworkConfig()
                    adapter.setupNetworks(REMOVE_NET, {}, NOCHK)

                    adapter.assertNoNetworkExists(NETWORK_NAME)

                    adapter.restore_nets()

                    adapter.assertNetworkExists(NETWORK_NAME)

    @parametrize_bridged
    def test_restore_network_static_ip_from_config(
        self, adapter, switch, bridged, nic0
    ):
        NET_WITH_IP_ATTRS = {
            'nic': nic0,
            'bridged': bridged,
            'ipaddr': IPv4_ADDRESS,
            'prefix': IPv4_PREFIX_LEN,
            'switch': switch,
        }
        NET_WITHOUT_IP_ATTRS = {
            'nic': nic0,
            'bridged': bridged,
            'switch': switch,
        }
        NET_WITH_IP = {NETWORK_NAME: NET_WITH_IP_ATTRS}
        NET_WITHOUT_IP = {NETWORK_NAME: NET_WITHOUT_IP_ATTRS}

        with adapter.reset_persistent_config():
            with adapter.setupNetworks(NET_WITH_IP, {}, NOCHK):
                adapter.setSafeNetworkConfig()
                adapter.setupNetworks(NET_WITHOUT_IP, {}, NOCHK)

                adapter.assertNetworkIp(NETWORK_NAME, NET_WITHOUT_IP_ATTRS)

                adapter.restore_nets()

                adapter.assertNetworkIp(NETWORK_NAME, NET_WITH_IP_ATTRS)

    def test_restore_missing_bond(self, adapter, switch, nic0, nic1):
        BONDCREATE = {BOND_NAME: {'nics': [nic0, nic1], 'switch': switch}}
        BONDREMOVE = {BOND_NAME: {'remove': True}}

        with adapter.reset_persistent_config():
            with adapter.setupNetworks({}, BONDCREATE, NOCHK):
                adapter.setSafeNetworkConfig()
                adapter.setupNetworks({}, BONDREMOVE, NOCHK)

                adapter.restore_nets()

                adapter.assertBond(BOND_NAME, BONDCREATE[BOND_NAME])

    @parametrize_bridged
    def test_restore_removes_unpersistent_network(
        self, adapter, switch, bridged, nic0
    ):
        SETUP_NET = {
            NETWORK_NAME: {'nic': nic0, 'bridged': bridged, 'switch': switch}
        }

        with adapter.reset_persistent_config():
            with adapter.setupNetworks(SETUP_NET, {}, NOCHK):

                adapter.restore_nets()

                adapter.assertNoNetworkExists(NETWORK_NAME)
