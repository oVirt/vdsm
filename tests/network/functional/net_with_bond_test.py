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

from contextlib import contextmanager

import six

from nose.plugins.attrib import attr

from vdsm.network import errors as ne
from vdsm.network.configurators.ifcfg import ifup, ifdown

from .netfunctestlib import NetFuncTestCase, SetupNetworksError, NOCHK
from network.nettestlib import dummy_device, dummy_devices

NETWORK1_NAME = 'test-network1'
NETWORK2_NAME = 'test-network2'
BOND_NAME = 'bond1'
VLAN1 = 10
VLAN2 = 20


class NetworkWithBondTemplate(NetFuncTestCase):
    __test__ = False

    @contextmanager
    def _test_detach_used_bond_from_bridge(self):
        with dummy_device() as nic:
            NETCREATE = {
                NETWORK1_NAME: {'bonding': BOND_NAME, 'switch': self.switch},
                NETWORK2_NAME: {'bonding': BOND_NAME, 'vlan': VLAN2,
                                'switch': self.switch}}
            BONDCREATE = {BOND_NAME: {'nics': [nic], 'switch': self.switch}}
            with self.setupNetworks(NETCREATE, BONDCREATE, NOCHK):
                NETEDIT = {
                    NETWORK1_NAME: {'bonding': BOND_NAME, 'vlan': VLAN1,
                                    'switch': self.switch}}
                self.setupNetworks(NETEDIT, {}, NOCHK)

                yield

                self.assertBond(BOND_NAME, BONDCREATE[BOND_NAME])

    def test_add_the_same_nic_to_net_and_bond_in_one_step(self):
        with dummy_device() as nic:
            NETCREATE = {NETWORK1_NAME: {'nic': nic, 'switch': self.switch}}
            BONDCREATE = {BOND_NAME: {'nics': [nic], 'switch': self.switch}}

            with self.assertRaises(SetupNetworksError) as e:
                self.setupNetworks(NETCREATE, BONDCREATE, NOCHK)
            self.assertEqual(e.exception.status, ne.ERR_USED_NIC)

    def test_add_bond_with_nic_that_is_already_used_by_network(self):
        with dummy_device() as nic:
            NETCREATE = {NETWORK1_NAME: {'nic': nic, 'switch': self.switch}}
            BONDCREATE = {BOND_NAME: {'nics': [nic], 'switch': self.switch}}

            with self.setupNetworks(NETCREATE, {}, NOCHK):
                with self.assertRaises(SetupNetworksError) as e:
                    self.setupNetworks({}, BONDCREATE, NOCHK)
                self.assertEqual(e.exception.status, ne.ERR_USED_NIC)

    def test_add_network_with_nic_that_is_already_used_by_bond(self):
        with dummy_device() as nic:
            NETCREATE = {NETWORK1_NAME: {'nic': nic, 'switch': self.switch}}
            BONDCREATE = {BOND_NAME: {'nics': [nic], 'switch': self.switch}}

            with self.setupNetworks({}, BONDCREATE, NOCHK):
                with self.assertRaises(SetupNetworksError) as e:
                    self.setupNetworks(NETCREATE, {}, NOCHK)
                self.assertEqual(e.exception.status, ne.ERR_USED_NIC)

    def test_add_bridged_network_with_multiple_vlans_over_a_bond(self):
        self._test_add_network_with_multiple_vlans_over_a_bond()

    def test_add_bridgeless_network_with_multiple_vlans_over_a_bond(self):
        self._test_add_network_with_multiple_vlans_over_a_bond(bridged=False)

    def test_add_net_with_invalid_bond_name_fails(self):
        INVALID_BOND_NAMES = ('bond',
                              'bonda',
                              'bond0a',
                              'jamesbond007')

        for bond_name in INVALID_BOND_NAMES:
            NETCREATE = {NETWORK1_NAME: {'bonding': bond_name,
                                         'switch': self.switch}}
            with self.assertRaises(SetupNetworksError) as cm:
                with self.setupNetworks(NETCREATE, {}, NOCHK):
                    pass
            self.assertEqual(cm.exception.status, ne.ERR_BAD_BONDING)

    def _test_add_network_with_multiple_vlans_over_a_bond(self, bridged=True):
        with dummy_devices(2) as nics:
            netsetup = {}
            VLAN_COUNT = 3
            for tag in range(VLAN_COUNT):
                net_name = '{}{}'.format(NETWORK1_NAME, tag)
                netsetup[net_name] = {'vlan': tag,
                                      'bonding': BOND_NAME,
                                      'bridged': bridged,
                                      'switch': self.switch}
            BONDCREATE = {BOND_NAME: {'nics': nics, 'switch': self.switch}}

            with self.setupNetworks(netsetup, BONDCREATE, NOCHK):
                for netname, netattrs in six.iteritems(netsetup):
                    self.assertNetwork(netname, netattrs)


@attr(switch='legacy')
class NetworkWithBondLegacyTest(NetworkWithBondTemplate):
    __test__ = True
    switch = 'legacy'

    def test_detach_used_bond_from_bridge(self):
        with self._test_detach_used_bond_from_bridge():
            ifdown(BOND_NAME)
            ifup(BOND_NAME)
            # netinfo must be updated explicitly after non-API changes
            self.update_netinfo()


@attr(switch='ovs')
class NetworkWithBondOvsTest(NetworkWithBondTemplate):
    __test__ = True
    switch = 'ovs'

    def test_detach_used_bond_from_bridge(self):
        with self._test_detach_used_bond_from_bridge():
            pass
