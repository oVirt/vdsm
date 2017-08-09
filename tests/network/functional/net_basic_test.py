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

import os

import six

import pytest

from vdsm.network import errors as ne
from vdsm.network.link import iface as link_iface

from . import netfunctestlib as nftestlib
from .netfunctestlib import NetFuncTestCase, NOCHK, SetupNetworksError
from network import nmnettestlib
from network.nettestlib import dummy_device, dummy_devices

NETWORK_NAME = 'test-network'
NET_1 = NETWORK_NAME + '1'
NET_2 = NETWORK_NAME + '2'
VLANID = 100


@nftestlib.parametrize_switch
class TestNetworkBasic(NetFuncTestCase):

    def test_add_net_based_on_nic(self, switch):
        with dummy_device() as nic:
            NETCREATE = {NETWORK_NAME: {'nic': nic, 'switch': switch}}
            with self.setupNetworks(NETCREATE, {}, NOCHK):
                self.assertNetwork(NETWORK_NAME, NETCREATE[NETWORK_NAME])

    def test_remove_net_based_on_nic(self, switch):
        with dummy_device() as nic:
            NETCREATE = {NETWORK_NAME: {'nic': nic, 'switch': switch}}
            NETREMOVE = {NETWORK_NAME: {'remove': True}}
            with self.setupNetworks(NETCREATE, {}, NOCHK):
                self.setupNetworks(NETREMOVE, {}, NOCHK)
                self.assertNoNetwork(NETWORK_NAME)

    def test_add_bridged_net_twice(self, switch):
        self._test_add_net_twice(switch, bridged=True)

    def test_add_bridgeless_net_twice(self, switch):
        self._test_add_net_twice(switch, bridged=False)

    def test_add_bridgeless_net_missing_nic_fails(self, switch):
        self._test_add_net_missing_nic_fails(switch, bridged=False)

    def test_add_bridged_net_missing_nic_fails(self, switch):
        self._test_add_net_missing_nic_fails(switch, bridged=True)

    def test_remove_missing_net_fails(self, switch):
        NETREMOVE = {NETWORK_NAME: {'remove': True}}
        with pytest.raises(SetupNetworksError) as cm:
            with self.setupNetworks(NETREMOVE, {}, NOCHK):
                pass
        assert cm.value.status == ne.ERR_BAD_BRIDGE

    def test_add_net_based_on_vlan(self, switch):
        with dummy_device() as nic:
            NETCREATE = {NETWORK_NAME: {'nic': nic, 'vlan': VLANID,
                                        'switch': switch}}
            with self.setupNetworks(NETCREATE, {}, NOCHK):
                self.assertNetwork(NETWORK_NAME, NETCREATE[NETWORK_NAME])

    def test_remove_net_based_on_vlan(self, switch):
        with dummy_device() as nic:
            NETCREATE = {NETWORK_NAME: {'nic': nic, 'vlan': VLANID,
                                        'switch': switch}}
            NETREMOVE = {NETWORK_NAME: {'remove': True}}
            with self.setupNetworks(NETCREATE, {}, NOCHK):
                self.setupNetworks(NETREMOVE, {}, NOCHK)
                self.assertNoNetwork(NETWORK_NAME)
                self.assertNoVlan(nic, VLANID)

    def test_add_bridged_net_with_multiple_vlans_over_a_nic(self, switch):
        self._test_add_net_with_multi_vlans_over_a_nic(switch, bridged=True)

    def test_add_bridgeless_net_with_multiple_vlans_over_a_nic(self, switch):
        self._test_add_net_with_multi_vlans_over_a_nic(switch, bridged=False)

    def test_add_bridged_net_missing_sb_device(self, switch):
        if switch == 'ovs':
            pytest.skip('nicless bridged ovs network is currently broken.')

        NETCREATE = {NETWORK_NAME: {'bridged': True, 'switch': switch}}
        with self.setupNetworks(NETCREATE, {}, NOCHK):
            self.assertNetwork(NETWORK_NAME, NETCREATE[NETWORK_NAME])

    def test_add_bridgeless_net_missing_sb_device_fails(self, switch):
        NETCREATE = {NETWORK_NAME: {'bridged': False, 'switch': switch}}
        with pytest.raises(SetupNetworksError) as err:
            with self.setupNetworks(NETCREATE, {}, NOCHK):
                pass
        assert err.value.status == ne.ERR_BAD_PARAMS

    def test_add_bridged_vlaned_net_missing_sb_device_fails(self, switch):
        NETCREATE = {NETWORK_NAME: {'bridged': True,
                                    'vlan': VLANID,
                                    'switch': switch}}
        with pytest.raises(SetupNetworksError) as err:
            with self.setupNetworks(NETCREATE, {}, NOCHK):
                pass
        assert err.value.status == ne.ERR_BAD_VLAN

    def test_add_bridgeless_vlaned_net_missing_sb_device_fails(self, switch):
        NETCREATE = {NETWORK_NAME: {'bridged': False,
                                    'vlan': VLANID,
                                    'switch': switch}}
        with pytest.raises(SetupNetworksError) as err:
            with self.setupNetworks(NETCREATE, {}, NOCHK):
                pass
        assert err.value.status == ne.ERR_BAD_VLAN

    def test_add_bridged_vlaned_and_non_vlaned_nets_same_nic(self, switch):
        self._test_add_vlaned_and_non_vlaned_nets_same_nic(switch,
                                                           bridged=True)

    def test_add_bridgeless_vlaned_and_non_vlaned_nets_same_nic(self, switch):
        self._test_add_vlaned_and_non_vlaned_nets_same_nic(switch,
                                                           bridged=False)

    def test_add_multiple_bridged_nets_on_the_same_nic_fails(self, switch):
        self._test_add_multiple_nets_fails(switch, bridged=True)

    def test_add_multiple_bridgeless_nets_on_the_same_nic_fails(self, switch):
        self._test_add_multiple_nets_fails(switch, bridged=False)

    def test_add_identical_vlan_id_bridged_nets_same_nic_fails(self, switch):
        self._test_add_multiple_nets_fails(switch,
                                           bridged=True, vlan_id=VLANID)

    def test_add_identical_vlan_id_bridgeless_nets_same_nic_fails(self,
                                                                  switch):
        self._test_add_multiple_nets_fails(switch,
                                           bridged=False, vlan_id=VLANID)

    def test_add_identical_vlan_id_bridged_nets_with_two_nics(self, switch):
        self._test_add_identical_vlan_id_nets_with_two_nics(switch,
                                                            bridged=True)

    def test_add_identical_vlan_id_bridgeless_nets_with_two_nics(self, switch):
        self._test_add_identical_vlan_id_nets_with_two_nics(switch,
                                                            bridged=False)

    def _test_add_net_with_multi_vlans_over_a_nic(self, switch, bridged):
        VLAN_COUNT = 3

        with dummy_device() as nic:
            netsetup = {}
            for tag in range(VLAN_COUNT):
                netname = '{}{}'.format(NETWORK_NAME, tag)
                netsetup[netname] = {'vlan': tag,
                                     'nic': nic,
                                     'switch': switch,
                                     'bridged': bridged}

            with self.setupNetworks(netsetup, {}, NOCHK):
                for netname, netattrs in six.viewitems(netsetup):
                    self.assertNetwork(netname, netattrs)

    def _test_add_vlaned_and_non_vlaned_nets_same_nic(self, switch, bridged):
        with dummy_device() as nic:
            net_1_attrs = self._create_net_attrs(nic, bridged, switch)
            net_2_attrs = self._create_net_attrs(nic, bridged, switch, VLANID)

            self._assert_nets(net_1_attrs, net_2_attrs)

    def _test_add_multiple_nets_fails(self, switch, bridged, vlan_id=None):
        with dummy_device() as nic:
            net_1_attrs = net_2_attrs = self._create_net_attrs(
                nic, bridged, switch, vlan_id)
            with self.setupNetworks({NET_1: net_1_attrs}, {}, NOCHK):
                with pytest.raises(SetupNetworksError) as cm:
                    with self.setupNetworks({NET_2: net_2_attrs}, {}, NOCHK):
                        pass
                assert cm.value.status == ne.ERR_BAD_PARAMS

    def _test_add_identical_vlan_id_nets_with_two_nics(self, switch, bridged):
        with dummy_devices(2) as (nic1, nic2):
            net_1_attrs = self._create_net_attrs(nic1, bridged, switch, VLANID)
            net_2_attrs = self._create_net_attrs(nic2, bridged, switch, VLANID)

            self._assert_nets(net_1_attrs, net_2_attrs)

    def _test_add_net_twice(self, switch, bridged):
        with dummy_device() as nic:
            NETCREATE = {NETWORK_NAME: {'nic': nic,
                                        'bridged': bridged,
                                        'switch': switch}}
            with self.setupNetworks(NETCREATE, {}, NOCHK):
                self.setupNetworks(NETCREATE, {}, NOCHK)
                self.assertNetwork(NETWORK_NAME, NETCREATE[NETWORK_NAME])

    def _test_add_net_missing_nic_fails(self, switch, bridged):
        NETCREATE = {NETWORK_NAME: {'nic': 'missing_nic',
                                    'bridged': bridged,
                                    'switch': switch}}
        with pytest.raises(SetupNetworksError) as cm:
            with self.setupNetworks(NETCREATE, {}, NOCHK):
                pass
        assert cm.value.status == ne.ERR_BAD_NIC

    def test_remove_unbridged_net_with_a_nic_used_by_a_vlan_net(self, switch):
        with dummy_device() as nic:
            netcreate = {
                NET_1: {
                    'bridged': False,
                    'nic': nic,
                },
                NET_2: {
                    'bridged': False,
                    'nic': nic,
                    'vlan': VLANID
                }
            }

            with self.setupNetworks(netcreate, {}, NOCHK):
                netremove = {NET_1: {'remove': True}}
                self.setupNetworks(netremove, {}, NOCHK)
                self.assertNoNetwork(NET_1)
                self.assertNetwork(NET_2, netcreate[NET_2])

    def _assert_nets(self, net_1_attrs, net_2_attrs):
        with self.setupNetworks({NET_1: net_1_attrs}, {}, NOCHK):
            with self.setupNetworks({NET_2: net_2_attrs}, {}, NOCHK):
                self.assertNetwork(NET_1, net_1_attrs)
                self.assertNetwork(NET_2, net_2_attrs)

    def _create_net_attrs(self, nic, bridged, switch, vlan_id=None):
        attrs = {'nic': nic,
                 'bridged': bridged,
                 'switch': switch}
        if vlan_id is not None:
            attrs['vlan'] = vlan_id

        return attrs


@pytest.mark.legacy_switch
class TestNetworkBasicLegacy(NetFuncTestCase):

    NET_CONF_DIR = '/etc/sysconfig/network-scripts/'
    NET_CONF_PREF = NET_CONF_DIR + 'ifcfg-'

    def test_add_net_based_on_device_with_non_standard_ifcfg_file(self):
        if nmnettestlib.is_networkmanager_running():
            pytest.skip('NetworkManager is running.')

        with dummy_device() as nic:
            NETCREATE = {NETWORK_NAME: {'nic': nic, 'switch': 'legacy'}}
            NETREMOVE = {NETWORK_NAME: {'remove': True}}
            with self.setupNetworks(NETCREATE, {}, NOCHK):
                self.setupNetworks(NETREMOVE, {}, NOCHK)
                self.assertNoNetwork(NETWORK_NAME)

                nic_ifcfg_file = self.NET_CONF_PREF + nic
                assert os.path.exists(nic_ifcfg_file)
                nic_ifcfg_badname_file = nic_ifcfg_file + 'tail123'
                os.rename(nic_ifcfg_file, nic_ifcfg_badname_file)

                # Up until now, we have set the test setup, now start the test.
                with self.setupNetworks(NETCREATE, {}, NOCHK):
                    self.assertNetwork(NETWORK_NAME, NETCREATE[NETWORK_NAME])
                    assert os.path.exists(nic_ifcfg_file)
                    assert not os.path.exists(nic_ifcfg_badname_file)


@pytest.mark.legacy_switch
@pytest.mark.skipif(not nmnettestlib.is_networkmanager_running(),
                    reason='NetworkManager is not running')
class TestNetworkManagerLegacy(NetFuncTestCase):
    switch = 'legacy'

    def setup_method(self, m):
        super(TestNetworkManagerLegacy, self).setup_method(m)
        self.iface = nmnettestlib.iface_name()

    def teardown_method(self, m):
        # The bond was acquired, therefore VDSM needs to clean it.
        BONDREMOVE = {self.iface: {'remove': True}}
        self.setupNetworks({}, BONDREMOVE, NOCHK)

    def test_add_net_based_on_device_with_multiple_nm_connections(self):
        IPv4_ADDRESS = '192.0.2.1'
        NET = {NETWORK_NAME: {'bonding': self.iface, 'switch': self.switch}}
        with dummy_devices(1) as nics:
            with nmnettestlib.nm_connections(
                    self.iface, IPv4_ADDRESS, con_count=3, slaves=nics):
                with self.setupNetworks(NET, {}, NOCHK):
                    self.assertNetwork(NETWORK_NAME, NET[NETWORK_NAME])

    def test_add_net_based_on_existing_vlan_bond_nm_setup(self):
        vlan_id = '101'
        NET = {NETWORK_NAME: {'bonding': self.iface, 'vlan': int(vlan_id),
                              'switch': self.switch}}
        with dummy_devices(1) as nics:
            with nmnettestlib.nm_connections(
                    self.iface, ipv4addr=None, vlan=vlan_id, slaves=nics):
                bond_hwaddress = link_iface.mac_address(self.iface)
                vlan_iface = '.'.join([self.iface, vlan_id])
                vlan_hwaddress = link_iface.mac_address(vlan_iface)
                assert vlan_hwaddress == bond_hwaddress

                with self.setupNetworks(NET, {}, NOCHK):
                    self.assertNetwork(NETWORK_NAME, NET[NETWORK_NAME])

                    # Check if the mac has been preserved.
                    bridge_hwaddress = link_iface.mac_address(NETWORK_NAME)
                    assert vlan_hwaddress == bridge_hwaddress
