#
# Copyright 2013-2018 Red Hat, Inc.
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
from __future__ import division
import json
import os
import tempfile

from vdsm.common import fileutils
from vdsm.network import errors as ne
from vdsm.network.canonicalize import canonicalize_networks
from vdsm.network.netconfpersistence import BaseConfig
from vdsm.network.netconfpersistence import Config
from vdsm.network.netconfpersistence import Transaction
from vdsm.network.netconfpersistence import NETCONF_NETS
from vdsm.network.netconfpersistence import NETCONF_BONDS
from vdsm.network.netconfpersistence import NETCONF_DEVS

from testlib import VdsmTestCase as TestCaseBase


NETWORK = 'luke'
NETWORK_ATTRIBUTES = {'bonding': 'bond0', 'vlan': 1}
BONDING = 'skywalker'
BONDING_ATTRIBUTES = {
    'options': 'mode=4 miimon=100',
    'nics': ['eth0', 'eth1'],
    'switch': 'legacy',
}
DEVICE = 'dev0'
DEVICE_ATTRIBUTES = {'sriov': {'numvfs': 2}}


class TestException(Exception):
    pass


def _create_netconf():
    tempdir = tempfile.mkdtemp()
    os.mkdir(os.path.join(tempdir, NETCONF_NETS))
    os.mkdir(os.path.join(tempdir, NETCONF_BONDS))
    os.mkdir(os.path.join(tempdir, NETCONF_DEVS))
    return tempdir


def setup_module():
    canonicalize_networks({'net': NETWORK_ATTRIBUTES})


class TestNetConfBaseConfig(TestCaseBase):
    def test_empty_config(self):
        config = BaseConfig({}, {}, {})
        self.assertFalse(config)


class NetConfPersistenceTests(TestCaseBase):
    def setUp(self):
        self.tempdir = _create_netconf()

    def tearDown(self):
        fileutils.rm_tree(self.tempdir)

    def testInit(self):
        net_path = os.path.join(self.tempdir, NETCONF_NETS, NETWORK)
        bond_path = os.path.join(self.tempdir, NETCONF_BONDS, BONDING)
        device_path = os.path.join(self.tempdir, NETCONF_DEVS, DEVICE)
        with open(net_path, 'w') as f:
            json.dump(NETWORK_ATTRIBUTES, f)
        with open(bond_path, 'w') as f:
            json.dump(BONDING_ATTRIBUTES, f)
        with open(device_path, 'w') as f:
            json.dump(DEVICE_ATTRIBUTES, f)

        persistence = Config(self.tempdir)
        self.assertEqual(persistence.networks[NETWORK], NETWORK_ATTRIBUTES)
        self.assertEqual(persistence.bonds[BONDING], BONDING_ATTRIBUTES)
        self.assertEqual(persistence.devices[DEVICE], DEVICE_ATTRIBUTES)

    def testSetAndRemoveNetwork(self):
        persistence = Config(self.tempdir)
        persistence.setNetwork(NETWORK, NETWORK_ATTRIBUTES)
        self.assertEqual(persistence.networks[NETWORK], NETWORK_ATTRIBUTES)
        persistence.removeNetwork(NETWORK)
        self.assertTrue(persistence.networks.get(NETWORK) is None)

    def testSetAndRemoveBonding(self):
        persistence = Config(self.tempdir)
        persistence.setBonding(BONDING, BONDING_ATTRIBUTES)
        self.assertEqual(persistence.bonds[BONDING], BONDING_ATTRIBUTES)
        persistence.removeBonding(BONDING)
        self.assertTrue(persistence.bonds.get(BONDING) is None)

    def testSetAndRemoveDevice(self):
        persistence = Config(self.tempdir)
        persistence.set_device(DEVICE, DEVICE_ATTRIBUTES)
        self.assertEqual(persistence.devices[DEVICE], DEVICE_ATTRIBUTES)
        persistence.remove_device(DEVICE)
        self.assertTrue(persistence.devices.get(DEVICE) is None)

    def testSaveAndDelete(self):
        persistence = Config(self.tempdir)
        persistence.setNetwork(NETWORK, NETWORK_ATTRIBUTES)
        persistence.setBonding(BONDING, BONDING_ATTRIBUTES)
        persistence.set_device(DEVICE, DEVICE_ATTRIBUTES)

        net_path = os.path.join(self.tempdir, NETCONF_NETS, NETWORK)
        bond_path = os.path.join(self.tempdir, NETCONF_BONDS, BONDING)
        device_path = os.path.join(self.tempdir, NETCONF_DEVS, DEVICE)
        self.assertFalse(os.path.exists(net_path))
        self.assertFalse(os.path.exists(bond_path))
        self.assertFalse(os.path.exists(device_path))

        persistence.save()
        self.assertTrue(os.path.exists(net_path))
        self.assertTrue(os.path.exists(bond_path))
        self.assertTrue(os.path.exists(device_path))

        persistence.delete()
        self.assertFalse(os.path.exists(net_path))
        self.assertFalse(os.path.exists(bond_path))
        self.assertFalse(os.path.exists(device_path))

    def testDiff(self):
        configA = Config(self.tempdir)
        configA.setNetwork(NETWORK, NETWORK_ATTRIBUTES)
        configA.setBonding(BONDING, BONDING_ATTRIBUTES)
        configA.set_device(DEVICE, DEVICE_ATTRIBUTES)

        configB = Config(self.tempdir)
        configB.setNetwork(NETWORK, NETWORK_ATTRIBUTES)
        configB.setBonding(BONDING, BONDING_ATTRIBUTES)
        configB.set_device(DEVICE, DEVICE_ATTRIBUTES)

        diff = configA.diffFrom(configB)
        self.assertEqual(diff.networks, {})
        self.assertEqual(diff.bonds, {})
        self.assertEqual(diff.devices, {})

        EVIL_NETWORK = 'jarjar'
        EVIL_BONDING_ATTRIBUTES = {'options': 'mode=3', 'nics': ['eth3']}
        EVIL_DEVICE = 'devdev'
        configB.setNetwork(EVIL_NETWORK, NETWORK_ATTRIBUTES)
        configB.setBonding(BONDING, EVIL_BONDING_ATTRIBUTES)
        configB.set_device(EVIL_DEVICE, DEVICE_ATTRIBUTES)

        diff = configA.diffFrom(configB)
        self.assertEqual(diff.networks[EVIL_NETWORK], {'remove': True})
        self.assertEqual(diff.bonds[BONDING], BONDING_ATTRIBUTES)
        # Devices diff is not yet supported.
        self.assertEqual(diff.devices, {})

        configB.removeNetwork(NETWORK)
        diff = configA.diffFrom(configB)
        self.assertIn(NETWORK, diff.networks)


class TransactionTests(TestCaseBase):
    def setUp(self):
        self.tempdir = _create_netconf()
        self.config = Config(self.tempdir)
        self.net_path = os.path.join(self.tempdir, NETCONF_NETS, NETWORK)
        self.bond_path = os.path.join(self.tempdir, NETCONF_BONDS, BONDING)
        self.device_path = os.path.join(self.tempdir, NETCONF_DEVS, DEVICE)

    def tearDown(self):
        self.config.delete()
        self.assertFalse(os.path.exists(self.tempdir))

    def test_successful_setup(self):
        with Transaction(config=self.config) as _config:
            _config.setNetwork(NETWORK, NETWORK_ATTRIBUTES)
            _config.setBonding(BONDING, BONDING_ATTRIBUTES)
            _config.set_device(DEVICE, DEVICE_ATTRIBUTES)

        self.assertTrue(os.path.exists(self.net_path))
        self.assertTrue(os.path.exists(self.bond_path))
        self.assertTrue(os.path.exists(self.device_path))

    def test_successful_non_persistent_setup(self):
        with Transaction(config=self.config, persistent=False) as _config:
            _config.setNetwork(NETWORK, NETWORK_ATTRIBUTES)
            _config.setBonding(BONDING, BONDING_ATTRIBUTES)
            _config.set_device(DEVICE, DEVICE_ATTRIBUTES)

        self.assertFalse(os.path.exists(self.net_path))
        self.assertFalse(os.path.exists(self.bond_path))
        self.assertFalse(os.path.exists(self.device_path))

    def test_failed_setup(self):
        with self.assertRaises(ne.RollbackIncomplete) as roi:
            with Transaction(config=self.config) as _config:
                _config.setNetwork(NETWORK, NETWORK_ATTRIBUTES)
                _config.setBonding(BONDING, BONDING_ATTRIBUTES)
                _config.set_device(DEVICE, DEVICE_ATTRIBUTES)
                raise TestException()

        diff = roi.exception.diff
        self.assertEqual(diff.networks[NETWORK], {'remove': True})
        self.assertEqual(diff.bonds[BONDING], {'remove': True})
        self.assertEqual(diff.devices, {})
        self.assertEqual(roi.exception.exc_type, TestException)
        self.assertFalse(os.path.exists(self.net_path))
        self.assertFalse(os.path.exists(self.bond_path))
        self.assertFalse(os.path.exists(self.device_path))

    def test_failed_setup_with_no_diff(self):
        with self.assertRaises(TestException):
            with Transaction(config=self.config):
                raise TestException()

    def test_failed_setup_in_rollback(self):
        with self.assertRaises(TestException):
            with Transaction(config=self.config, in_rollback=True) as _config:
                _config.setNetwork(NETWORK, NETWORK_ATTRIBUTES)
                _config.setBonding(BONDING, BONDING_ATTRIBUTES)
                _config.set_device(DEVICE, DEVICE_ATTRIBUTES)
                raise TestException()

        self.assertFalse(os.path.exists(self.net_path))
        self.assertFalse(os.path.exists(self.bond_path))
        self.assertFalse(os.path.exists(self.device_path))
