# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

import contextlib
import json
import os
import tempfile

import pytest

from vdsm.common import constants
from vdsm.common import fileutils

from . import netfunctestlib as nftestlib
from .netfunctestlib import NOCHK
from network.nettestlib import dummy_device


NETWORK_NAME = 'test-network'
ENABLE_BRIDGE_HOOK = f"""\
#!/usr/bin/python3
import hooking
from vdsm.network import canonicalize

network_config = hooking.read_json()

network = network_config['request']['networks']['{NETWORK_NAME}']
network['bridged'] = True

# refresh the defaults for bridged network
canonicalize.canonicalize_networks(network_config['request']['networks'])

hooking.write_json(network_config)
"""


@pytest.fixture(scope='module', autouse=True)
def create_hooks_dir():
    created_root_dir = None
    dir_path = constants.P_VDSM_HOOKS
    while not os.path.isdir(dir_path):
        created_root_dir = dir_path
        dir_path, _ = os.path.split(dir_path)

    if created_root_dir:
        os.makedirs(constants.P_VDSM_HOOKS)

    yield

    if created_root_dir:
        fileutils.rm_tree(created_root_dir)


@pytest.fixture
def before_network_setup_hook():
    with _create_hook_file(
        'before_network_setup',
        'test_before_network_setup.py',
        ENABLE_BRIDGE_HOOK,
    ):
        yield


@pytest.fixture
def after_network_setup_hook():
    _, cookie_file = tempfile.mkstemp()
    with _create_hook_file(
        'after_network_setup',
        'test_after_network_setup.py',
        '#!/bin/sh\n' f'cat $_hook_json > {cookie_file}\n',
    ):
        yield cookie_file

    fileutils.rm_file(cookie_file)


@contextlib.contextmanager
def _create_hook_file(dir_name, hook_name, hook_script):
    with _create_hook_dir(dir_name) as dir_path:
        hook_path = os.path.join(dir_path, hook_name)
        with open(hook_path, 'w') as f:
            f.write(hook_script)
        os.chmod(hook_path, 0o777)

        yield

        fileutils.rm_file(hook_path)


@contextlib.contextmanager
def _create_hook_dir(dir_name):
    dir_path = os.path.join(constants.P_VDSM_HOOKS, dir_name)
    dir_existed = os.path.isdir(dir_path)

    if not dir_existed:
        os.mkdir(dir_path)

    yield dir_path

    if not dir_existed:
        fileutils.rm_tree(dir_path)


@pytest.fixture
def bridgeless_network(switch):
    with dummy_device() as nic:
        NETCREATE = {
            NETWORK_NAME: {'nic': nic, 'bridged': False, 'switch': switch}
        }
        yield NETCREATE


@nftestlib.parametrize_switch
class TestNetworkSetupHook(object):
    def test_before_network_setup_hook_enables_bridged(
        self, adapter, before_network_setup_hook, bridgeless_network
    ):
        with adapter.setupNetworks(bridgeless_network, {}, NOCHK):
            bridgeless_network[NETWORK_NAME]['bridged'] = True
            adapter.assertNetwork(
                NETWORK_NAME, bridgeless_network[NETWORK_NAME]
            )

    def test_after_network_setup_hook(
        self, adapter, after_network_setup_hook, bridgeless_network
    ):
        with adapter.setupNetworks(bridgeless_network, {}, NOCHK):
            pass

        assert os.path.isfile(after_network_setup_hook)

        with open(after_network_setup_hook, 'r') as cookie_file:
            network_config = json.load(cookie_file)
            assert 'networks' in network_config['request']
            assert 'bondings' in network_config['request']
            assert NETWORK_NAME in network_config['request']['networks']
