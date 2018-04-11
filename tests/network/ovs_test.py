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
from vdsm.network.ovs import driver as ovs_driver
from vdsm.network.ovs import info as ovs_info
from vdsm.network.ovs import switch as ovs_switch
from vdsm.network.ovs import validator as ovs_validator

from .ovsnettestlib import cleanup_bridges
from testlib import VdsmTestCase as TestCaseBase
from testValidation import ValidateRunningAsRoot
from nose.plugins.attrib import attr


@attr(type='unit')
class ValidationTests(TestCaseBase):

    def test_adding_a_new_single_untagged_net(self):
        fake_running_bonds = {}
        fake_to_be_added_bonds = {}
        fake_kernel_nics = ['eth0']
        with self.assertNotRaises():
            ovs_validator.validate_net_configuration(
                'net2', {'nic': 'eth0', 'switch': 'ovs'},
                fake_to_be_added_bonds, fake_running_bonds, fake_kernel_nics)

    def test_edit_single_untagged_net_nic(self):
        fake_running_bonds = {}
        fake_to_be_added_bonds = {}
        fake_kernel_nics = ['eth0', 'eth1']
        with self.assertNotRaises():
            ovs_validator.validate_net_configuration(
                'net1', {'nic': 'eth1', 'switch': 'ovs'},
                fake_to_be_added_bonds, fake_running_bonds, fake_kernel_nics)

    def test_adding_a_second_untagged_net(self):
        fake_running_bonds = {}
        fake_to_be_added_bonds = {}
        fake_kernel_nics = ['eth0', 'eth1']
        with self.assertNotRaises():
            ovs_validator.validate_net_configuration(
                'net2', {'nic': 'eth1', 'switch': 'ovs'},
                fake_to_be_added_bonds, fake_running_bonds, fake_kernel_nics)

    def test_add_network_with_non_existing_nic(self):
        fake_running_bonds = {}
        fake_to_be_added_bonds = {}
        fake_kernel_nics = []
        with self.assertRaises(ne.ConfigNetworkError) as e:
            ovs_validator.validate_net_configuration(
                'net1', {'nic': 'eth0', 'switch': 'ovs'},
                fake_to_be_added_bonds, fake_running_bonds, fake_kernel_nics)
        self.assertEqual(e.exception.args[0], ne.ERR_BAD_NIC)

    def test_add_network_with_non_existing_bond(self):
        fake_running_bonds = {}
        fake_to_be_added_bonds = {}
        fake_kernel_nics = []
        with self.assertRaises(ne.ConfigNetworkError) as e:
            ovs_validator.validate_net_configuration(
                'net1', {'bonding': 'bond1', 'switch': 'ovs'},
                fake_to_be_added_bonds, fake_running_bonds, fake_kernel_nics)
        self.assertEqual(e.exception.args[0], ne.ERR_BAD_BONDING)

    def test_add_network_with_to_be_added_bond(self):
        fake_running_bonds = {}
        fake_to_be_added_bonds = {'bond1': {}}
        fake_kernel_nics = []
        with self.assertNotRaises():
            ovs_validator.validate_net_configuration(
                'net1', {'bonding': 'bond1', 'switch': 'ovs'},
                fake_to_be_added_bonds, fake_running_bonds, fake_kernel_nics)

    def test_add_network_with_running_bond(self):
        fake_running_bonds = {'bond1': {}}
        fake_to_be_added_bonds = {}
        fake_kernel_nics = []
        with self.assertNotRaises():
            ovs_validator.validate_net_configuration(
                'net1', {'bonding': 'bond1', 'switch': 'ovs'},
                fake_to_be_added_bonds, fake_running_bonds, fake_kernel_nics)

    def test_add_bond_with_no_slaves(self):
        fake_kernel_nics = []
        nets = {}
        running_nets = {}
        with self.assertRaises(ne.ConfigNetworkError):
            ovs_validator.validate_bond_configuration(
                'bond1', {'switch': 'ovs'}, nets, running_nets,
                fake_kernel_nics)

    def test_add_bond_with_one_slave(self):
        fake_kernel_nics = ['eth0']
        nets = {}
        running_nets = {}
        with self.assertNotRaises():
            ovs_validator.validate_bond_configuration(
                'bond1', {'nics': ['eth0'], 'switch': 'ovs'}, nets,
                running_nets, fake_kernel_nics)

    def test_add_bond_with_one_slave_twice(self):
        fake_kernel_nics = ['eth0']
        nets = {}
        running_nets = {}
        with self.assertNotRaises():
            ovs_validator.validate_bond_configuration(
                'bond1', {'nics': ['eth0', 'eth0'], 'switch': 'ovs'}, nets,
                running_nets, fake_kernel_nics)

    def test_add_bond_with_two_slaves(self):
        fake_kernel_nics = ['eth0', 'eth1']
        nets = {}
        running_nets = {}
        with self.assertNotRaises():
            ovs_validator.validate_bond_configuration(
                'bond1', {'nics': ['eth0', 'eth1'], 'switch': 'ovs'}, nets,
                running_nets, fake_kernel_nics)

    def test_add_bond_with_not_existing_slaves(self):
        fake_kernel_nics = []
        nets = {}
        running_nets = {}
        with self.assertRaises(ne.ConfigNetworkError):
            ovs_validator.validate_bond_configuration(
                'bond1', {'nics': ['eth0', 'eth1'], 'switch': 'ovs'},
                nets, running_nets, fake_kernel_nics)

    def test_add_bond_with_dpdk(self):
        fake_kernel_nics = []
        nets = {}
        running_nets = {}
        with self.assertRaises(ne.ConfigNetworkError):
            ovs_validator.validate_bond_configuration(
                'bond1', {'nics': ['eth0', 'dpdk0'], 'switch': 'ovs'},
                nets, running_nets, fake_kernel_nics)

    def test_remove_bond_attached_to_a_network(self):
        fake_kernel_nics = ['eth0', 'eth1']
        nets = {}
        running_nets = {}
        with self.assertNotRaises():
            ovs_validator.validate_bond_configuration(
                'bond1', {'remove': True}, nets, running_nets,
                fake_kernel_nics)

    def test_remove_bond_attached_to_network_that_was_removed(self):
        fake_kernel_nics = ['eth0', 'eth1']
        nets = {'net1': {'remove': True}}
        running_nets = {'net1': {'bond': 'bond1'}}
        with self.assertNotRaises():
            ovs_validator.validate_bond_configuration(
                'bond1', {'remove': True}, nets, running_nets,
                fake_kernel_nics)

    def test_remove_bond_attached_to_network_that_was_not_removed(self):
        fake_kernel_nics = ['eth0', 'eth1']
        nets = {}
        running_nets = {'net1': {'bond': 'bond1'}}
        with self.assertRaises(ne.ConfigNetworkError) as e:
            ovs_validator.validate_bond_configuration(
                'bond1', {'remove': True}, nets, running_nets,
                fake_kernel_nics)
        self.assertEqual(e.exception.args[0], ne.ERR_USED_BOND)

    def test_remove_bond_attached_to_network_that_will_use_nic(self):
        fake_kernel_nics = ['eth0', 'eth1']
        nets = {'net1': {'nic': 'eth0'}}
        running_nets = {'net1': {'bond': 'bond1'}}
        with self.assertNotRaises():
            ovs_validator.validate_bond_configuration(
                'bond1', {'remove': True}, nets, running_nets,
                fake_kernel_nics)

    def test_remove_bond_reattached_to_another_network(self):
        fake_kernel_nics = ['eth0', 'eth1', 'eth2']
        nets = {'net1': {'nic': 'eth0'}, 'net2': {'bonding': 'bond1'}}
        running_nets = {'net1': {'bond': 'bond1'}}
        with self.assertRaises(ne.ConfigNetworkError) as e:
            ovs_validator.validate_bond_configuration(
                'bond1', {'remove': True}, nets, running_nets,
                fake_kernel_nics)
        self.assertEqual(e.exception.args[0], ne.ERR_USED_BOND)


class MockedOvsInfo(ovs_info.OvsInfo):
    def __init__(self):
        self._bridges = {}
        self._bridges_by_sb = {}
        self._northbounds_by_sb = {}


@attr(type='integration')
class SetupTransactionTests(TestCaseBase):

    @ValidateRunningAsRoot
    def setUp(self):
        self.ovsdb = ovs_driver.create()

    def tearDown(self):
        cleanup_bridges()

    def test_dry_run(self):
        ovs_info = MockedOvsInfo()
        net_rem_setup = ovs_switch.NetsRemovalSetup(self.ovsdb, ovs_info)
        net_rem_setup.remove({})

        net_add_setup = ovs_switch.NetsAdditionSetup(self.ovsdb, ovs_info)
        with net_add_setup.add({}):
            pass
