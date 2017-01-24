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

import os

from nose.plugins.attrib import attr

from vdsm.network.link import iface as link_iface

from .netfunctestlib import NetFuncTestCase, NOCHK
from .nettestlib import dummy_device
from .nmnettestlib import iface_name, nm_connections, is_networkmanager_running

NETWORK_NAME = 'test-network'
VLAN = 10


class NetworkBasicTemplate(NetFuncTestCase):
    __test__ = False

    def test_add_net_based_on_nic(self):
        with dummy_device() as nic:
            NETCREATE = {NETWORK_NAME: {'nic': nic, 'switch': self.switch}}
            with self.setupNetworks(NETCREATE, {}, NOCHK):
                self.assertNetwork(NETWORK_NAME, NETCREATE[NETWORK_NAME])

    def test_remove_net_based_on_nic(self):
        with dummy_device() as nic:
            NETCREATE = {NETWORK_NAME: {'nic': nic, 'switch': self.switch}}
            NETREMOVE = {NETWORK_NAME: {'remove': True}}
            with self.setupNetworks(NETCREATE, {}, NOCHK):
                self.setupNetworks(NETREMOVE, {}, NOCHK)
                self.assertNoNetwork(NETWORK_NAME)

    def test_add_net_based_on_vlan(self):
        with dummy_device() as nic:
            NETCREATE = {NETWORK_NAME: {'nic': nic, 'vlan': VLAN,
                                        'switch': self.switch}}
            with self.setupNetworks(NETCREATE, {}, NOCHK):
                self.assertNetwork(NETWORK_NAME, NETCREATE[NETWORK_NAME])

    def test_remove_net_based_on_vlan(self):
        with dummy_device() as nic:
            NETCREATE = {NETWORK_NAME: {'nic': nic, 'vlan': VLAN,
                                        'switch': self.switch}}
            NETREMOVE = {NETWORK_NAME: {'remove': True}}
            with self.setupNetworks(NETCREATE, {}, NOCHK):
                self.setupNetworks(NETREMOVE, {}, NOCHK)
                self.assertNoNetwork(NETWORK_NAME)
                self.assertNoVlan(nic, VLAN)


@attr(type='functional', switch='legacy')
class NetworkBasicLegacyTest(NetworkBasicTemplate):
    __test__ = True
    switch = 'legacy'

    NET_CONF_DIR = '/etc/sysconfig/network-scripts/'
    NET_CONF_PREF = NET_CONF_DIR + 'ifcfg-'

    def test_add_net_based_on_device_with_non_standard_ifcfg_file(self):
        if is_networkmanager_running():
            self.skipTest('NetworkManager is running.')

        with dummy_device() as nic:
            NETCREATE = {NETWORK_NAME: {'nic': nic, 'switch': self.switch}}
            NETREMOVE = {NETWORK_NAME: {'remove': True}}
            with self.setupNetworks(NETCREATE, {}, NOCHK):
                self.setupNetworks(NETREMOVE, {}, NOCHK)
                self.assertNoNetwork(NETWORK_NAME)

                nic_ifcfg_file = self.NET_CONF_PREF + nic
                self.assertTrue(os.path.exists(nic_ifcfg_file))
                nic_ifcfg_badname_file = nic_ifcfg_file + 'tail123'
                os.rename(nic_ifcfg_file, nic_ifcfg_badname_file)

                # Up until now, we have set the test setup, now start the test.
                with self.setupNetworks(NETCREATE, {}, NOCHK):
                    self.assertNetwork(NETWORK_NAME, NETCREATE[NETWORK_NAME])
                    self.assertTrue(os.path.exists(nic_ifcfg_file))
                    self.assertFalse(os.path.exists(nic_ifcfg_badname_file))

    def test_add_net_based_on_device_with_multiple_nm_connections(self):
        if not is_networkmanager_running():
            self.skipTest('NetworkManager is not running.')

        IPv4_ADDRESS = '192.0.2.1'
        iface = iface_name()
        NET = {NETWORK_NAME: {'bonding': iface, 'switch': self.switch}}
        with nm_connections(iface, IPv4_ADDRESS, con_count=3):
            with self.setupNetworks(NET, {}, NOCHK):
                self.assertNetwork(NETWORK_NAME, NET[NETWORK_NAME])

            # The bond was acquired, therefore VDSM needs to clean it.
            BONDREMOVE = {iface: {'remove': True}}
            self.setupNetworks({}, BONDREMOVE, NOCHK)

    def test_add_net_based_on_existing_vlan_bond_nm_setup(self):
        if not is_networkmanager_running():
            self.skipTest('NetworkManager is not running.')

        iface = iface_name()
        vlan_id = '101'
        NET = {NETWORK_NAME: {'bonding': iface, 'vlan': int(vlan_id),
                              'switch': self.switch}}
        with nm_connections(iface, ipv4addr=None, vlan=vlan_id, slave_count=2):
            bond_hwaddress = link_iface.mac_address(iface)
            vlan_hwaddress = link_iface.mac_address('.'.join([iface, vlan_id]))
            self.assertEqual(vlan_hwaddress, bond_hwaddress)

            with self.setupNetworks(NET, {}, NOCHK):
                self.assertNetwork(NETWORK_NAME, NET[NETWORK_NAME])

                # Check if the mac has been preserved.
                bridge_hwaddress = link_iface.mac_address(NETWORK_NAME)
                self.assertEqual(vlan_hwaddress, bridge_hwaddress)

        # The bond was acquired, therefore VDSM needs to clean it.
        BONDREMOVE = {iface: {'remove': True}}
        self.setupNetworks({}, BONDREMOVE, NOCHK)


@attr(type='functional', switch='ovs')
class NetworkBasicOvsTest(NetworkBasicTemplate):
    __test__ = True
    switch = 'ovs'
