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

from vdsm.network import api as net_api
from vdsm.network.initializer import init_unpriviliged_dhclient_monitor_ctx
from vdsm.network.ipwrapper import linkSet, addrAdd

from . import netfunctestlib as nftestlib
from .netfunctestlib import NetFuncTestAdapter, NOCHK
from network.nettestlib import veth_pair
from network.nettestlib import dnsmasq_run
from network.nettestlib import dhcp_client_run
from network.nettestlib import running_on_fedora

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


adapter = None


@pytest.fixture(scope='module', autouse=True)
def create_adapter(target):
    global adapter
    adapter = NetFuncTestAdapter(target)


@pytest.fixture(scope='module', autouse=True)
def dhclient_monitor():
    event_sink = FakeNotifier()
    with init_unpriviliged_dhclient_monitor_ctx(event_sink, net_api):
        yield


class FakeNotifier:
    def notify(self, event_id, params=None):
        pass


class IpFamily(object):
    IPv4 = 4
    IPv6 = 6


parametrize_ip_families = pytest.mark.parametrize(
    'families', [(IpFamily.IPv4,),
                 (IpFamily.IPv6,),
                 (IpFamily.IPv4, IpFamily.IPv6)],
    ids=['IPv4', 'IPv6', 'IPv4&6'])

parametrize_def_route = pytest.mark.parametrize(
    'def_route',
    [True, False],
    ids=['withDefRoute', 'withoutDefRoute']
)


@pytest.mark.nmstate
@nftestlib.parametrize_switch
@parametrize_ip_families
@nftestlib.parametrize_bridged
@parametrize_def_route
class TestNetworkDhcpBasic(object):

    def test_add_net_with_dhcp(self, switch, families, bridged, def_route):
        if switch == 'legacy' and running_on_fedora(29):
            pytest.xfail('Fails on Fedora 29')
        if families == (IpFamily.IPv6,) and def_route:
            pytest.skip('Skipping default route + dynamic with IPv6 '
                        'see https://bugzilla.redhat.com/1467332')

        with veth_pair() as (server, client):
            addrAdd(server, IPv4_ADDRESS, IPv4_PREFIX_LEN)
            addrAdd(server, IPv6_ADDRESS, IPv6_CIDR, IpFamily.IPv6)
            linkSet(server, ['up'])
            linkSet(client, ['up'])
            with dnsmasq_run(server, DHCPv4_RANGE_FROM, DHCPv4_RANGE_TO,
                             DHCPv6_RANGE_FROM, DHCPv6_RANGE_TO,
                             router=DHCPv4_GATEWAY):

                network_attrs = {'bridged': bridged,
                                 'nic': client,
                                 'blockingdhcp': True,
                                 'switch': switch,
                                 'defaultRoute': def_route}

                if IpFamily.IPv4 in families:
                    network_attrs['bootproto'] = 'dhcp'
                if IpFamily.IPv6 in families:
                    network_attrs['dhcpv6'] = True

                netcreate = {NETWORK_NAME: network_attrs}

                with adapter.setupNetworks(netcreate, {}, NOCHK):
                    adapter.assertNetworkIp(
                        NETWORK_NAME, netcreate[NETWORK_NAME])


@pytest.mark.nmstate
@nftestlib.parametrize_switch
class TestStopDhclientOnUsedNics(object):

    def test_attach_dhcp_nic_to_ipless_network(self, switch):
        with veth_pair() as (server, client):
            addrAdd(server, IPv4_ADDRESS, IPv4_PREFIX_LEN)
            addrAdd(server, IPv6_ADDRESS, IPv6_CIDR, IpFamily.IPv6)
            linkSet(server, ['up'])
            with dnsmasq_run(server, DHCPv4_RANGE_FROM, DHCPv4_RANGE_TO,
                             DHCPv6_RANGE_FROM, DHCPv6_RANGE_TO,
                             router=DHCPv4_GATEWAY):
                with dhcp_client_run(client):
                    adapter.assertDhclient(client, family=IpFamily.IPv4)
                    adapter.assertDhclient(client, family=IpFamily.IPv6)

                    NETCREATE = {NETWORK_NAME: {
                        'nic': client, 'switch': switch}}
                    with adapter.setupNetworks(NETCREATE, {}, NOCHK):
                        nic_netinfo = adapter.netinfo.nics[client]
                        adapter.assertDisabledIPv4(nic_netinfo)
                        adapter.assertDisabledIPv6(nic_netinfo)
                        net_netinfo = adapter.netinfo.networks[NETWORK_NAME]
                        adapter.assertDisabledIPv4(net_netinfo)
                        adapter.assertDisabledIPv6(nic_netinfo)

    def test_attach_dhcp_nic_to_dhcpv4_bridged_network(self, switch):
        with veth_pair() as (server, client):
            addrAdd(server, IPv4_ADDRESS, IPv4_PREFIX_LEN)
            linkSet(server, ['up'])
            with dnsmasq_run(server, DHCPv4_RANGE_FROM, DHCPv4_RANGE_TO,
                             router=DHCPv4_GATEWAY):
                with dhcp_client_run(client):
                    adapter.assertDhclient(client, family=IpFamily.IPv4)

                    NETCREATE = {NETWORK_NAME: {
                        'nic': client, 'bootproto': 'dhcp',
                        'blockingdhcp': True, 'switch': switch}}
                    with adapter.setupNetworks(NETCREATE, {}, NOCHK):
                        nic_netinfo = adapter.netinfo.nics[client]
                        adapter.assertDisabledIPv4(nic_netinfo)
                        adapter.assertNoDhclient(client, family=IpFamily.IPv4)
                        net_netinfo = adapter.netinfo.networks[NETWORK_NAME]
                        adapter.assertDHCPv4(net_netinfo)
                        adapter.assertDhclient(NETWORK_NAME,
                                               family=IpFamily.IPv4)

    def test_attach_dhcp_nic_to_dhcpv6_bridged_network(self, switch):
        with veth_pair() as (server, client):
            addrAdd(server, IPv6_ADDRESS, IPv6_CIDR, IpFamily.IPv6)
            linkSet(server, ['up'])
            with dnsmasq_run(server, DHCPv6_RANGE_FROM, DHCPv6_RANGE_TO):
                with dhcp_client_run(client, family=IpFamily.IPv6):
                    adapter.assertDhclient(client, family=IpFamily.IPv6)

                    NETCREATE = {NETWORK_NAME: {
                        'nic': client, 'dhcpv6': True,
                        'blockingdhcp': True, 'switch': switch}}
                    with adapter.setupNetworks(NETCREATE, {}, NOCHK):
                        nic_netinfo = adapter.netinfo.nics[client]
                        adapter.assertDisabledIPv6(nic_netinfo)
                        adapter.assertNoDhclient(client, family=IpFamily.IPv6)
                        net_netinfo = adapter.netinfo.networks[NETWORK_NAME]
                        adapter.assertDHCPv6(net_netinfo)
                        adapter.assertDhclient(NETWORK_NAME,
                                               family=IpFamily.IPv6)
