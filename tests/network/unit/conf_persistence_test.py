# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import
from __future__ import division
import json
import os
import tempfile

import pytest

from vdsm.common import fileutils
from vdsm.network import errors as ne
from vdsm.network.canonicalize import canonicalize_networks
from vdsm.network.netconfpersistence import BaseConfig
from vdsm.network.netconfpersistence import Config
from vdsm.network.netconfpersistence import Transaction
from vdsm.network.netconfpersistence import NETCONF_NETS
from vdsm.network.netconfpersistence import NETCONF_BONDS
from vdsm.network.netconfpersistence import NETCONF_DEVS


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


class FailTest(Exception):
    pass


@pytest.fixture
def netconf_dir():
    tempdir = tempfile.mkdtemp()
    try:
        os.mkdir(os.path.join(tempdir, NETCONF_NETS))
        os.mkdir(os.path.join(tempdir, NETCONF_BONDS))
        os.mkdir(os.path.join(tempdir, NETCONF_DEVS))
        yield tempdir
    finally:
        fileutils.rm_tree(tempdir)


@pytest.fixture(scope='module', autouse=True)
def canonicalize_networks_attributes():
    canonicalize_networks({'net': NETWORK_ATTRIBUTES})


class TestNetConfBaseConfig(object):
    def test_empty_config(self):
        config = BaseConfig({}, {}, {})
        assert not config


class TestNetConfPersistence(object):
    def testInit(self, netconf_dir):
        net_path = os.path.join(netconf_dir, NETCONF_NETS, NETWORK)
        bond_path = os.path.join(netconf_dir, NETCONF_BONDS, BONDING)
        device_path = os.path.join(netconf_dir, NETCONF_DEVS, DEVICE)
        with open(net_path, 'w') as f:
            json.dump(NETWORK_ATTRIBUTES, f)
        with open(bond_path, 'w') as f:
            json.dump(BONDING_ATTRIBUTES, f)
        with open(device_path, 'w') as f:
            json.dump(DEVICE_ATTRIBUTES, f)

        persistence = Config(netconf_dir)
        assert persistence.networks[NETWORK] == NETWORK_ATTRIBUTES
        assert persistence.bonds[BONDING] == BONDING_ATTRIBUTES
        assert persistence.devices[DEVICE] == DEVICE_ATTRIBUTES

    def testSetAndRemoveNetwork(self, netconf_dir):
        persistence = Config(netconf_dir)
        persistence.setNetwork(NETWORK, NETWORK_ATTRIBUTES)
        assert persistence.networks[NETWORK] == NETWORK_ATTRIBUTES
        persistence.removeNetwork(NETWORK)
        assert persistence.networks.get(NETWORK) is None

    def testSetAndRemoveBonding(self, netconf_dir):
        persistence = Config(netconf_dir)
        persistence.setBonding(BONDING, BONDING_ATTRIBUTES)
        assert persistence.bonds[BONDING] == BONDING_ATTRIBUTES
        persistence.removeBonding(BONDING)
        assert persistence.bonds.get(BONDING) is None

    def testSetAndRemoveDevice(self, netconf_dir):
        persistence = Config(netconf_dir)
        persistence.set_device(DEVICE, DEVICE_ATTRIBUTES)
        assert persistence.devices[DEVICE] == DEVICE_ATTRIBUTES
        persistence.remove_device(DEVICE)
        assert persistence.devices.get(DEVICE) is None

    def testSaveAndDelete(self, netconf_dir):
        persistence = Config(netconf_dir)
        persistence.setNetwork(NETWORK, NETWORK_ATTRIBUTES)
        persistence.setBonding(BONDING, BONDING_ATTRIBUTES)
        persistence.set_device(DEVICE, DEVICE_ATTRIBUTES)

        net_path = os.path.join(netconf_dir, NETCONF_NETS, NETWORK)
        bond_path = os.path.join(netconf_dir, NETCONF_BONDS, BONDING)
        device_path = os.path.join(netconf_dir, NETCONF_DEVS, DEVICE)
        assert not os.path.exists(net_path)
        assert not os.path.exists(bond_path)
        assert not os.path.exists(device_path)

        persistence.save()
        assert os.path.exists(net_path)
        assert os.path.exists(bond_path)
        assert os.path.exists(device_path)

        persistence.delete()
        assert not os.path.exists(net_path)
        assert not os.path.exists(bond_path)
        assert not os.path.exists(device_path)

    def testDiff(self, netconf_dir):
        configA = Config(netconf_dir)
        configA.setNetwork(NETWORK, NETWORK_ATTRIBUTES)
        configA.setBonding(BONDING, BONDING_ATTRIBUTES)
        configA.set_device(DEVICE, DEVICE_ATTRIBUTES)

        configB = Config(netconf_dir)
        configB.setNetwork(NETWORK, NETWORK_ATTRIBUTES)
        configB.setBonding(BONDING, BONDING_ATTRIBUTES)
        configB.set_device(DEVICE, DEVICE_ATTRIBUTES)

        diff = configA.diffFrom(configB)
        assert diff.networks == {}
        assert diff.bonds == {}
        assert diff.devices == {}

        EVIL_NETWORK = 'jarjar'
        EVIL_BONDING_ATTRIBUTES = {'options': 'mode=3', 'nics': ['eth3']}
        EVIL_DEVICE = 'devdev'
        configB.setNetwork(EVIL_NETWORK, NETWORK_ATTRIBUTES)
        configB.setBonding(BONDING, EVIL_BONDING_ATTRIBUTES)
        configB.set_device(EVIL_DEVICE, DEVICE_ATTRIBUTES)

        diff = configA.diffFrom(configB)
        assert diff.networks[EVIL_NETWORK] == {'remove': True}
        assert diff.bonds[BONDING] == BONDING_ATTRIBUTES
        # Devices diff is not yet supported.
        assert diff.devices == {}

        configB.removeNetwork(NETWORK)
        diff = configA.diffFrom(configB)
        assert NETWORK in diff.networks


@pytest.fixture
def config(netconf_dir):
    config = Config(netconf_dir)
    yield config
    config.delete()
    assert not os.path.exists(netconf_dir)


@pytest.fixture
def groups_paths(netconf_dir):
    return (
        os.path.join(netconf_dir, NETCONF_NETS, NETWORK),
        os.path.join(netconf_dir, NETCONF_BONDS, BONDING),
        os.path.join(netconf_dir, NETCONF_DEVS, DEVICE),
    )


class TestTransaction(object):
    def test_successful_setup(self, config, groups_paths):
        with Transaction(config=config) as _config:
            _config.setNetwork(NETWORK, NETWORK_ATTRIBUTES)
            _config.setBonding(BONDING, BONDING_ATTRIBUTES)
            _config.set_device(DEVICE, DEVICE_ATTRIBUTES)

        net_path, bond_path, dev_path = groups_paths
        assert os.path.exists(net_path)
        assert os.path.exists(bond_path)
        assert os.path.exists(dev_path)

    def test_successful_non_persistent_setup(self, config, groups_paths):
        with Transaction(config=config, persistent=False) as _config:
            _config.setNetwork(NETWORK, NETWORK_ATTRIBUTES)
            _config.setBonding(BONDING, BONDING_ATTRIBUTES)
            _config.set_device(DEVICE, DEVICE_ATTRIBUTES)

        net_path, bond_path, dev_path = groups_paths
        assert not os.path.exists(net_path)
        assert not os.path.exists(bond_path)
        assert not os.path.exists(dev_path)

    def test_failed_setup(self, config, groups_paths):
        with pytest.raises(ne.RollbackIncomplete) as roi:
            with Transaction(config=config) as _config:
                _config.setNetwork(NETWORK, NETWORK_ATTRIBUTES)
                _config.setBonding(BONDING, BONDING_ATTRIBUTES)
                _config.set_device(DEVICE, DEVICE_ATTRIBUTES)
                raise FailTest()

        diff = roi.value.diff
        assert diff.networks[NETWORK] == {'remove': True}
        assert diff.bonds[BONDING] == {'remove': True}
        assert diff.devices == {}
        assert roi.value.exc_type == FailTest
        net_path, bond_path, dev_path = groups_paths
        assert not os.path.exists(net_path)
        assert not os.path.exists(bond_path)
        assert not os.path.exists(dev_path)

    def test_failed_setup_with_no_diff(self, config):
        with pytest.raises(FailTest):
            with Transaction(config=config):
                raise FailTest()

    def test_failed_setup_in_rollback(self, config, groups_paths):
        with pytest.raises(FailTest):
            with Transaction(config=config, in_rollback=True) as _config:
                _config.setNetwork(NETWORK, NETWORK_ATTRIBUTES)
                _config.setBonding(BONDING, BONDING_ATTRIBUTES)
                _config.set_device(DEVICE, DEVICE_ATTRIBUTES)
                raise FailTest()

        net_path, bond_path, dev_path = groups_paths
        assert not os.path.exists(net_path)
        assert not os.path.exists(bond_path)
        assert not os.path.exists(dev_path)
