#
# Copyright 2016-2019 Red Hat, Inc.
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
from vdsm.network.ipwrapper import addrAdd

from . import netfunctestlib as nftestlib
from .netfunctestlib import parametrize_ip_families
from .netfunctestlib import IpFamily
from .netfunctestlib import NetFuncTestAdapter, NOCHK, SetupNetworksError
from network.nettestlib import dummy_device, dummy_devices
from network.nettestlib import preserve_default_route
from network.nettestlib import restore_resolv_conf

NETWORK_NAME = 'test-network'
NETWORK2_NAME = 'test-network2'
BOND_NAME = 'bond1'
VLAN = 10

IPv4_ADDRESS = '192.0.2.1'
IPv4_ADDRESS2 = '192.0.3.1'
IPv4_NETMASK = '255.255.255.0'
IPv4_PREFIX_LEN = '24'
IPv4_GATEWAY = '192.0.2.254'
IPv4_GATEWAY2 = '192.0.3.254'
IPv6_ADDRESS = 'fdb3:84e5:4ff4:55e3::1'
IPv6_PREFIX_LEN = '64'

adapter = None


@pytest.fixture(scope='module', autouse=True)
def create_adapter(target):
    global adapter
    adapter = NetFuncTestAdapter(target)


@pytest.fixture
def preserve_conf():
    with restore_resolv_conf(), preserve_default_route():
        yield


@pytest.mark.nmstate
@nftestlib.parametrize_switch
class TestNetworkStaticIpBasic(object):
    @nftestlib.parametrize_bridged
    @parametrize_ip_families
    def test_add_net_with_ip_based_on_nic(self, switch, bridged, families):
        self._test_add_net_with_ip(families, switch, bridged=bridged)

    @parametrize_ip_families
    def test_add_net_with_ip_based_on_bond(self, switch, families):
        self._test_add_net_with_ip(families, switch, bonded=True)

    @parametrize_ip_families
    def test_add_net_with_ip_based_on_vlan(self, switch, families):
        self._test_add_net_with_ip(families, switch, vlaned=True)

    def _test_add_net_with_ip(
        self, families, switch, bonded=False, vlaned=False, bridged=False
    ):
        IPv6_ADDRESS_AND_PREFIX_LEN = IPv6_ADDRESS + '/' + IPv6_PREFIX_LEN

        with dummy_devices(2) as (nic1, nic2):
            network_attrs = {'bridged': bridged, 'switch': switch}

            if IpFamily.IPv4 in families:
                network_attrs['ipaddr'] = IPv4_ADDRESS
                network_attrs['netmask'] = IPv4_NETMASK
            if IpFamily.IPv6 in families:
                network_attrs['ipv6addr'] = IPv6_ADDRESS_AND_PREFIX_LEN

            if bonded:
                bondcreate = {
                    BOND_NAME: {'nics': [nic1, nic2], 'switch': switch}
                }
                network_attrs['bonding'] = BOND_NAME
            else:
                bondcreate = {}
                network_attrs['nic'] = nic1

            if vlaned:
                network_attrs['vlan'] = VLAN

            netcreate = {NETWORK_NAME: network_attrs}

            with adapter.setupNetworks(netcreate, bondcreate, NOCHK):
                adapter.assertNetworkIp(NETWORK_NAME, netcreate[NETWORK_NAME])

    def test_add_net_with_prefix(self, switch):
        with dummy_device() as nic:
            network_attrs = {
                'nic': nic,
                'ipaddr': IPv4_ADDRESS,
                'prefix': IPv4_PREFIX_LEN,
                'switch': switch,
            }
            netcreate = {NETWORK_NAME: network_attrs}

            with adapter.setupNetworks(netcreate, {}, NOCHK):
                adapter.assertNetworkIp(NETWORK_NAME, netcreate[NETWORK_NAME])

    def test_static_ip_configuration_v4_to_v6_and_back(self, switch):
        with dummy_devices(1) as (nic1,):
            net_ipv4_atts = {
                'nic': nic1,
                'ipaddr': IPv4_ADDRESS,
                'netmask': IPv4_NETMASK,
                'switch': switch,
            }
            net_ipv6_atts = {
                'nic': nic1,
                'ipv6addr': IPv6_ADDRESS + '/' + IPv6_PREFIX_LEN,
                'switch': switch,
            }

            net_ipv4 = {NETWORK_NAME: net_ipv4_atts}
            net_ipv6 = {NETWORK_NAME: net_ipv6_atts}

            with adapter.setupNetworks(net_ipv4, {}, NOCHK):
                adapter.assertNetworkIp(NETWORK_NAME, net_ipv4_atts)
                adapter.setupNetworks(net_ipv6, {}, NOCHK)
                adapter.assertNetworkIp(NETWORK_NAME, net_ipv6_atts)
                adapter.setupNetworks(net_ipv4, {}, NOCHK)
                adapter.assertNetworkIp(NETWORK_NAME, net_ipv4_atts)

    def test_edit_ipv4_address_on_bonded_network(self, switch):
        with dummy_devices(2) as (nic1, nic2):
            net_attrs_ip1 = {
                'bonding': BOND_NAME,
                'bridged': False,
                'ipaddr': '1.1.1.1',
                'prefix': '24',
                'switch': switch,
            }
            net_attrs_ip2 = {
                'bonding': BOND_NAME,
                'bridged': False,
                'ipaddr': '1.1.1.2',
                'prefix': '24',
                'switch': switch,
            }

            bond = {BOND_NAME: {'nics': [nic1, nic2], 'switch': switch}}

            net1 = {NETWORK_NAME: net_attrs_ip1}
            net2 = {NETWORK_NAME: net_attrs_ip2}

            with adapter.setupNetworks(net1, bond, NOCHK):
                adapter.assertNetworkIp(NETWORK_NAME, net_attrs_ip1)
                adapter.setupNetworks(net2, bond, NOCHK)
                adapter.assertNetworkIp(NETWORK_NAME, net_attrs_ip2)

    def test_add_static_ip_to_the_existing_net_with_bond(self, switch):
        with dummy_devices(2) as (nic1, nic2):
            network_attrs1 = {
                'bonding': BOND_NAME,
                'bridged': False,
                'ipaddr': IPv4_ADDRESS,
                'netmask': IPv4_NETMASK,
                'switch': switch,
            }

            network_attrs2 = {
                'bonding': BOND_NAME,
                'bridged': True,
                'vlan': VLAN,
                'switch': switch,
            }

            bond = {BOND_NAME: {'nics': [nic1, nic2], 'switch': switch}}
            netconf = {
                NETWORK_NAME: network_attrs1,
                NETWORK2_NAME: network_attrs2,
            }

            net2 = {NETWORK2_NAME: netconf[NETWORK2_NAME]}

            with adapter.setupNetworks(netconf, bond, NOCHK):
                adapter.assertNetworkIp(NETWORK_NAME, network_attrs1)
                adapter.assertNetworkIp(NETWORK2_NAME, network_attrs2)
                network_attrs2['ipaddr'] = IPv4_ADDRESS2
                network_attrs2['netmask'] = IPv4_NETMASK
                adapter.setupNetworks(net2, bond, NOCHK)
                adapter.assertNetworkIp(NETWORK_NAME, network_attrs1)
                adapter.assertNetworkIp(NETWORK2_NAME, network_attrs2)


@pytest.mark.nmstate
@nftestlib.parametrize_switch
class TestNetworkIPDefaultGateway(object):
    def test_add_net_with_ipv4_default_gateway(self, switch, preserve_conf):
        with dummy_device() as nic:
            network_attrs = {
                'nic': nic,
                'ipaddr': IPv4_ADDRESS,
                'netmask': IPv4_NETMASK,
                'gateway': IPv4_GATEWAY,
                'defaultRoute': True,
                'switch': switch,
            }
            netcreate = {NETWORK_NAME: network_attrs}

            with adapter.setupNetworks(netcreate, {}, NOCHK):
                adapter.assertNetworkIp(NETWORK_NAME, network_attrs)

    def test_add_net_and_move_ipv4_default_gateway(
        self, switch, preserve_conf
    ):
        with dummy_devices(2) as (nic1, nic2):
            net1_attrs = {
                'nic': nic1,
                'ipaddr': IPv4_ADDRESS,
                'netmask': IPv4_NETMASK,
                'gateway': IPv4_GATEWAY,
                'defaultRoute': True,
                'switch': switch,
            }
            net2_attrs = {
                'nic': nic2,
                'ipaddr': IPv4_ADDRESS2,
                'netmask': IPv4_NETMASK,
                'gateway': IPv4_GATEWAY2,
                'defaultRoute': True,
                'switch': switch,
            }
            net1create = {NETWORK_NAME: net1_attrs}
            net2create = {NETWORK2_NAME: net2_attrs}

            with adapter.setupNetworks(net1create, {}, NOCHK):
                with adapter.setupNetworks(net2create, {}, NOCHK):
                    net1_attrs['defaultRoute'] = False
                    adapter.assertNetworkIp(NETWORK_NAME, net1_attrs)
                    adapter.assertNetworkIp(NETWORK2_NAME, net2_attrs)

    def test_add_net_without_default_route(self, switch, preserve_conf):
        with dummy_devices(2) as (nic1, nic2):

            net1_attrs = {
                'nic': nic1,
                'ipaddr': IPv4_ADDRESS,
                'netmask': IPv4_NETMASK,
                'gateway': IPv4_GATEWAY,
                'defaultRoute': True,
                'switch': switch,
            }
            net2_attrs = {
                'nic': nic2,
                'ipaddr': IPv4_ADDRESS2,
                'netmask': IPv4_NETMASK,
                'gateway': IPv4_GATEWAY2,
                'defaultRoute': False,
                'switch': switch,
            }

            net1create = {NETWORK_NAME: net1_attrs}
            net2create = {NETWORK2_NAME: net2_attrs}

            with adapter.setupNetworks(net1create, {}, NOCHK):
                with adapter.setupNetworks(net2create, {}, NOCHK):
                    adapter.assertNetworkIp(NETWORK_NAME, net1_attrs)
                    adapter.assertNetworkIp(NETWORK2_NAME, net2_attrs)
                adapter.assertNetworkIp(NETWORK_NAME, net1_attrs)

    def test_add_net_without_gateway_and_default_route(
        self, switch, preserve_conf
    ):
        with dummy_devices(2) as (nic1, nic2):
            net1_attrs = {
                'nic': nic1,
                'ipaddr': IPv4_ADDRESS,
                'netmask': IPv4_NETMASK,
                'gateway': IPv4_GATEWAY,
                'defaultRoute': True,
                'switch': switch,
            }
            net2_attrs = {
                'nic': nic2,
                'ipaddr': IPv4_ADDRESS2,
                'netmask': IPv4_NETMASK,
                'defaultRoute': False,
                'switch': switch,
            }

            net1create = {NETWORK_NAME: net1_attrs}
            net2create = {NETWORK2_NAME: net2_attrs}

            with adapter.setupNetworks(net1create, {}, NOCHK):
                with adapter.setupNetworks(net2create, {}, NOCHK):
                    adapter.assertNetworkIp(NETWORK_NAME, net1_attrs)
                    adapter.assertNetworkIp(NETWORK2_NAME, net2_attrs)

    def test_create_net_without_default_route(self, switch, preserve_conf):
        with dummy_devices(1) as (nic1,):
            net1_attrs = {
                'nic': nic1,
                'ipaddr': IPv4_ADDRESS,
                'netmask': IPv4_NETMASK,
                'gateway': IPv4_GATEWAY,
                'switch': switch,
            }
            net1create = {NETWORK_NAME: net1_attrs}

            with adapter.setupNetworks(net1create, {}, NOCHK):
                adapter.assertNetworkIp(NETWORK_NAME, net1_attrs)

    def test_remove_net_without_default_route(self, switch, preserve_conf):
        with dummy_devices(2) as (nic1, nic2):
            net1_attrs = {
                'nic': nic1,
                'ipaddr': IPv4_ADDRESS,
                'netmask': IPv4_NETMASK,
                'gateway': IPv4_GATEWAY,
                'defaultRoute': True,
                'switch': switch,
            }
            net2_attrs = {
                'nic': nic2,
                'ipaddr': IPv4_ADDRESS2,
                'netmask': IPv4_NETMASK,
                'gateway': IPv4_GATEWAY2,
                'defaultRoute': False,
                'switch': switch,
            }

            net1create = {NETWORK_NAME: net1_attrs}
            net2create = {NETWORK2_NAME: net2_attrs}

            with adapter.setupNetworks(net1create, {}, NOCHK):
                with adapter.setupNetworks(net2create, {}, NOCHK):
                    adapter.assertNetworkIp(NETWORK2_NAME, net2_attrs)
                adapter.assertNetworkIp(NETWORK_NAME, net1_attrs)

    def test_remove_net_with_default_route_and_gateway(
        self, switch, preserve_conf
    ):
        with dummy_devices(2) as (nic1, nic2):
            net1_attrs = {
                'nic': nic1,
                'ipaddr': IPv4_ADDRESS,
                'netmask': IPv4_NETMASK,
                'gateway': IPv4_GATEWAY,
                'defaultRoute': True,
                'switch': switch,
            }
            net2_attrs = {
                'nic': nic2,
                'ipaddr': IPv4_ADDRESS2,
                'netmask': IPv4_NETMASK,
                'switch': switch,
            }

            net1create = {NETWORK_NAME: net1_attrs}
            net2create = {NETWORK2_NAME: net2_attrs}

            with adapter.setupNetworks(net2create, {}, NOCHK):
                with adapter.setupNetworks(net1create, {}, NOCHK):
                    adapter.assertNetworkIp(NETWORK_NAME, net1_attrs)
                adapter.assertNetworkIp(NETWORK2_NAME, net2_attrs)


@nftestlib.parametrize_switch
@pytest.mark.nmstate
class TestAcquireNicsWithStaticIP(object):
    def test_attach_nic_with_ip_to_ipless_network(self, switch):
        with dummy_device() as nic:
            addrAdd(nic, IPv4_ADDRESS, IPv4_PREFIX_LEN)
            addrAdd(nic, IPv6_ADDRESS, IPv6_PREFIX_LEN, family=6)

            NETCREATE = {NETWORK_NAME: {'nic': nic, 'switch': switch}}
            with adapter.setupNetworks(NETCREATE, {}, NOCHK):
                nic_netinfo = adapter.netinfo.nics[nic]
                adapter.assertDisabledIPv4(nic_netinfo)
                adapter.assertDisabledIPv6(nic_netinfo)

    def test_attach_nic_with_ip_to_ip_network(self, switch):
        with dummy_device() as nic:
            addrAdd(nic, IPv4_ADDRESS, IPv4_PREFIX_LEN)

            NETCREATE = {
                NETWORK_NAME: {
                    'nic': nic,
                    'ipaddr': IPv4_ADDRESS,
                    'netmask': IPv4_NETMASK,
                    'switch': switch,
                }
            }
            with adapter.setupNetworks(NETCREATE, {}, NOCHK):
                nic_netinfo = adapter.netinfo.nics[nic]
                adapter.assertDisabledIPv4(nic_netinfo)
                adapter.assertNetworkIp(NETWORK_NAME, NETCREATE[NETWORK_NAME])

    def test_attach_nic_with_ip_as_a_slave_to_ipless_network(self, switch):
        with dummy_devices(2) as (nic1, nic2):
            addrAdd(nic1, IPv4_ADDRESS, IPv4_PREFIX_LEN)
            addrAdd(nic1, IPv6_ADDRESS, IPv6_PREFIX_LEN, family=6)

            NETCREATE = {
                NETWORK_NAME: {'bonding': BOND_NAME, 'switch': switch}
            }
            BONDCREATE = {BOND_NAME: {'nics': [nic1, nic2], 'switch': switch}}
            with adapter.setupNetworks(NETCREATE, BONDCREATE, NOCHK):
                nic_netinfo = adapter.netinfo.nics[nic1]
                adapter.assertDisabledIPv4(nic_netinfo)
                adapter.assertDisabledIPv6(nic_netinfo)

    def test_attach_nic_with_ip_as_a_slave_to_ip_network(self, switch):
        with dummy_devices(2) as (nic1, nic2):
            addrAdd(nic1, IPv4_ADDRESS, IPv4_PREFIX_LEN)

            NETCREATE = {
                NETWORK_NAME: {
                    'bonding': BOND_NAME,
                    'ipaddr': IPv4_ADDRESS,
                    'netmask': IPv4_NETMASK,
                    'switch': switch,
                }
            }
            BONDCREATE = {BOND_NAME: {'nics': [nic1, nic2], 'switch': switch}}
            with adapter.setupNetworks(NETCREATE, BONDCREATE, NOCHK):
                nic_netinfo = adapter.netinfo.nics[nic1]
                adapter.assertDisabledIPv4(nic_netinfo)
                adapter.assertNetworkIp(NETWORK_NAME, NETCREATE[NETWORK_NAME])


@pytest.mark.legacy_switch
@pytest.mark.nmstate
class TestIfacesWithMultiplesUsers(object):
    @nftestlib.parametrize_bonded
    def test_remove_ip_from_an_iface_used_by_a_vlan_network(self, bonded):
        with dummy_device() as nic:
            netcreate = {
                NETWORK_NAME: {
                    'bridged': False,
                    'ipaddr': IPv4_ADDRESS,
                    'netmask': IPv4_NETMASK,
                },
                NETWORK2_NAME: {'bridged': False, 'vlan': VLAN},
            }

            bondcreate = {}
            if bonded:
                bondcreate[BOND_NAME] = {'nics': [nic]}
                netcreate[NETWORK_NAME]['bonding'] = BOND_NAME
                netcreate[NETWORK2_NAME]['bonding'] = BOND_NAME
            else:
                netcreate[NETWORK_NAME]['nic'] = nic
                netcreate[NETWORK2_NAME]['nic'] = nic

            with adapter.setupNetworks(netcreate, bondcreate, NOCHK):
                netremove = {NETWORK_NAME: {'remove': True}}
                adapter.setupNetworks(netremove, {}, NOCHK)
                if bonded:
                    adapter.assertDisabledIPv4(
                        adapter.netinfo.bondings[BOND_NAME]
                    )
                adapter.assertDisabledIPv4(adapter.netinfo.nics[nic])


@nftestlib.parametrize_switch
@pytest.mark.nmstate
class TestIPValidation(object):
    def test_add_net_ip_missing_addresses_fails(self, switch):
        with dummy_device() as nic:
            self._test_invalid_ip_config_fails(switch, nic, ipaddr='1.2.3.4')
            self._test_invalid_ip_config_fails(switch, nic, gateway='1.2.3.4')
            self._test_invalid_ip_config_fails(
                switch, nic, netmask='255.255.255.0'
            )

    def test_add_net_out_of_range_addresses_fails(self, switch):
        with dummy_device() as nic:
            self._test_invalid_ip_config_fails(
                switch, nic, ipaddr='1.2.3.256', netmask='255.255.0.0'
            )
            self._test_invalid_ip_config_fails(
                switch, nic, ipaddr='1.2.3.4', netmask='256.255.0.0'
            )
            self._test_invalid_ip_config_fails(
                switch,
                nic,
                ipaddr='1.2.3.4',
                netmask='255.255.0.0',
                gateway='1.2.3.256',
            )

    def test_add_net_bad_format_addresses_fails(self, switch):
        with dummy_device() as nic:
            self._test_invalid_ip_config_fails(
                switch, nic, ipaddr='1.2.3.4.5', netmask='255.255.0.0'
            )
            self._test_invalid_ip_config_fails(
                switch, nic, ipaddr='1.2.3', netmask='255.255.0.0'
            )

    def _test_invalid_ip_config_fails(self, switch, nic, **ip_config):
        ip_config.update(switch=switch, nic=nic)
        with pytest.raises(SetupNetworksError) as err:
            with adapter.setupNetworks({NETWORK_NAME: ip_config}, {}, NOCHK):
                pass
        assert err.value.status == ne.ERR_BAD_ADDR
