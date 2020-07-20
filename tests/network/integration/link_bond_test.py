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

import errno
import os
import time

import pytest

from network.compat import mock
from network.nettestlib import bond_device
from network.nettestlib import check_sysfs_bond_permission
from network.nettestlib import dummy_devices
from network.nettestlib import FakeNotifier

from vdsm.network import bond_monitor
from vdsm.network.link.bond import Bond
from vdsm.network.link.bond import sysfs_options
from vdsm.network.link.bond import sysfs_options_mapper
from vdsm.network.link.iface import iface
from vdsm.network.link.iface import random_iface_name


@pytest.fixture(scope='module', autouse=True)
def setup():
    check_sysfs_bond_permission()


def _sorted_arp_ip_target(options):
    arp_ip_target = options.get('arp_ip_target')
    if arp_ip_target is not None:
        options['arp_ip_target'] = ','.join(sorted(arp_ip_target.split(',')))
    return options


class TestLinkBond(object):
    def test_bond_without_slaves(self):
        with bond_device() as bond:
            assert not iface(bond.master).is_up()

    def test_bond_with_slaves(self):
        with dummy_devices(2) as (nic1, nic2):
            with bond_device() as bond:
                bond.add_slaves((nic1, nic2))
                assert not iface(bond.master).is_up()

    def test_bond_devices_are_up(self):
        with dummy_devices(2) as (nic1, nic2):
            with bond_device() as bond:
                bond.add_slaves((nic1, nic2))
                bond.up()
                assert iface(nic1).is_up()
                assert iface(nic2).is_up()
                assert iface(bond.master).is_up()

    def test_bond_exists(self):
        OPTIONS = {'mode': '1', 'miimon': '300'}
        with dummy_devices(2) as (nic1, nic2):
            with bond_device() as _bond:
                _bond.set_options(OPTIONS)
                _bond.add_slaves((nic1, nic2))
                _bond.up()

                bond = Bond(_bond.master)
                assert bond.slaves == set((nic1, nic2))
                assert bond.options == OPTIONS

    def test_bond_list(self):
        with bond_device() as b1, bond_device() as b2, bond_device() as b3:
            actual_bond_set = set(Bond.bonds())
            expected_bond_set = set([b1.master, b2.master, b3.master])
            assert expected_bond_set <= actual_bond_set

    def test_bond_create_failure_on_slave_add(self):
        with dummy_devices(2) as (nic1, nic2):
            with bond_device() as base_bond:
                base_bond.add_slaves((nic1, nic2))

                bond_name = random_iface_name('bond_', max_length=11)
                with pytest.raises(IOError):
                    with Bond(bond_name) as broken_bond:
                        broken_bond.create()
                        broken_bond.add_slaves((nic1, nic2))
                assert not Bond(bond_name).exists()

    def test_bond_edit_failure_on_slave_add(self):
        with dummy_devices(2) as (nic1, nic2):
            with bond_device() as base_bond, bond_device() as edit_bond:
                base_bond.add_slaves((nic1,))
                edit_bond.add_slaves((nic2,))

                with pytest.raises(IOError):
                    with Bond(edit_bond.master) as broken_bond:
                        assert broken_bond.exists()
                        broken_bond.add_slaves((nic1,))
                assert edit_bond.exists()
                assert edit_bond.slaves == set((nic2,))

    def test_bond_set_options(self):
        OPTIONS = {'mode': '1', 'miimon': '300'}

        with dummy_devices(2) as (nic1, nic2):
            with bond_device() as bond:
                bond.set_options(OPTIONS)
                bond.add_slaves((nic1, nic2))
                bond.up()

                _bond = Bond(bond.master)
                assert _bond.options == OPTIONS

    def test_bond_edit_options(self):
        OPTIONS_A = {'mode': '1', 'miimon': '300'}
        OPTIONS_B = {'mode': '2'}
        OPTIONS_C = {'mode': 'balance-rr', 'miimon': '150'}

        with dummy_devices(2) as (nic1, nic2):
            with bond_device() as bond:
                bond.set_options(OPTIONS_A)
                bond.add_slaves((nic1, nic2))
                _bond = Bond(bond.master)
                assert _bond.options == OPTIONS_A

                bond.set_options(OPTIONS_B)
                _bond.refresh()
                assert _bond.options == OPTIONS_B

                bond.set_options(OPTIONS_C)
                _bond.refresh()
                OPTIONS_C['mode'] = '0'
                assert _bond.options == OPTIONS_C

    def test_bond_set_one_arp_ip_target(self):
        OPTIONS = {
            'mode': '1',
            'arp_interval': '1000',
            'arp_ip_target': '192.168.122.1',
        }

        with bond_device() as bond:
            bond.set_options(OPTIONS)
            bond.refresh()
            assert bond.options == OPTIONS

    def test_bond_set_two_arp_ip_targets(self):
        OPTIONS = {
            'mode': '1',
            'arp_interval': '1000',
            'arp_ip_target': '10.1.3.1,10.1.2.1',
        }

        with bond_device() as bond:
            bond.set_options(OPTIONS)
            bond.refresh()
            sorted_bond_opts = _sorted_arp_ip_target(bond.options)
            sorted_opts = _sorted_arp_ip_target(OPTIONS)
            assert sorted_bond_opts == sorted_opts

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
            assert bond.options == OPTIONS_B

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
                assert bond.options == OPTIONS_B

    def test_bond_properties_includes_non_options_keys(self):
        with bond_device() as bond:
            assert 'active_slave' in bond.properties

    def test_bond_monitor(self):
        notifier = FakeNotifier()
        with dummy_devices(2) as (nic1, nic2):
            with bond_device(slaves=(nic1, nic2)) as bond:
                bond.set_options({'mode': '1'})
                bond.up()
                bond.set_options({'active_slave': nic1})
                bond_monitor.initialize_monitor(notifier)
                try:
                    bond.set_options({'active_slave': nic2})
                    _wait_until(lambda: notifier.calls)
                finally:
                    bond_monitor.stop()
        assert notifier.calls == [('|net|host_conn|no_id', None)]


def _wait_until(condition, timeout=1, interval=0.1):
    start = time.time()
    while not condition() and time.time() - start < timeout:
        time.sleep(interval)


class TestLinkBondSysFS(object):
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
            assert 'mode' in properties
            assert len(properties) == 1

    def test_bond_properties_with_filter_out(self):
        with bond_device() as bond:
            properties = sysfs_options.properties(
                bond.master, filter_out_properties=('mode',)
            )
            assert 'mode' not in properties
            assert len(properties) > 1


class TestBondingSysfsOptionsMapper(object):
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

        assert BOND_MODE in opt_map
        assert OPT_NAME in opt_map[BOND_MODE]
        assert VAL_NAME in opt_map[BOND_MODE][OPT_NAME]
        assert opt_map[BOND_MODE][OPT_NAME][VAL_NAME] == VAL_NUMERIC

    def test_get_bonding_option_numeric_val_exists(self):
        opt_num_val = self._get_bonding_option_num_val('ad_select', 'stable')
        assert opt_num_val is not None

    def test_get_bonding_option_numeric_val_does_not_exists(self):
        opt_num_val = self._get_bonding_option_num_val('no_such_opt', 'none')
        assert opt_num_val is None

    def _get_bonding_option_num_val(self, option_name, val_name):
        mode_num = sysfs_options.BONDING_MODES_NAME_TO_NUMBER['balance-rr']
        opt_num_val = sysfs_options_mapper.get_bonding_option_numeric_val(
            mode_num, option_name, val_name
        )
        return opt_num_val
