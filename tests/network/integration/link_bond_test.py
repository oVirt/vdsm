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
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA
#
# Refer to the README and COPYING files for full details of the license
#

from __future__ import absolute_import
from __future__ import division

from contextlib import contextmanager
import errno
import os
import unittest

import pytest

from network.compat import mock
from network.nettestlib import dummy_devices, check_sysfs_bond_permission

from vdsm.network.link.bond import Bond
from vdsm.network.link.bond import sysfs_options
from vdsm.network.link.bond import sysfs_options_mapper
from vdsm.network.link.iface import iface
from vdsm.network.link.iface import random_iface_name


def setup_module():
    check_sysfs_bond_permission()


def _sorted_arp_ip_target(options):
    arp_ip_target = options.get('arp_ip_target')
    if arp_ip_target is not None:
        options['arp_ip_target'] = ','.join(sorted(arp_ip_target.split(',')))
    return options


class LinkBondTests(unittest.TestCase):
    def test_bond_without_slaves(self):
        with bond_device() as bond:
            self.assertFalse(iface(bond.master).is_up())

    def test_bond_with_slaves(self):
        with dummy_devices(2) as (nic1, nic2):
            with bond_device() as bond:
                bond.add_slaves((nic1, nic2))
                self.assertFalse(iface(bond.master).is_up())

    def test_bond_devices_are_up(self):
        with dummy_devices(2) as (nic1, nic2):
            with bond_device() as bond:
                bond.add_slaves((nic1, nic2))
                bond.up()
                self.assertTrue(iface(nic1).is_up())
                self.assertTrue(iface(nic2).is_up())
                self.assertTrue(iface(bond.master).is_up())

    def test_bond_exists(self):
        OPTIONS = {'mode': '1', 'miimon': '300'}
        with dummy_devices(2) as (nic1, nic2):
            with bond_device() as _bond:
                _bond.set_options(OPTIONS)
                _bond.add_slaves((nic1, nic2))
                _bond.up()

                bond = Bond(_bond.master)
                self.assertEqual(bond.slaves, set((nic1, nic2)))
                self.assertEqual(bond.options, OPTIONS)

    def test_bond_list(self):
        with bond_device() as b1, bond_device() as b2, bond_device() as b3:
            actual_bond_set = set(Bond.bonds())
            expected_bond_set = set([b1.master, b2.master, b3.master])
            self.assertLessEqual(expected_bond_set, actual_bond_set)

    def test_bond_create_failure_on_slave_add(self):
        with dummy_devices(2) as (nic1, nic2):
            with bond_device() as base_bond:
                base_bond.add_slaves((nic1, nic2))

                bond_name = random_iface_name('bond_', max_length=11)
                with self.assertRaises(IOError):
                    with Bond(bond_name) as broken_bond:
                        broken_bond.create()
                        broken_bond.add_slaves((nic1, nic2))
                self.assertFalse(Bond(bond_name).exists())

    def test_bond_edit_failure_on_slave_add(self):
        with dummy_devices(2) as (nic1, nic2):
            with bond_device() as base_bond, bond_device() as edit_bond:
                base_bond.add_slaves((nic1,))
                edit_bond.add_slaves((nic2,))

                with self.assertRaises(IOError):
                    with Bond(edit_bond.master) as broken_bond:
                        self.assertTrue(broken_bond.exists())
                        broken_bond.add_slaves((nic1,))
                self.assertTrue(edit_bond.exists())
                self.assertEqual(set((nic2,)), edit_bond.slaves)

    def test_bond_set_options(self):
        OPTIONS = {'mode': '1', 'miimon': '300'}

        with dummy_devices(2) as (nic1, nic2):
            with bond_device() as bond:
                bond.set_options(OPTIONS)
                bond.add_slaves((nic1, nic2))
                bond.up()

                _bond = Bond(bond.master)
                self.assertEqual(_bond.options, OPTIONS)

    def test_bond_edit_options(self):
        OPTIONS_A = {'mode': '1', 'miimon': '300'}
        OPTIONS_B = {'mode': '2'}
        OPTIONS_C = {'mode': 'balance-rr', 'miimon': '150'}

        with dummy_devices(2) as (nic1, nic2):
            with bond_device() as bond:
                bond.set_options(OPTIONS_A)
                bond.add_slaves((nic1, nic2))
                _bond = Bond(bond.master)
                self.assertEqual(_bond.options, OPTIONS_A)

                bond.set_options(OPTIONS_B)
                _bond.refresh()
                self.assertEqual(_bond.options, OPTIONS_B)

                bond.set_options(OPTIONS_C)
                _bond.refresh()
                OPTIONS_C['mode'] = '0'
                self.assertEqual(_bond.options, OPTIONS_C)

    def test_bond_set_one_arp_ip_target(self):
        OPTIONS = {
            'mode': '1',
            'arp_interval': '1000',
            'arp_ip_target': '192.168.122.1',
        }

        with bond_device() as bond:
            bond.set_options(OPTIONS)
            bond.refresh()
            self.assertEqual(bond.options, OPTIONS)

    def test_bond_set_two_arp_ip_targets(self):
        OPTIONS = {
            'mode': '1',
            'arp_interval': '1000',
            'arp_ip_target': '10.1.3.1,10.1.2.1',
        }

        with bond_device() as bond:
            bond.set_options(OPTIONS)
            bond.refresh()
            self.assertEqual(
                _sorted_arp_ip_target(bond.options),
                _sorted_arp_ip_target(OPTIONS),
            )

    def test_bond_clear_arp_ip_target(self):
        OPTIONS_A = {
            'mode': '1',
            'arp_interval': '1000',
            'arp_ip_target': '192.168.122.1',
        }
        OPTIONS_B = {'mode': '1', 'arp_interval': '1000'}

        with bond_device() as bond:
            bond.set_options(OPTIONS_A)
            bond.set_options(OPTIONS_B)
            bond.refresh()
            self.assertEqual(bond.options, OPTIONS_B)

    def test_bond_update_existing_arp_ip_targets(self):
        preserved_addr = '10.1.4.1'
        removed_addr = '10.1.3.1,10.1.2.1'
        added_addr = '10.1.1.1'

        OPTIONS_A = {
            'mode': '1',
            'arp_interval': '1000',
            'arp_ip_target': ','.join([preserved_addr, removed_addr]),
        }
        OPTIONS_B = {
            'mode': '1',
            'arp_interval': '1000',
            'arp_ip_target': ','.join([preserved_addr, added_addr]),
        }

        with dummy_devices(2) as (nic1, nic2):
            with bond_device() as bond:
                bond.set_options(OPTIONS_A)
                bond.set_options(OPTIONS_B)
                bond.refresh()
                self.assertEqual(bond.options, OPTIONS_B)

    def test_bond_properties_includes_non_options_keys(self):
        with bond_device() as bond:
            self.assertTrue('active_slave' in bond.properties)


class LinkBondSysFSTests(unittest.TestCase):
    def test_do_not_detach_slaves_while_changing_options(self):
        OPTIONS = {'miimon': '110'}

        with dummy_devices(2) as (nic1, nic2):
            with bond_device() as bond:
                bond.add_slaves((nic1, nic2))
                mock_slaves = bond.del_slaves = bond.add_slaves = mock.Mock()

                bond.set_options(OPTIONS)

                mock_slaves.assert_not_called()

    def test_bond_properties_with_filter(self):
        with bond_device() as bond:
            properties = sysfs_options.properties(
                bond.master, filter_properties=('mode',)
            )
            self.assertTrue('mode' in properties)
            self.assertEqual(1, len(properties))

    def test_bond_properties_with_filter_out(self):
        with bond_device() as bond:
            properties = sysfs_options.properties(
                bond.master, filter_out_properties=('mode',)
            )
            self.assertTrue('mode' not in properties)
            self.assertGreater(len(properties), 1)


class TestBondingSysfsOptionsMapper(unittest.TestCase):
    def test_dump_bonding_name2numeric(self):
        BOND_MODE = '0'
        OPT_NAME = 'arp_validate'
        VAL_NAME = 'none'
        VAL_NUMERIC = '0'

        try:
            opt_map = sysfs_options_mapper._get_bonding_options_name2numeric()
        except IOError as e:
            if e.errno == errno.EBUSY:
                pytest.skip(
                    'Bond option mapping failed on EBUSY, '
                    'Kernel version: %s' % os.uname()[2]
                )
            raise

        self.assertIn(BOND_MODE, opt_map)
        self.assertIn(OPT_NAME, opt_map[BOND_MODE])
        self.assertIn(VAL_NAME, opt_map[BOND_MODE][OPT_NAME])
        self.assertEqual(opt_map[BOND_MODE][OPT_NAME][VAL_NAME], VAL_NUMERIC)

    def test_get_bonding_option_numeric_val_exists(self):
        opt_num_val = self._get_bonding_option_num_val('ad_select', 'stable')
        self.assertNotEqual(opt_num_val, None)

    def test_get_bonding_option_numeric_val_does_not_exists(self):
        opt_num_val = self._get_bonding_option_num_val('no_such_opt', 'none')
        self.assertEqual(opt_num_val, None)

    def _get_bonding_option_num_val(self, option_name, val_name):
        mode_num = sysfs_options.BONDING_MODES_NAME_TO_NUMBER['balance-rr']
        opt_num_val = sysfs_options_mapper.get_bonding_option_numeric_val(
            mode_num, option_name, val_name
        )
        return opt_num_val


@contextmanager
def bond_device(prefix='bond_', max_length=11):
    bond_name = random_iface_name(prefix, max_length)
    bond = Bond(bond_name)
    bond.create()
    try:
        yield bond
    finally:
        bond.destroy()
