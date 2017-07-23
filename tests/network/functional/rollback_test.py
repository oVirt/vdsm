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

from .netfunctestlib import SetupNetworksError, NetFuncTestCase, NOCHK
from network.nettestlib import dummy_devices

NETWORK_NAME = 'test-network'
BOND_NAME = 'bond10'
VLAN = 10

IPv4_ADDRESS = '192.0.2.1'
IPv4_NETMASK = '255.255.255.0'


class NetworkRollbackTemplate(NetFuncTestCase):
    __test__ = False

    def test_remove_broken_network(self):
        with dummy_devices(2) as (nic1, nic2):
            BROKEN_NETCREATE = {NETWORK_NAME: {
                'bonding': BOND_NAME, 'bridged': True, 'vlan': VLAN,
                'netmask': '300.300.300.300', 'ipaddr': '300.300.300.300',
                'switch': self.switch}}
            BONDCREATE = {BOND_NAME: {
                'nics': [nic1, nic2], 'switch': self.switch}}

            with self.assertRaises(SetupNetworksError):
                self.setupNetworks(BROKEN_NETCREATE, BONDCREATE, NOCHK)

            self.update_netinfo()
            self.assertNoNetwork(NETWORK_NAME)
            self.assertNoBond(BOND_NAME)

    def test_rollback_to_initial_basic_network(self):
        self._test_rollback_to_initial_network()

    def test_rollback_to_initial_network_with_static_ip(self):
        self._test_rollback_to_initial_network(
            ipaddr=IPv4_ADDRESS, netmask=IPv4_NETMASK)

    def _test_rollback_to_initial_network(self, **kwargs):
        with dummy_devices(2) as (nic1, nic2):
            NETCREATE = {NETWORK_NAME: {
                'nic': nic1, 'bridged': False, 'switch': self.switch}}
            NETCREATE[NETWORK_NAME].update(kwargs)

            BROKEN_NETCREATE = {NETWORK_NAME: {
                'bonding': BOND_NAME, 'bridged': True, 'vlan': VLAN,
                'netmask': '300.300.300.300', 'ipaddr': '300.300.300.300',
                'switch': self.switch}}
            BONDCREATE = {BOND_NAME: {
                'nics': [nic1, nic2], 'switch': self.switch}}

            with self.setupNetworks(NETCREATE, {}, NOCHK):

                with self.assertRaises(SetupNetworksError):
                    self.setupNetworks(BROKEN_NETCREATE, BONDCREATE, NOCHK)

                self.update_netinfo()
                self.assertNetwork(NETWORK_NAME, NETCREATE[NETWORK_NAME])
                self.assertNoBond(BOND_NAME)


@attr(switch='legacy')
class NetworkRollbackLegacyTest(NetworkRollbackTemplate):
    __test__ = True
    switch = 'legacy'


@attr(switch='ovs')
class NetworkRollbackOvsTest(NetworkRollbackTemplate):
    __test__ = True
    switch = 'ovs'
