#
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

import pytest

from vdsm.network import errors as ne
from vdsm.network.ipwrapper import addrAdd

from . import netfunctestlib as nftestlib
from .netfunctestlib import NetFuncTestCase, NOCHK, SetupNetworksError
from network.nettestlib import dummy_device, dummy_devices
from network.nettestlib import preserve_default_route
from network.nettestlib import restore_resolv_conf

NETWORK_NAME = 'test-network'
NETWORK2_NAME = 'test-network2'
BOND_NAME = 'bond1'
VLAN = 10

IPv4_ADDRESS = '192.0.2.1'
IPv4_NETMASK = '255.255.255.0'
IPv4_PREFIX_LEN = '24'
IPv4_GATEWAY = '192.0.2.254'
IPv6_ADDRESS = 'fdb3:84e5:4ff4:55e3::1/64'


class IpFamily(object):
    IPv4 = 4
    IPv6 = 6


parametrize_ip_families = pytest.mark.parametrize(
    'families', [(IpFamily.IPv4,),
                 (IpFamily.IPv6,),
                 (IpFamily.IPv4, IpFamily.IPv6)],
    ids=['IPv4', 'IPv6', 'IPv4&6'])


@nftestlib.parametrize_switch
class TestNetworkStaticIpBasic(NetFuncTestCase):

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

    def test_add_net_with_ipv4_default_gateway(self, switch):
        with dummy_device() as nic:
            network_attrs = {'nic': nic,
                             'ipaddr': IPv4_ADDRESS,
                             'netmask': IPv4_NETMASK,
                             'gateway': IPv4_GATEWAY,
                             'defaultRoute': True,
                             'switch': switch}
            netcreate = {NETWORK_NAME: network_attrs}

            with restore_resolv_conf(), preserve_default_route():
                with self.setupNetworks(netcreate, {}, NOCHK):
                    self.assertNetworkIp(NETWORK_NAME, netcreate[NETWORK_NAME])

    def _test_add_net_with_ip(self, families, switch,
                              bonded=False, vlaned=False, bridged=False):
        with dummy_devices(2) as (nic1, nic2):
            network_attrs = {'bridged': bridged, 'switch': switch}

            if IpFamily.IPv4 in families:
                network_attrs['ipaddr'] = IPv4_ADDRESS
                network_attrs['netmask'] = IPv4_NETMASK
            if IpFamily.IPv6 in families:
                network_attrs['ipv6addr'] = IPv6_ADDRESS

            if bonded:
                bondcreate = {
                    BOND_NAME: {'nics': [nic1, nic2], 'switch': switch}}
                network_attrs['bonding'] = BOND_NAME
            else:
                bondcreate = {}
                network_attrs['nic'] = nic1

            if vlaned:
                network_attrs['vlan'] = VLAN

            netcreate = {NETWORK_NAME: network_attrs}

            with self.setupNetworks(netcreate, bondcreate, NOCHK):
                self.assertNetworkIp(NETWORK_NAME, netcreate[NETWORK_NAME])

    def test_add_net_with_prefix(self, switch):
        with dummy_device() as nic:
            network_attrs = {'nic': nic,
                             'ipaddr': IPv4_ADDRESS,
                             'prefix': IPv4_PREFIX_LEN,
                             'switch': switch}
            netcreate = {NETWORK_NAME: network_attrs}

            with self.setupNetworks(netcreate, {}, NOCHK):
                self.assertNetworkIp(NETWORK_NAME, netcreate[NETWORK_NAME])


@nftestlib.parametrize_switch
class TestAcquireNicsWithStaticIP(NetFuncTestCase):

    def test_attach_nic_with_ip_to_ipless_network(self, switch):
        with dummy_device() as nic:
            addrAdd(nic, IPv4_ADDRESS, IPv4_PREFIX_LEN)

            NETCREATE = {NETWORK_NAME: {'nic': nic, 'switch': switch}}
            with self.setupNetworks(NETCREATE, {}, NOCHK):
                nic_netinfo = self.netinfo.nics[nic]
                self.assertDisabledIPv4(nic_netinfo)

    def test_attach_nic_with_ip_to_ip_network(self, switch):
        with dummy_device() as nic:
            addrAdd(nic, IPv4_ADDRESS, IPv4_PREFIX_LEN)

            NETCREATE = {
                NETWORK_NAME: {'nic': nic, 'ipaddr': IPv4_ADDRESS,
                               'netmask': IPv4_NETMASK, 'switch': switch}}
            with self.setupNetworks(NETCREATE, {}, NOCHK):
                nic_netinfo = self.netinfo.nics[nic]
                self.assertDisabledIPv4(nic_netinfo)
                self.assertNetworkIp(NETWORK_NAME, NETCREATE[NETWORK_NAME])

    def test_attach_nic_with_ip_as_a_slave_to_ipless_network(self, switch):
        with dummy_devices(2) as (nic1, nic2):
            addrAdd(nic1, IPv4_ADDRESS, IPv4_PREFIX_LEN)

            NETCREATE = {
                NETWORK_NAME: {'bonding': BOND_NAME, 'switch': switch}}
            BONDCREATE = {
                BOND_NAME: {'nics': [nic1, nic2], 'switch': switch}}
            with self.setupNetworks(NETCREATE, BONDCREATE, NOCHK):
                nic_netinfo = self.netinfo.nics[nic1]
                self.assertDisabledIPv4(nic_netinfo)

    def test_attach_nic_with_ip_as_a_slave_to_ip_network(self, switch):
        with dummy_devices(2) as (nic1, nic2):
            addrAdd(nic1, IPv4_ADDRESS, IPv4_PREFIX_LEN)

            NETCREATE = {
                NETWORK_NAME: {'bonding': BOND_NAME, 'ipaddr': IPv4_ADDRESS,
                               'netmask': IPv4_NETMASK, 'switch': switch}}
            BONDCREATE = {
                BOND_NAME: {'nics': [nic1, nic2], 'switch': switch}}
            with self.setupNetworks(NETCREATE, BONDCREATE, NOCHK):
                nic_netinfo = self.netinfo.nics[nic1]
                self.assertDisabledIPv4(nic_netinfo)
                self.assertNetworkIp(NETWORK_NAME, NETCREATE[NETWORK_NAME])


@pytest.mark.legacy_switch
class TestIfacesWithMultiplesUsers(NetFuncTestCase):

    @nftestlib.parametrize_bonded
    def test_remove_ip_from_an_iface_used_by_a_vlan_network(self, bonded):
        with dummy_device() as nic:
            netcreate = {
                NETWORK_NAME: {
                    'bridged': False,
                    'ipaddr': IPv4_ADDRESS,
                    'netmask': IPv4_NETMASK
                },
                NETWORK2_NAME: {
                    'bridged': False,
                    'vlan': VLAN
                }
            }

            bondcreate = {}
            if bonded:
                bondcreate[BOND_NAME] = {'nics': [nic]}
                netcreate[NETWORK_NAME]['bonding'] = BOND_NAME
                netcreate[NETWORK2_NAME]['bonding'] = BOND_NAME
            else:
                netcreate[NETWORK_NAME]['nic'] = nic
                netcreate[NETWORK2_NAME]['nic'] = nic

            with self.setupNetworks(netcreate, bondcreate, NOCHK):
                netremove = {NETWORK_NAME: {'remove': True}}
                self.setupNetworks(netremove, {}, NOCHK)
                if bonded:
                    self.assertDisabledIPv4(self.netinfo.bondings[BOND_NAME])
                self.assertDisabledIPv4(self.netinfo.nics[nic])


@nftestlib.parametrize_switch
class TestIPValidation(NetFuncTestCase):

    def test_add_net_ip_missing_addresses_fails(self, switch):
        with dummy_device() as nic:
            self._test_invalid_ip_config_fails(switch, nic, ipaddr='1.2.3.4')
            self._test_invalid_ip_config_fails(switch, nic, gateway='1.2.3.4')
            self._test_invalid_ip_config_fails(switch, nic,
                                               netmask='255.255.255.0')

    def test_add_net_out_of_range_addresses_fails(self, switch):
        with dummy_device() as nic:
            self._test_invalid_ip_config_fails(
                switch, nic, ipaddr='1.2.3.256', netmask='255.255.0.0')
            self._test_invalid_ip_config_fails(
                switch, nic, ipaddr='1.2.3.4', netmask='256.255.0.0')
            self._test_invalid_ip_config_fails(switch,
                                               nic,
                                               ipaddr='1.2.3.4',
                                               netmask='255.255.0.0',
                                               gateway='1.2.3.256')

    def test_add_net_bad_format_addresses_fails(self, switch):
        with dummy_device() as nic:
            self._test_invalid_ip_config_fails(
                switch, nic, ipaddr='1.2.3.4.5', netmask='255.255.0.0')
            self._test_invalid_ip_config_fails(
                switch, nic, ipaddr='1.2.3', netmask='255.255.0.0')

    def _test_invalid_ip_config_fails(self, switch, nic, **ip_config):
        ip_config.update(switch=switch, nic=nic)
        with pytest.raises(SetupNetworksError) as err:
            with self.setupNetworks({NETWORK_NAME: ip_config}, {}, NOCHK):
                pass
        assert err.value.status == ne.ERR_BAD_ADDR
