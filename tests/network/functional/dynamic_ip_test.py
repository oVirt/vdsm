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

from vdsm.network.ipwrapper import linkSet, addrAdd

from . import netfunctestlib as nftestlib
from .netfunctestlib import NetFuncTestCase, NOCHK
from network.nettestlib import veth_pair, dnsmasq_run, dhclient_run

NETWORK_NAME = 'test-network'
VLAN = 10

IPv4_ADDRESS = '192.0.3.1'
IPv4_PREFIX_LEN = '24'
IPv6_ADDRESS = 'fdb3:84e5:4ff4:55e3::1'
IPv6_CIDR = '64'

DHCPv4_RANGE_FROM = '192.0.3.2'
DHCPv4_RANGE_TO = '192.0.3.253'
DHCPv4_GATEWAY = IPv4_ADDRESS
DHCPv6_RANGE_FROM = 'fdb3:84e5:4ff4:55e3::a'
DHCPv6_RANGE_TO = 'fdb3:84e5:4ff4:55e3::64'


class IpFamily(object):
    IPv4 = 4
    IPv6 = 6


parametrize_ip_families = pytest.mark.parametrize(
    'families', [(IpFamily.IPv4,),
                 (IpFamily.IPv6,),
                 (IpFamily.IPv4, IpFamily.IPv6)],
    ids=['IPv4', 'IPv6', 'IPv4&6'])


@nftestlib.parametrize_switch
@parametrize_ip_families
@nftestlib.parametrize_bridged
class TestNetworkDhcpBasic(NetFuncTestCase):

    def test_add_net_with_dhcp(self, switch, families, bridged):

        if switch == 'ovs' and IpFamily.IPv6 in families:
            pytest.xfail('DHCPv6 is not supported with OVS yet.')

        with veth_pair() as (server, client):
            addrAdd(server, IPv4_ADDRESS, IPv4_PREFIX_LEN)
            addrAdd(server, IPv6_ADDRESS, IPv6_CIDR, IpFamily.IPv6)
            linkSet(server, ['up'])
            with dnsmasq_run(server, DHCPv4_RANGE_FROM, DHCPv4_RANGE_TO,
                             DHCPv6_RANGE_FROM, DHCPv6_RANGE_TO,
                             router=DHCPv4_GATEWAY):

                network_attrs = {'bridged': bridged,
                                 'nic': client,
                                 'blockingdhcp': True,
                                 'switch': switch}

                if IpFamily.IPv4 in families:
                    network_attrs['bootproto'] = 'dhcp'
                if IpFamily.IPv6 in families:
                    network_attrs['dhcpv6'] = True

                netcreate = {NETWORK_NAME: network_attrs}

                with self.setupNetworks(netcreate, {}, NOCHK):
                    self.assertNetworkIp(
                        NETWORK_NAME, netcreate[NETWORK_NAME])


@nftestlib.parametrize_switch
class TestStopDhclientOnUsedNics(NetFuncTestCase):

    def test_attach_dhcp_nic_to_ipless_network(self, switch):
        with veth_pair() as (server, client):
            addrAdd(server, IPv4_ADDRESS, IPv4_PREFIX_LEN)
            linkSet(server, ['up'])
            with dnsmasq_run(server, DHCPv4_RANGE_FROM, DHCPv4_RANGE_TO,
                             router=DHCPv4_GATEWAY):
                with dhclient_run(client):
                    self.assertDhclient(client, family=4)

                    NETCREATE = {NETWORK_NAME: {
                        'nic': client, 'switch': switch}}
                    with self.setupNetworks(NETCREATE, {}, NOCHK):
                        nic_netinfo = self.netinfo.nics[client]
                        self.assertDisabledIPv4(nic_netinfo)
                        net_netinfo = self.netinfo.networks[NETWORK_NAME]
                        self.assertDisabledIPv4(net_netinfo)

    def test_attach_dhcp_nic_to_dhcp_bridged_network(self, switch):
        with veth_pair() as (server, client):
            addrAdd(server, IPv4_ADDRESS, IPv4_PREFIX_LEN)
            linkSet(server, ['up'])
            with dnsmasq_run(server, DHCPv4_RANGE_FROM, DHCPv4_RANGE_TO,
                             router=DHCPv4_GATEWAY):
                with dhclient_run(client):
                    self.assertDhclient(client, family=4)

                    NETCREATE = {NETWORK_NAME: {
                        'nic': client, 'bootproto': 'dhcp',
                        'blockingdhcp': True, 'switch': switch}}
                    with self.setupNetworks(NETCREATE, {}, NOCHK):
                        nic_netinfo = self.netinfo.nics[client]
                        self.assertDisabledIPv4(nic_netinfo)
                        self.assertNoDhclient(client, family=4)
                        net_netinfo = self.netinfo.networks[NETWORK_NAME]
                        self.assertDHCPv4(net_netinfo)
                        self.assertDhclient(NETWORK_NAME, family=4)
