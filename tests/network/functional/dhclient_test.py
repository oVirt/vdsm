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

from vdsm.network.ipwrapper import linkSet, addrAdd

from .netfunctestlib import NetFuncTestCase, NOCHK
from network.nettestlib import veth_pair, dnsmasq_run, dhclient_run

NETWORK_NAME = 'test-network'
VLAN = 10

IPv4_ADDRESS = '192.0.3.1'
IPv4_PREFIX_LEN = '24'

DHCPv4_RANGE_FROM = '192.0.3.2'
DHCPv4_RANGE_TO = '192.0.3.253'


class NetworkDhcpBasicTemplate(NetFuncTestCase):
    __test__ = False

    def test_add_net_with_dhcpv4_based_on_nic(self):
        self._test_add_net_with_dhcpv4()

    def test_add_net_with_dhcpv4_based_on_bridge(self):
        self._test_add_net_with_dhcpv4(bridged=True)

    def _test_add_net_with_dhcpv4(self, bridged=False):

        with veth_pair() as (server, client):
            addrAdd(server, IPv4_ADDRESS, IPv4_PREFIX_LEN)
            linkSet(server, ['up'])
            with dnsmasq_run(server, DHCPv4_RANGE_FROM, DHCPv4_RANGE_TO):

                netcreate = {NETWORK_NAME: {
                    'bridged': bridged, 'nic': client, 'blockingdhcp': True,
                    'bootproto': 'dhcp', 'switch': self.switch}}

                with self.setupNetworks(netcreate, {}, NOCHK):
                    self.assertNetworkIp(
                        NETWORK_NAME, netcreate[NETWORK_NAME])


@attr(switch='legacy')
class NetworkDhcpBasicLegacyTest(NetworkDhcpBasicTemplate):
    __test__ = True
    switch = 'legacy'


@attr(switch='ovs')
class NetworkDhcpBasicOvsTest(NetworkDhcpBasicTemplate):
    __test__ = True
    switch = 'ovs'


class StopDhclientOnUsedNicsTemplate(NetFuncTestCase):
    __test__ = False

    def test_attach_dhcp_nic_to_ipless_network(self):
        with veth_pair() as (server, client):
            addrAdd(server, IPv4_ADDRESS, IPv4_PREFIX_LEN)
            linkSet(server, ['up'])
            with dnsmasq_run(server, DHCPv4_RANGE_FROM, DHCPv4_RANGE_TO):
                with dhclient_run(client):
                    self.assertDhclient(client, family=4)

                    NETCREATE = {NETWORK_NAME: {
                        'nic': client, 'switch': self.switch}}
                    with self.setupNetworks(NETCREATE, {}, NOCHK):
                        nic_netinfo = self.netinfo.nics[client]
                        self.assertDisabledIPv4(nic_netinfo)
                        net_netinfo = self.netinfo.networks[NETWORK_NAME]
                        self.assertDisabledIPv4(net_netinfo)

    def test_attach_dhcp_nic_to_dhcp_bridged_network(self):
        with veth_pair() as (server, client):
            addrAdd(server, IPv4_ADDRESS, IPv4_PREFIX_LEN)
            linkSet(server, ['up'])
            with dnsmasq_run(server, DHCPv4_RANGE_FROM, DHCPv4_RANGE_TO):
                with dhclient_run(client):
                    self.assertDhclient(client, family=4)

                    NETCREATE = {NETWORK_NAME: {
                        'nic': client, 'bootproto': 'dhcp',
                        'blockingdhcp': True, 'switch': self.switch}}
                    with self.setupNetworks(NETCREATE, {}, NOCHK):
                        nic_netinfo = self.netinfo.nics[client]
                        self.assertDisabledIPv4(nic_netinfo)
                        self.assertNoDhclient(client, family=4)
                        net_netinfo = self.netinfo.networks[NETWORK_NAME]
                        self.assertDHCPv4(net_netinfo)
                        self.assertDhclient(NETWORK_NAME, family=4)


@attr(switch='legacy')
class StopDhclientOnUsedNicsLegacyTest(StopDhclientOnUsedNicsTemplate):
    __test__ = True
    switch = 'legacy'


@attr(switch='ovs')
class StopDhclientOnUsedNicsOvsTest(StopDhclientOnUsedNicsTemplate):
    __test__ = True
    switch = 'ovs'
