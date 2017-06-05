#
# Copyright 2016 Red Hat, Inc.
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

from nose.plugins.attrib import attr

from vdsm.network.ipwrapper import addrAdd

from .netfunctestlib import NetFuncTestCase, NOCHK
from .nettestlib import dummy_device, dummy_devices

NETWORK_NAME = 'test-network'
NETWORK2_NAME = 'test-network2'
BOND_NAME = 'bond1'
VLAN = 10

IPv4_ADDRESS = '192.0.2.1'
IPv4_NETMASK = '255.255.255.0'
IPv4_PREFIX_LEN = '24'
IPv4_GATEWAY = '192.0.2.254'
IPv6_ADDRESS = 'fdb3:84e5:4ff4:55e3::1/64'

IPv4 = [4]
IPv6 = [6]
IPv4IPv6 = [4, 6]


class NetworkStaticIpBasicTemplate(NetFuncTestCase):
    __test__ = False

    def test_add_net_with_ipv4_based_on_nic(self):
        self._test_add_net_with_ip(IPv4)

    def test_add_net_with_ipv6_based_on_nic(self):
        self._test_add_net_with_ip(IPv6)

    def test_add_net_with_ipv4_ipv6_based_on_nic(self):
        self._test_add_net_with_ip(IPv4IPv6)

    def test_add_net_with_ipv4_based_on_bond(self):
        self._test_add_net_with_ip(IPv4, bonded=True)

    def test_add_net_with_ipv6_based_on_bond(self):
        self._test_add_net_with_ip(IPv6, bonded=True)

    def test_add_net_with_ipv4_ipv6_based_on_bond(self):
        self._test_add_net_with_ip(IPv4IPv6, bonded=True)

    def test_add_net_with_ipv4_based_on_vlan(self):
        self._test_add_net_with_ip(IPv4, vlaned=True)

    def test_add_net_with_ipv6_based_on_vlan(self):
        self._test_add_net_with_ip(IPv6, vlaned=True)

    def test_add_net_with_ipv4_ipv6_based_on_vlan(self):
        self._test_add_net_with_ip(IPv4IPv6, vlaned=True)

    def test_add_net_with_ipv4_based_on_bridge(self):
        self._test_add_net_with_ip(IPv4, bridged=True)

    def test_add_net_with_ipv6_based_on_bridge(self):
        self._test_add_net_with_ip(IPv6, bridged=True)

    def test_add_net_with_ipv4_ipv6_based_on_bridge(self):
        self._test_add_net_with_ip(IPv4IPv6, bridged=True)

    def test_add_net_with_ipv4_default_gateway(self):
        with dummy_device() as nic:
            network_attrs = {'nic': nic,
                             'ipaddr': IPv4_ADDRESS,
                             'netmask': IPv4_NETMASK,
                             'gateway': IPv4_GATEWAY,
                             'defaultRoute': True,
                             'switch': self.switch}
            netcreate = {NETWORK_NAME: network_attrs}

            with self.setupNetworks(netcreate, {}, NOCHK):
                self.assertNetworkIp(NETWORK_NAME, netcreate[NETWORK_NAME])

    def _test_add_net_with_ip(self, families, bonded=False, vlaned=False,
                              bridged=False):
        with dummy_devices(2) as (nic1, nic2):
            network_attrs = {'bridged': bridged, 'switch': self.switch}

            if 4 in families:
                network_attrs['ipaddr'] = IPv4_ADDRESS
                network_attrs['netmask'] = IPv4_NETMASK
            if 6 in families:
                network_attrs['ipv6addr'] = IPv6_ADDRESS

            if bonded:
                bondcreate = {
                    BOND_NAME: {'nics': [nic1, nic2], 'switch': self.switch}}
                network_attrs['bonding'] = BOND_NAME
            else:
                bondcreate = {}
                network_attrs['nic'] = nic1

            if vlaned:
                network_attrs['vlan'] = VLAN

            netcreate = {NETWORK_NAME: network_attrs}

            with self.setupNetworks(netcreate, bondcreate, NOCHK):
                self.assertNetworkIp(NETWORK_NAME, netcreate[NETWORK_NAME])

    def test_add_net_with_prefix(self):
        with dummy_device() as nic:
            network_attrs = {'nic': nic,
                             'ipaddr': IPv4_ADDRESS,
                             'prefix': IPv4_PREFIX_LEN,
                             'switch': self.switch}
            netcreate = {NETWORK_NAME: network_attrs}

            with self.setupNetworks(netcreate, {}, NOCHK):
                self.assertNetworkIp(NETWORK_NAME, netcreate[NETWORK_NAME])


@attr(type='functional', switch='legacy')
class NetworkStaticIpBasicLegacyTest(NetworkStaticIpBasicTemplate):
    __test__ = True
    switch = 'legacy'


@attr(type='functional', switch='ovs')
class NetworkStaticIpBasicOvsTest(NetworkStaticIpBasicTemplate):
    __test__ = True
    switch = 'ovs'


class AcquireNicsWithStaticIPTemplate(NetFuncTestCase):
    __test__ = False

    def test_attach_nic_with_ip_to_ipless_network(self):
        with dummy_device() as nic:
            addrAdd(nic, IPv4_ADDRESS, IPv4_PREFIX_LEN)

            NETCREATE = {NETWORK_NAME: {'nic': nic, 'switch': self.switch}}
            with self.setupNetworks(NETCREATE, {}, NOCHK):
                nic_netinfo = self.netinfo.nics[nic]
                self.assertDisabledIPv4(nic_netinfo)

    def test_attach_nic_with_ip_to_ip_network(self):
        with dummy_device() as nic:
            addrAdd(nic, IPv4_ADDRESS, IPv4_PREFIX_LEN)

            NETCREATE = {
                NETWORK_NAME: {'nic': nic, 'ipaddr': IPv4_ADDRESS,
                               'netmask': IPv4_NETMASK, 'switch': self.switch}}
            with self.setupNetworks(NETCREATE, {}, NOCHK):
                nic_netinfo = self.netinfo.nics[nic]
                self.assertDisabledIPv4(nic_netinfo)
                self.assertNetworkIp(NETWORK_NAME, NETCREATE[NETWORK_NAME])

    def test_attach_nic_with_ip_as_a_slave_to_ipless_network(self):
        with dummy_devices(2) as (nic1, nic2):
            addrAdd(nic1, IPv4_ADDRESS, IPv4_PREFIX_LEN)

            NETCREATE = {
                NETWORK_NAME: {'bonding': BOND_NAME, 'switch': self.switch}}
            BONDCREATE = {
                BOND_NAME: {'nics': [nic1, nic2], 'switch': self.switch}}
            with self.setupNetworks(NETCREATE, BONDCREATE, NOCHK):
                nic_netinfo = self.netinfo.nics[nic1]
                self.assertDisabledIPv4(nic_netinfo)

    def test_attach_nic_with_ip_as_a_slave_to_ip_network(self):
        with dummy_devices(2) as (nic1, nic2):
            addrAdd(nic1, IPv4_ADDRESS, IPv4_PREFIX_LEN)

            NETCREATE = {
                NETWORK_NAME: {'bonding': BOND_NAME, 'ipaddr': IPv4_ADDRESS,
                               'netmask': IPv4_NETMASK, 'switch': self.switch}}
            BONDCREATE = {
                BOND_NAME: {'nics': [nic1, nic2], 'switch': self.switch}}
            with self.setupNetworks(NETCREATE, BONDCREATE, NOCHK):
                nic_netinfo = self.netinfo.nics[nic1]
                self.assertDisabledIPv4(nic_netinfo)
                self.assertNetworkIp(NETWORK_NAME, NETCREATE[NETWORK_NAME])


@attr(type='functional', switch='legacy')
class AcquireNicsWithStaticIPLegacyTest(AcquireNicsWithStaticIPTemplate):
    __test__ = True
    switch = 'legacy'


@attr(type='functional', switch='ovs')
class AcquireNicsWithStaticIPOvsTest(AcquireNicsWithStaticIPTemplate):
    __test__ = True
    switch = 'ovs'


class IfacesWithMultiplesUsersTemplate(NetFuncTestCase):
    __test__ = False

    def test_remove_network_from_a_nic_used_by_a_vlan_network(self):
        with dummy_device() as nic:
            netcreate = {
                NETWORK_NAME: {
                    'bridged': False,
                    'nic': nic,
                    'ipaddr': IPv4_ADDRESS,
                    'netmask': IPv4_NETMASK
                },
                NETWORK2_NAME: {
                    'bridged': False,
                    'nic': nic,
                    'vlan': VLAN
                }
            }

            with self.setupNetworks(netcreate, {}, NOCHK):
                netremove = {NETWORK_NAME: {'remove': True}}
                self.setupNetworks(netremove, {}, NOCHK)
                self.assertDisabledIPv4(self.netinfo.nics[nic])


@attr(type='functional', switch='legacy')
class IfacesWithMultiplesUsersLegacyTest(IfacesWithMultiplesUsersTemplate):
    __test__ = True
    switch = 'legacy'


@attr(type='functional', switch='ovs')
class IfacesWithMultiplesUsersOvsTest(IfacesWithMultiplesUsersTemplate):
    __test__ = True
    switch = 'ovs'
