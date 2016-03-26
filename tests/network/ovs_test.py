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
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA
#
# Refer to the README and COPYING files for full details of the license
#
from __future__ import absolute_import

from vdsm.network import errors as ne
from vdsm.network.ovs import switch as ovs_switch
from vdsm.network.ovs import validator as ovs_validator

from testlib import VdsmTestCase as TestCaseBase
from nose.plugins.attrib import attr


@attr(type='unit')
class ValidationTests(TestCaseBase):

    def test_adding_a_new_single_untagged_net(self):
        fake_running_networks = {
            'net1': {'nic': 'eth0', 'vlan': 10, 'switch': 'ovs'}}
        with self.assertNotRaises():
            ovs_validator.validate_net_configuration(
                'net2', {'nic': 'eth0', 'switch': 'ovs'},
                fake_running_networks)

    def test_edit_single_untagged_net_nic(self):
        fake_running_networks = {
            'net1': {'nic': 'eth0', 'switch': 'ovs'}}
        with self.assertNotRaises():
            ovs_validator.validate_net_configuration(
                'net1', {'nic': 'eth1', 'switch': 'ovs'},
                fake_running_networks)

    def test_adding_a_second_untagged_net(self):
        fake_running_networks = {
            'net1': {'nic': 'eth0', 'switch': 'ovs'}}
        with self.assertRaises(ne.ConfigNetworkError):
            ovs_validator.validate_net_configuration(
                'net2', {'nic': 'eth1', 'switch': 'ovs'},
                fake_running_networks)

    def test_bond_with_no_slaves(self):
        fake_kernel_nics = []
        with self.assertRaises(ne.ConfigNetworkError):
            ovs_validator.validate_bond_configuration(
                {'switch': 'ovs'}, fake_kernel_nics)

    def test_bond_with_one_slave(self):
        fake_kernel_nics = ['eth0']
        with self.assertRaises(ne.ConfigNetworkError):
            ovs_validator.validate_bond_configuration(
                {'nics': ['eth0'], 'switch': 'ovs'}, fake_kernel_nics)

    def test_bond_with_two_slaves(self):
        fake_kernel_nics = ['eth0', 'eth1']
        with self.assertNotRaises():
            ovs_validator.validate_bond_configuration(
                {'nics': ['eth0', 'eth1'], 'switch': 'ovs'}, fake_kernel_nics)

    def test_bond_with_not_existing_slaves(self):
        fake_kernel_nics = []
        with self.assertRaises(ne.ConfigNetworkError):
            ovs_validator.validate_bond_configuration(
                {'nics': ['eth0', 'eth1'], 'switch': 'ovs'}, fake_kernel_nics)


@attr(type='unit')
class SplitActionTests(TestCaseBase):

    def test_split_nets_action(self):
        fake_running_nets = {'to-stay': {'nic': 'eth0'},
                             'to-edit': {'nic': 'eth1'},
                             'to-remove': {'nic': 'eth2'}}
        nets_query = {'to-edit': {'nic': 'eth3'},
                      'to-add': {'nic': 'eth4'},
                      'to-remove': {'remove': True}}
        nets_to_be_added, nets_to_be_removed = ovs_switch._split_nets_action(
            nets_query, fake_running_nets)
        self.assertEquals(set(nets_to_be_added.keys()), {'to-edit', 'to-add'})
        self.assertEquals(nets_to_be_removed, {'to-edit', 'to-remove'})

    def test_split_bonds_action(self):
        fake_running_bonds = {'to-stay': {'nics': ['eth0', 'eth1']},
                              'to-edit': {'nics': ['eth2', 'eth3']}}
        bonds_query = {'to-edit': {'nic': ['eth0', 'eth4']},
                       'to-add': {'nic': ['eth5', 'eth6']},
                       'to-remove': {'remove': True}}
        bonds_to_be_added, bonds_to_be_removed_or_edited = \
            ovs_switch._split_bonds_action(bonds_query, fake_running_bonds)
        self.assertEquals(set(bonds_to_be_added.keys()), {'to-edit', 'to-add'})
        self.assertEquals(bonds_to_be_removed_or_edited, {'to-remove'})
