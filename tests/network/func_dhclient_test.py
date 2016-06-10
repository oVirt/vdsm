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
from .nettestlib import veth_pair, dnsmasq_run

NETWORK_NAME = 'test-network'
VLAN = 10

IPv4_ADDRESS = '192.0.3.1'
IPv4_PREFIX_LEN = '24'

DHCPv4_RANGE_FROM = '192.0.3.2'
DHCPv4_RANGE_TO = '192.0.3.253'


@attr(type='functional')
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


@attr(type='functional', switch='legacy')
class NetworkDhcpBasicLegacyTest(NetworkDhcpBasicTemplate):
    __test__ = True
    switch = 'legacy'


@attr(type='functional', switch='ovs')
class NetworkDhcpBasicOvsTest(NetworkDhcpBasicTemplate):
    __test__ = True
    switch = 'ovs'
