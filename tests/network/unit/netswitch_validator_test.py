# Copyright 2016-2020 Red Hat, Inc.
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
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA
#
# Refer to the README and COPYING files for full details of the license
#

from __future__ import absolute_import
from __future__ import division

import pytest

from vdsm.network import errors as ne
from vdsm.network.netswitch import validator

from . import testlib

NET0 = 'net0'
NET1 = 'net1'
NET2 = 'net2'
NET_BROKEN = 'net_broken'

BOND0 = 'bond0'
BOND1 = 'bond1'
BOND2 = 'bond2'

VLAN = '10'

NICS = [f'eth{i}' for i in range(12)]
FAKE_NIC = 'fakenic'
FAKE_BOND = 'fakebond'


@pytest.fixture(scope='function')
def net_info():
    return testlib.NetInfo.create(
        networks={
            NET0: testlib.NetInfo.create_network(
                iface=NET0, southbound=NICS[0], ports=NICS[:1], bridged=True
            ),
            NET1: testlib.NetInfo.create_network(
                iface=NET1, southbound=BOND0, ports=[BOND0], bridged=True
            ),
            NET_BROKEN: testlib.NetInfo.create_network(
                iface=NET_BROKEN,
                southbound=NICS[9],
                ports=NICS[9:10],
                bridged=True,
            ),
        },
        nics=NICS,
        bridges={NET0: testlib.NetInfo.create_bridge(ports=NICS[:1])},
        bondings={
            BOND0: testlib.NetInfo.create_bond(slaves=NICS[1:3]),
            BOND1: testlib.NetInfo.create_bond(salves=NICS[9:11]),
        },
        vlans={VLAN: testlib.NetInfo.create_vlan(iface=NICS[11], vlanid=VLAN)},
    )


class TestValidation(object):
    def test_adding_a_new_single_untagged_net(self, net_info):
        nets = {NET2: {'nic': NICS[0], 'switch': 'ovs'}}
        valid = validator.Validator(nets, {}, net_info)
        valid.validate_net(NET2)

    def test_edit_single_untagged_net_nic(self, net_info):
        nets = {NET1: {'nic': NICS[0], 'switch': 'ovs'}}
        valid = validator.Validator(nets, {}, net_info)
        valid.validate_net(NET1)

    def test_add_network_with_non_existing_nic(self, net_info):
        nets = {NET2: {'nic': FAKE_NIC, 'switch': 'ovs'}}
        with pytest.raises(ne.ConfigNetworkError) as e:
            valid = validator.Validator(nets, {}, net_info)
            valid.validate_net(NET2)
        assert e.value.errCode == ne.ERR_BAD_NIC

    def test_add_network_with_non_existing_bond(self, net_info):
        nets = {NET2: {'bonding': FAKE_BOND, 'switch': 'ovs'}}
        with pytest.raises(ne.ConfigNetworkError) as e:
            valid = validator.Validator(nets, {}, net_info)
            valid.validate_net(NET2)
        assert e.value.errCode == ne.ERR_BAD_BONDING

    def test_add_network_with_to_be_added_bond(self, net_info):
        nets = {NET2: {'bonding': BOND2, 'switch': 'ovs'}}
        bonds = {BOND2: {'nics': NICS[:1], 'switch': 'ovs'}}
        valid = validator.Validator(nets, bonds, net_info)
        valid.validate_net(NET2)

    def test_add_network_with_running_bond(self, net_info):
        nets = {NET2: {'bonding': BOND1, 'switch': 'ovs'}}
        valid = validator.Validator(nets, {}, net_info)
        valid.validate_net(NET2)

    @pytest.mark.parametrize(
        'slaves',
        [(), [NICS[0], FAKE_NIC]],
        ids=['no slaves', 'not existing slave'],
    )
    def test_add_bond_with_slaves_fail(self, slaves, net_info):
        bonds = {BOND2: {'switch': 'ovs'}}
        if slaves:
            bonds[BOND2]['nics'] = slaves

        with pytest.raises(ne.ConfigNetworkError):
            valid = validator.Validator({}, bonds, net_info)
            valid.validate_bond(BOND2)

    @pytest.mark.parametrize(
        'slaves',
        [NICS[:1], [NICS[0], NICS[0]], NICS[:2]],
        ids=['one slave', 'same slave twice', 'two slaves'],
    )
    def test_add_bond_with_slaves_pass(self, slaves, net_info):
        bonds = {BOND2: {'switch': 'ovs', 'nics': slaves}}

        valid = validator.Validator({}, bonds, net_info)
        valid.validate_bond(BOND2)

    def test_remove_bond_not_attached_to_a_network(self, net_info):
        bonds = {BOND1: {'remove': True}}
        valid = validator.Validator({}, bonds, net_info)
        valid.validate_bond(BOND1)

    def test_remove_bond_attached_to_network_that_was_removed(self, net_info):
        bonds = {BOND0: {'remove': True}}
        nets = {NET1: {'remove': True}}
        valid = validator.Validator(nets, bonds, net_info)
        valid.validate_bond(BOND0)

    def test_remove_bond_attached_to_network_that_was_not_removed(
        self, net_info
    ):
        bonds = {BOND0: {'remove': True}}
        with pytest.raises(ne.ConfigNetworkError) as e:
            valid = validator.Validator({}, bonds, net_info)
            valid.validate_bond(BOND0)
        assert e.value.errCode == ne.ERR_USED_BOND

    def test_remove_bond_attached_to_network_that_will_use_nic(self, net_info):
        bonds = {BOND0: {'remove': True}}
        nets = {NET1: {'nic': NICS[0]}}
        valid = validator.Validator(nets, bonds, net_info)
        valid.validate_bond(BOND0)

    def test_remove_bond_reattached_to_another_network(self, net_info):
        bonds = {BOND0: {'remove': True}}
        nets = {NET1: {'nic': NICS[0]}, NET0: {'bonding': BOND0}}
        with pytest.raises(ne.ConfigNetworkError) as e:
            valid = validator.Validator(nets, bonds, net_info)
            valid.validate_bond(BOND0)
        assert e.value.errCode == ne.ERR_USED_BOND

    def test_remove_missing_net_fails(self, net_info):
        nets = {NET2: {'remove': True}}
        with pytest.raises(ne.ConfigNetworkError) as cne:
            valid = validator.Validator(nets, {}, net_info)
            valid.validate_net(NET2)
        assert cne.value.errCode == ne.ERR_BAD_BRIDGE

    def test_remove_broken_net_succeeds(self, net_info):
        nets = {NET_BROKEN: {'remove': True}}
        valid = validator.Validator(nets, {}, net_info)
        valid.validate_net(NET_BROKEN)

    def test_is_bridge_name_valid(self):
        invalid_bridge_name = ('', '-abc', 'abcdefghijklmnop', 'a:b', 'a.b')
        for invalid_name in invalid_bridge_name:
            with pytest.raises(ne.ConfigNetworkError) as cne_context:
                validator.validate_bridge_name(invalid_name)
            assert cne_context.value.errCode == ne.ERR_BAD_BRIDGE

    @pytest.mark.parametrize(
        'vlan_id', ['bad id', 5000], ids=['invalid type', 'invalid range']
    )
    def test_network_with_invalid_vlan_id(self, vlan_id, net_info):
        nets = {NET2: {'vlan': vlan_id, 'bridged': True, 'nic': NICS[0]}}
        with pytest.raises(ne.ConfigNetworkError) as cne_context:
            valid = validator.Validator(nets, {}, net_info)
            valid.validate_net(NET2)
        assert cne_context.value.errCode == ne.ERR_BAD_VLAN

    def test_nic_used_by_new_network_and_current_bond(self, net_info):
        nets_to_add = {'net1': {'nic': 'eth1'}}
        with pytest.raises(ne.ConfigNetworkError) as cne_context:
            valid = validator.Validator(nets_to_add, {}, net_info)
            valid.validate_nic_usage()
        assert cne_context.value.errCode == ne.ERR_USED_NIC

    def test_nic_used_by_current_network_and_new_bond(self, net_info):
        bonds_to_add = {'bond1': {'nics': ['eth0', 'eth3']}}
        with pytest.raises(ne.ConfigNetworkError) as cne_context:
            valid = validator.Validator({}, bonds_to_add, net_info)
            valid.validate_nic_usage()
        assert cne_context.value.errCode == ne.ERR_USED_NIC

    def test_vlanned_nic_used_by_new_bond(self, net_info):
        bonds_to_add = {'bond1': {'nics': ['eth10', 'eth11']}}
        with pytest.raises(ne.ConfigNetworkError) as cne_context:
            valid = validator.Validator({}, bonds_to_add, net_info)
            valid.validate_nic_usage()
        assert cne_context.value.errCode == ne.ERR_USED_NIC

    def test_nic_used_by_new_network_only(self, net_info):
        nets_to_add = {'net2': {'nic': 'eth3'}}

        valid = validator.Validator(nets_to_add, {}, net_info)
        valid.validate_nic_usage()

    def test_nic_used_by_new_bond_only(self, net_info):
        bonds_to_add = {'bond1': {'nics': ['eth3', 'eth4']}}

        valid = validator.Validator({}, bonds_to_add, net_info)
        valid.validate_nic_usage()
