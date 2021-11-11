#
# Copyright 2016-2021 Red Hat, Inc.
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

from copy import deepcopy

import pytest

from vdsm.network import api as net_api
from vdsm.network import errors as ne
from vdsm.network.initializer import init_unpriviliged_dhcp_monitor_ctx

from network.nettestlib import dummy_device
from network.nettestlib import Interface
from network.nettestlib import IpFamily
from network.nettestlib import veth_pair
from network.nettestlib import dnsmasq_run
from network.nettestlib import wait_for_ipv4

from .netfunctestlib import SetupNetworksError, NOCHK


NET1_NAME = 'test-network1'
NET2_NAME = 'test-network2'
VLAN = 10
BOND_NAME = 'bond10'

IPv4_ADDRESS = '192.0.3.1'
IPv4_NETMASK = '255.255.255.0'
IPv4_PREFIX_LEN = '24'
IPv6_ADDRESS = 'fdb3:84e5:4ff4:55e3::1'
IPv6_PREFIX_LEN = '64'

DHCPv4_RANGE_FROM = '192.0.3.2'
DHCPv4_RANGE_TO = '192.0.3.253'


pytestmark = pytest.mark.ovs_switch

parametrize_switch_change = pytest.mark.parametrize(
    'sw_src, sw_dst', [('legacy', 'ovs'), ('ovs', 'legacy')]
)


class FakeNotifier:
    def notify(self, event_id, params=None):
        pass


@pytest.fixture(scope='module', autouse=True)
def dhcp_monitor():
    event_sink = FakeNotifier()
    with init_unpriviliged_dhcp_monitor_ctx(event_sink, net_api):
        yield


@pytest.fixture
def nic0():
    with dummy_device() as nic:
        yield nic


@pytest.fixture
def nic1():
    with dummy_device() as nic:
        yield nic


@pytest.fixture
def nic2():
    with dummy_device() as nic:
        yield nic


@pytest.fixture
def dynamic_ipv4_iface():
    with veth_pair() as (server, client):
        with wait_for_ipv4(server, IPv4_ADDRESS, IPv4_PREFIX_LEN):
            Interface.from_existing_dev_name(server).add_ip(
                IPv4_ADDRESS, IPv4_PREFIX_LEN, IpFamily.IPv4
            )
        with dnsmasq_run(
            server, DHCPv4_RANGE_FROM, DHCPv4_RANGE_TO, router=IPv4_ADDRESS
        ):
            yield client


@parametrize_switch_change
class TestBasicSwitchChange(object):
    def test_switch_change_basic_network(self, adapter, sw_src, sw_dst, nic0):
        NETSETUP_SOURCE = {NET1_NAME: {'nic': nic0, 'switch': sw_src}}
        NETSETUP_TARGET = _change_switch_type(NETSETUP_SOURCE, sw_dst)

        with adapter.setupNetworks(NETSETUP_SOURCE, {}, NOCHK):
            adapter.setupNetworks(NETSETUP_TARGET, {}, NOCHK)
            adapter.assertNetwork(NET1_NAME, NETSETUP_TARGET[NET1_NAME])

    def test_switch_change_basic_vlaned_network(
        self, adapter, sw_src, sw_dst, nic0
    ):
        NETSETUP_SOURCE = {
            NET1_NAME: {'nic': nic0, 'vlan': VLAN, 'switch': sw_src}
        }
        NETSETUP_TARGET = _change_switch_type(NETSETUP_SOURCE, sw_dst)

        with adapter.setupNetworks(NETSETUP_SOURCE, {}, NOCHK):
            adapter.setupNetworks(NETSETUP_TARGET, {}, NOCHK)
            adapter.assertNetwork(NET1_NAME, NETSETUP_TARGET[NET1_NAME])

    def test_switch_change_bonded_network(
        self, adapter, sw_src, sw_dst, nic0, nic1
    ):
        NETSETUP_SOURCE = {NET1_NAME: {'bonding': BOND_NAME, 'switch': sw_src}}
        NETSETUP_TARGET = _change_switch_type(NETSETUP_SOURCE, sw_dst)
        BONDSETUP_SOURCE = {
            BOND_NAME: {'nics': [nic0, nic1], 'switch': sw_src}
        }
        BONDSETUP_TARGET = _change_switch_type(BONDSETUP_SOURCE, sw_dst)

        with adapter.setupNetworks(NETSETUP_SOURCE, BONDSETUP_SOURCE, NOCHK):
            adapter.setupNetworks(NETSETUP_TARGET, BONDSETUP_TARGET, NOCHK)
            adapter.assertNetwork(NET1_NAME, NETSETUP_TARGET[NET1_NAME])
            adapter.assertBond(BOND_NAME, BONDSETUP_TARGET[BOND_NAME])


@parametrize_switch_change
class TestIpSwitch(object):
    def test_switch_change_bonded_network_with_static_ip(
        self, adapter, sw_src, sw_dst, nic0, nic1
    ):
        NETSETUP_SOURCE = {
            NET1_NAME: {
                'bonding': BOND_NAME,
                'ipaddr': IPv4_ADDRESS,
                'netmask': IPv4_NETMASK,
                'ipv6addr': IPv6_ADDRESS + '/' + IPv6_PREFIX_LEN,
                'switch': sw_src,
            }
        }
        NETSETUP_TARGET = _change_switch_type(NETSETUP_SOURCE, sw_dst)
        BONDSETUP_SOURCE = {
            BOND_NAME: {'nics': [nic0, nic1], 'switch': sw_src}
        }
        BONDSETUP_TARGET = _change_switch_type(BONDSETUP_SOURCE, sw_dst)

        with adapter.setupNetworks(NETSETUP_SOURCE, BONDSETUP_SOURCE, NOCHK):
            adapter.setupNetworks(NETSETUP_TARGET, BONDSETUP_TARGET, NOCHK)
            adapter.assertNetwork(NET1_NAME, NETSETUP_TARGET[NET1_NAME])
            adapter.assertBond(BOND_NAME, BONDSETUP_TARGET[BOND_NAME])

    def test_switch_change_bonded_network_with_dhcp_client(
        self, adapter, sw_src, sw_dst, dynamic_ipv4_iface, nic0
    ):
        NETSETUP_SOURCE = {
            NET1_NAME: {
                'bonding': BOND_NAME,
                'bootproto': 'dhcp',
                'blockingdhcp': True,
                'switch': sw_src,
            }
        }
        NETSETUP_TARGET = _change_switch_type(NETSETUP_SOURCE, sw_dst)
        BONDSETUP_SOURCE = {
            BOND_NAME: {'nics': [dynamic_ipv4_iface, nic0], 'switch': sw_src}
        }
        BONDSETUP_TARGET = _change_switch_type(BONDSETUP_SOURCE, sw_dst)

        with adapter.setupNetworks(NETSETUP_SOURCE, BONDSETUP_SOURCE, NOCHK):
            adapter.setupNetworks(NETSETUP_TARGET, BONDSETUP_TARGET, NOCHK)
            adapter.assertNetwork(NET1_NAME, NETSETUP_TARGET[NET1_NAME])
            adapter.assertBond(BOND_NAME, BONDSETUP_TARGET[BOND_NAME])


@parametrize_switch_change
class TestSwitchRollback(object):
    def test_rollback_target_configuration_with_invalid_ip(
        self, adapter, sw_src, sw_dst, nic0
    ):
        NETSETUP_SOURCE = {NET1_NAME: {'nic': nic0, 'switch': sw_src}}
        NETSETUP_TARGET = {
            NET1_NAME: {
                'nic': nic0,
                'ipaddr': '300.300.300.300',  # invalid
                'netmask': IPv4_NETMASK,
                'switch': sw_dst,
            }
        }

        with adapter.setupNetworks(NETSETUP_SOURCE, {}, NOCHK):
            with pytest.raises(SetupNetworksError) as e:
                adapter.setupNetworks(NETSETUP_TARGET, {}, NOCHK)
            assert e.value.status == ne.ERR_BAD_ADDR
            adapter.assertNetwork(NET1_NAME, NETSETUP_SOURCE[NET1_NAME])

    def test_rollback_target_bond_configuration_with_invalid_ip(
        self, adapter, sw_src, sw_dst, nic0, nic1, nic2
    ):
        NETSETUP_SOURCE = {NET1_NAME: {'nic': nic0, 'switch': sw_src}}
        BONDSETUP_SOURCE = {
            BOND_NAME: {'nics': [nic1, nic2], 'switch': sw_src}
        }
        NETSETUP_TARGET = {
            NET1_NAME: {
                'nic': nic0,
                'ipaddr': '300.300.300.300',  # invalid
                'netmask': IPv4_NETMASK,
                'switch': sw_dst,
            }
        }
        BONDSETUP_TARGET = {
            BOND_NAME: {'nics': [nic1, nic2], 'switch': sw_dst}
        }

        with adapter.setupNetworks(NETSETUP_SOURCE, BONDSETUP_SOURCE, NOCHK):
            with pytest.raises(SetupNetworksError) as e:
                adapter.setupNetworks(NETSETUP_TARGET, BONDSETUP_TARGET, NOCHK)
            assert e.value.status == ne.ERR_BAD_ADDR
            adapter.assertNetwork(NET1_NAME, NETSETUP_SOURCE[NET1_NAME])
            adapter.assertBond(BOND_NAME, BONDSETUP_SOURCE[BOND_NAME])

    def test_rollback_target_configuration_failed_connectivity_check(
        self, adapter, sw_src, sw_dst, nic0
    ):
        NETSETUP_SOURCE = {
            NET1_NAME: {'nic': nic0, 'switch': sw_src},
            NET2_NAME: {'nic': nic0, 'vlan': VLAN, 'switch': sw_src},
        }
        NETSETUP_TARGET = _change_switch_type(NETSETUP_SOURCE, sw_dst)

        with adapter.setupNetworks(NETSETUP_SOURCE, {}, NOCHK):
            with pytest.raises(SetupNetworksError) as e:
                adapter.setupNetworks(
                    NETSETUP_TARGET,
                    {},
                    {'connectivityCheck': True, 'connectivityTimeout': 0.1},
                )
            assert e.value.status == ne.ERR_LOST_CONNECTION
            adapter.assertNetwork(NET1_NAME, NETSETUP_SOURCE[NET1_NAME])
            adapter.assertNetwork(NET2_NAME, NETSETUP_SOURCE[NET2_NAME])


@parametrize_switch_change
class TestSwitchValidation(object):
    def test_switch_change_with_not_all_existing_networks_specified(
        self, adapter, sw_src, sw_dst, nic0
    ):
        NETSETUP_SOURCE = {
            NET1_NAME: {'nic': nic0, 'switch': sw_src},
            NET2_NAME: {'nic': nic0, 'vlan': VLAN, 'switch': sw_src},
        }
        NETSETUP_TARGET = {NET1_NAME: {'nic': nic0, 'switch': sw_dst}}

        with adapter.setupNetworks(NETSETUP_SOURCE, {}, NOCHK):
            with pytest.raises(SetupNetworksError) as e:
                adapter.setupNetworks(NETSETUP_TARGET, {}, NOCHK)
            assert e.value.status == ne.ERR_BAD_PARAMS
            adapter.assertNetwork(NET1_NAME, NETSETUP_SOURCE[NET1_NAME])
            adapter.assertNetwork(NET2_NAME, NETSETUP_SOURCE[NET2_NAME])

    def test_switch_change_setup_includes_a_network_removal(
        self, adapter, sw_src, sw_dst, nic0
    ):
        NETSETUP_SOURCE = {
            NET1_NAME: {'nic': nic0, 'switch': sw_src},
            NET2_NAME: {'nic': nic0, 'vlan': VLAN, 'switch': sw_src},
        }
        NETSETUP_TARGET = {
            NET1_NAME: {'nic': nic0, 'switch': sw_dst},
            NET2_NAME: {'remove': True},
        }

        with adapter.setupNetworks(NETSETUP_SOURCE, {}, NOCHK):
            with pytest.raises(SetupNetworksError) as e:
                adapter.setupNetworks(NETSETUP_TARGET, {}, NOCHK)
            assert e.value.status == ne.ERR_BAD_PARAMS
            adapter.assertNetwork(NET1_NAME, NETSETUP_SOURCE[NET1_NAME])
            adapter.assertNetwork(NET2_NAME, NETSETUP_SOURCE[NET2_NAME])


def _change_switch_type(requests, target_switch):
    changed_requests = deepcopy(requests)
    for attrs in changed_requests.values():
        attrs['switch'] = target_switch
    return changed_requests
