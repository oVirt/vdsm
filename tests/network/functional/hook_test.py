#
# Copyright 2020 Red Hat, Inc.
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

import contextlib
import os

import pytest

from vdsm.common import constants
from vdsm.common import fileutils

from . import netfunctestlib as nftestlib
from .netfunctestlib import NetFuncTestAdapter, NOCHK
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

adapter = None


@pytest.fixture(scope='module', autouse=True)
def create_adapter(target):
    global adapter
    adapter = NetFuncTestAdapter(target)


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


@pytest.mark.nmstate
@nftestlib.parametrize_switch
class TestNetworkSetupHook(object):
    def test_before_network_setup_hook_enables_bridged(
        self, before_network_setup_hook, bridgeless_network
    ):
        with adapter.setupNetworks(bridgeless_network, {}, NOCHK):
            bridgeless_network[NETWORK_NAME]['bridged'] = True
            adapter.assertNetwork(
                NETWORK_NAME, bridgeless_network[NETWORK_NAME]
            )
