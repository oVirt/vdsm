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
from unittest import mock

import pytest

from network.nettestlib import bond_device_link as bond_device
from network.nettestlib import check_sysfs_bond_permission
from network.nettestlib import dummy_devices
from network.nettestlib import FakeNotifier
from network.nettestlib import running_on_travis_ci

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


@pytest.fixture
def nics():
    with dummy_devices(2) as nics:
        yield nics


@pytest.fixture
def bond0():
    with bond_device() as bond:
        yield bond


@pytest.fixture
def bond1():
    with bond_device() as bond:
        yield bond


@pytest.fixture
def bond2():
    with bond_device() as bond:
        yield bond


@pytest.fixture
def bond_with_slaves(bond0, nics):
    bond0.add_slaves(nics)
    yield bond0


class TestLinkBond(object):
    def test_bond_without_slaves(self, bond0):
        assert not iface(bond0.master).is_up()

    def test_bond_with_slaves(self, bond_with_slaves):
        assert not iface(bond_with_slaves.master).is_up()

    def test_bond_devices_are_up(self, bond_with_slaves):
        bond_with_slaves.up()
        for nic in bond_with_slaves.slaves:
            assert iface(nic).is_up()
        assert iface(bond_with_slaves.master).is_up()

    def test_bond_exists(self, bond_with_slaves):
        OPTIONS = {'mode': '1', 'miimon': '300'}
        bond_with_slaves.set_options(OPTIONS)
        bond_with_slaves.up()

        bond = Bond(bond_with_slaves.master)
        assert bond.slaves == set(bond_with_slaves.slaves)
        assert bond.options == OPTIONS

    def test_bond_list(self, bond0, bond1, bond2):
        actual_bond_set = set(Bond.bonds())
        expected_bond_set = {b.master for b in (bond0, bond1, bond2)}
        assert expected_bond_set <= actual_bond_set

    def test_bond_create_failure_on_slave_add(self, bond_with_slaves):
        bond_name = random_iface_name('bond_', max_length=11)
        with pytest.raises(IOError):
            with Bond(bond_name) as broken_bond:
                broken_bond.create()
                broken_bond.add_slaves(bond_with_slaves.slaves)
        assert not Bond(bond_name).exists()

    def test_bond_edit_failure_on_slave_add(self, bond0, bond1, nics):
        base_bond, edit_bond = bond0, bond1
        base_bond.add_slaves((nics[0],))
        edit_bond.add_slaves((nics[1],))

        with pytest.raises(IOError):
            with Bond(edit_bond.master) as broken_bond:
                assert broken_bond.exists()
                broken_bond.add_slaves((nics[0],))
        assert edit_bond.exists()
        assert edit_bond.slaves == {nics[1]}

    def test_bond_set_options(self, bond_with_slaves):
        OPTIONS = {'mode': '1', 'miimon': '300'}
        bond_with_slaves.set_options(OPTIONS)
        bond_with_slaves.up()

        bond = Bond(bond_with_slaves.master)
        assert bond.options == OPTIONS

    def test_bond_edit_options(self, bond_with_slaves):
        OPTIONS_A = {'mode': '1', 'miimon': '300'}
        OPTIONS_B = {'mode': '2'}
        OPTIONS_C = {'mode': 'balance-rr', 'miimon': '150'}

        bond_with_slaves.set_options(OPTIONS_A)
        bond = Bond(bond_with_slaves.master)
        assert bond.options == OPTIONS_A

        bond_with_slaves.set_options(OPTIONS_B)
        bond.refresh()
        assert bond.options == OPTIONS_B

        bond_with_slaves.set_options(OPTIONS_C)
        bond.refresh()
        OPTIONS_C['mode'] = '0'
        assert bond.options == OPTIONS_C

    def test_bond_set_one_arp_ip_target(self, bond0):
        OPTIONS = {
            'mode': '1',
            'arp_interval': '1000',
            'arp_ip_target': '192.168.122.1',
        }
        bond0.set_options(OPTIONS)
        bond0.refresh()
        assert bond0.options == OPTIONS

    def test_bond_set_two_arp_ip_targets(self, bond0):
        OPTIONS = {
            'mode': '1',
            'arp_interval': '1000',
            'arp_ip_target': '10.1.3.1,10.1.2.1',
        }
        bond0.set_options(OPTIONS)
        bond0.refresh()
        sorted_bond_opts = _sorted_arp_ip_target(bond0.options)
        sorted_opts = _sorted_arp_ip_target(OPTIONS)
        assert sorted_bond_opts == sorted_opts

    def test_bond_clear_arp_ip_target(self, bond0):
        OPTIONS_A = {
            'mode': '1',
            'arp_interval': '1000',
            'arp_ip_target': '192.168.122.1',
        }
        OPTIONS_B = {'mode': '1', 'arp_interval': '1000'}

        bond0.set_options(OPTIONS_A)
        bond0.set_options(OPTIONS_B)
        bond0.refresh()
        assert bond0.options == OPTIONS_B

    def test_bond_update_existing_arp_ip_targets(self, bond0):
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

        bond0.set_options(OPTIONS_A)
        bond0.set_options(OPTIONS_B)
        bond0.refresh()
        assert bond0.options == OPTIONS_B

    def test_bond_properties_includes_non_options_keys(self, bond0):
        assert 'active_slave' in bond0.properties

    def test_bond_monitor(self, nics):
        notifier = FakeNotifier()
        with bond_device(slaves=nics) as bond:
            bond.set_options({'mode': '1'})
            bond.up()
            bond.set_options({'active_slave': nics[0]})
            bond_monitor.initialize_monitor(notifier)
            try:
                bond.set_options({'active_slave': nics[1]})
                _wait_until(lambda: notifier.calls)
            finally:
                bond_monitor.stop()
        assert notifier.calls == [('|net|host_conn|no_id', None)]


def _wait_until(condition, timeout=1, interval=0.1):
    start = time.time()
    while not condition() and time.time() - start < timeout:
        time.sleep(interval)


class TestLinkBondSysFS(object):
    def test_do_not_detach_slaves_while_changing_options(
        self, bond_with_slaves
    ):
        OPTIONS = {'miimon': '110'}

        bond = bond_with_slaves
        mock_slaves = bond.del_slaves = bond.add_slaves = mock.Mock()
        bond.set_options(OPTIONS)

        mock_slaves.assert_not_called()

    def test_bond_properties_with_filter(self, bond0):
        properties = sysfs_options.properties(
            bond0.master, filter_properties=('mode',)
        )
        assert 'mode' in properties
        assert len(properties) == 1

    def test_bond_properties_with_filter_out(self, bond0):
        properties = sysfs_options.properties(
            bond0.master, filter_out_properties=('mode',)
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
