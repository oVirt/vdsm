#
# Copyright 2013-2017 Red Hat, Inc.
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
import json
import os
import tempfile

from nose.plugins.attrib import attr

from vdsm.common import fileutils
from vdsm.network import errors as ne
from vdsm.network.canonicalize import canonicalize_networks
from vdsm.network.netconfpersistence import Config, Transaction

from testlib import VdsmTestCase as TestCaseBase


NETWORK = 'luke'
NETWORK_ATTRIBUTES = {'bonding': 'bond0', 'vlan': 1}
BONDING = 'skywalker'
BONDING_ATTRIBUTES = {'options': 'mode=4 miimon=100', 'nics': ['eth0', 'eth1'],
                      'switch': 'legacy'}


class TestException(Exception):
    pass


def _create_netconf():
    tempdir = tempfile.mkdtemp()
    os.mkdir(os.path.join(tempdir, 'nets'))
    os.mkdir(os.path.join(tempdir, 'bonds'))
    return tempdir


def setup_module():
    canonicalize_networks({'net': NETWORK_ATTRIBUTES})


@attr(type='unit')
class NetConfPersistenceTests(TestCaseBase):
    def setUp(self):
        self.tempdir = _create_netconf()

    def tearDown(self):
        fileutils.rm_tree(self.tempdir)

    def testInit(self):
        filePath = os.path.join(self.tempdir, 'nets', NETWORK)
        try:
            with open(filePath, 'w') as networkFile:
                json.dump(NETWORK_ATTRIBUTES, networkFile)

            persistence = Config(self.tempdir)
            self.assertEqual(persistence.networks[NETWORK], NETWORK_ATTRIBUTES)
        finally:
            fileutils.rm_file(filePath)

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

    def testSaveAndDelete(self):
        persistence = Config(self.tempdir)
        persistence.setNetwork(NETWORK, NETWORK_ATTRIBUTES)
        filePath = os.path.join(self.tempdir, 'nets', NETWORK)
        self.assertFalse(os.path.exists(filePath))
        persistence.save()
        self.assertTrue(os.path.exists(filePath))
        persistence.delete()
        self.assertFalse(os.path.exists(filePath))

    def testDiff(self):
        configA = Config(self.tempdir)
        configA.setNetwork(NETWORK, NETWORK_ATTRIBUTES)
        configA.setBonding(BONDING, BONDING_ATTRIBUTES)

        configB = Config(self.tempdir)
        configB.setNetwork(NETWORK, NETWORK_ATTRIBUTES)
        configB.setBonding(BONDING, BONDING_ATTRIBUTES)

        diff = configA.diffFrom(configB)
        self.assertEqual(diff.networks, {})
        self.assertEqual(diff.bonds, {})

        EVIL_NETWORK = 'jarjar'
        EVIL_BONDING_ATTRIBUTES = {'options': 'mode=3', 'nics': ['eth3']}
        configB.setNetwork(EVIL_NETWORK, NETWORK_ATTRIBUTES)
        configB.setBonding(BONDING, EVIL_BONDING_ATTRIBUTES)

        diff = configA.diffFrom(configB)
        self.assertEqual(diff.networks[EVIL_NETWORK], {'remove': True})
        self.assertEqual(diff.bonds[BONDING], BONDING_ATTRIBUTES)

        configB.removeNetwork(NETWORK)
        diff = configA.diffFrom(configB)
        self.assertIn(NETWORK, diff.networks)


@attr(type='unit')
class TransactionTests(TestCaseBase):
    def setUp(self):
        self.tempdir = _create_netconf()
        self.config = Config(self.tempdir)

    def tearDown(self):
        self.config.delete()
        self.assertFalse(os.path.exists(self.tempdir))

    def test_successful_setup(self):
        with Transaction(config=self.config) as _config:
            _config.setNetwork(NETWORK, NETWORK_ATTRIBUTES)

        file_path = os.path.join(self.tempdir, 'nets', NETWORK)
        self.assertTrue(os.path.exists(file_path))

    def test_successful_non_persistent_setup(self):
        with Transaction(config=self.config, persistent=False) as _config:
            _config.setNetwork(NETWORK, NETWORK_ATTRIBUTES)

        file_path = os.path.join(self.tempdir, 'nets', NETWORK)
        self.assertFalse(os.path.exists(file_path))

    def test_failed_setup(self):
        with self.assertRaises(ne.RollbackIncomplete) as roi:
            with Transaction(config=self.config) as _config:
                _config.setNetwork(NETWORK, NETWORK_ATTRIBUTES)
                raise TestException()

        diff, ex_type, _ = roi.exception.args
        self.assertEqual(diff.networks[NETWORK], {'remove': True})
        self.assertEqual(ex_type, TestException)
        file_path = os.path.join(self.tempdir, 'nets', NETWORK)
        self.assertFalse(os.path.exists(file_path))

    def test_failed_setup_with_no_diff(self):
        with self.assertRaises(TestException):
            with Transaction(config=self.config):
                raise TestException()

    def test_failed_setup_in_rollback(self):
        with self.assertRaises(TestException):
            with Transaction(config=self.config, in_rollback=True) as _config:
                _config.setNetwork(NETWORK, NETWORK_ATTRIBUTES)
                raise TestException()

        file_path = os.path.join(self.tempdir, 'nets', NETWORK)
        self.assertFalse(os.path.exists(file_path))
