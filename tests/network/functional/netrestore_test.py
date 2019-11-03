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
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA  02110-1301 USA
#
# Refer to the README and COPYING files for full details of the license

from __future__ import absolute_import
from __future__ import division

import pytest

from .netfunctestlib import NetFuncTestAdapter, NOCHK
from .netfunctestlib import parametrize_bridged
from .netfunctestlib import parametrize_switch
from network.compat import mock
from network.nettestlib import dummy_devices
from network.nettestlib import veth_pair

from vdsm.network import netrestore
from vdsm.network.ipwrapper import linkSet
from vdsm.network.link.bond import Bond

BOND_NAME = 'bond1'
NETWORK_NAME = 'test-network'


adapter = None


@pytest.fixture(scope='module', autouse=True)
def create_adapter(target):
    global adapter
    adapter = NetFuncTestAdapter(target)


@pytest.mark.ovs_switch
class TestRestoreOvsBond(object):
    @mock.patch.object(netrestore, 'NETS_RESTORED_MARK', 'does/not/exist')
    def test_restore_bond(self):
        with dummy_devices(2) as (nic1, nic2):
            BONDCREATE = {BOND_NAME: {'nics': [nic1, nic2], 'switch': 'ovs'}}

            with adapter.reset_persistent_config():
                with adapter.setupNetworks({}, BONDCREATE, NOCHK):
                    adapter.setSafeNetworkConfig()

                    Bond(BOND_NAME).destroy()

                    netrestore.init_nets()

                    adapter.update_netinfo()
                    adapter.assertBond(BOND_NAME, BONDCREATE[BOND_NAME])


@parametrize_switch
class TestRestore(object):
    @parametrize_bridged
    def test_restore_missing_network_from_config(self, switch, bridged):
        with dummy_devices(1) as (nic,):
            SETUP_NET = {
                NETWORK_NAME: {
                    'nic': nic,
                    'bridged': bridged,
                    'switch': switch,
                }
            }
            REMOVE_NET = {NETWORK_NAME: {'remove': True}}

            with adapter.reset_persistent_config():
                with adapter.setupNetworks(SETUP_NET, {}, NOCHK):
                    adapter.setSafeNetworkConfig()
                    adapter.setupNetworks(REMOVE_NET, {}, NOCHK)

                    adapter.assertNoNetworkExists(NETWORK_NAME)

                    adapter.restore_nets()

                    adapter.assertNetworkExists(NETWORK_NAME)

    def test_restore_dynamic_ipv4_network(self, switch):
        if switch == 'ovs':
            # With OVS, the restoration creates the network without an IP.
            pytest.xfail('Inconsistent behaviour with OVS')

        with veth_pair() as (server, client):
            linkSet(server, ['up'])
            SETUP_NET = {
                NETWORK_NAME: {
                    'nic': client,
                    'bridged': False,
                    'bootproto': 'dhcp',
                    'switch': switch,
                }
            }
            REMOVE_NET = {NETWORK_NAME: {'remove': True}}

            with adapter.reset_persistent_config():
                with adapter.setupNetworks(SETUP_NET, {}, NOCHK):
                    adapter.setSafeNetworkConfig()
                    adapter.setupNetworks(REMOVE_NET, {}, NOCHK)

                    adapter.assertNoNetworkExists(NETWORK_NAME)

                    adapter.restore_nets()

                    # Attempt to restore network without dhcp server.
                    # As expected, restoration occurs with blockingdhcp=True
                    # and therefore it should fail the setup.
                    adapter.assertNoNetworkExists(NETWORK_NAME)
