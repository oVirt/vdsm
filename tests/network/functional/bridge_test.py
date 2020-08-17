#
# Copyright 2017-2020 Red Hat, Inc.
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

from contextlib import contextmanager

import pytest

from vdsm.network.cmd import exec_sync

from . import netfunctestlib as nftestlib
from network.nettestlib import dummy_device


NETWORK_NAME = 'test-network'


adapter = None


@pytest.fixture(scope='module', autouse=True)
def create_adapter(target):
    global adapter
    adapter = nftestlib.NetFuncTestAdapter(target)


@pytest.fixture
def nic0():
    with dummy_device() as nic:
        yield nic


@pytest.fixture
def nic1():
    with dummy_device() as nic:
        yield nic


@pytest.fixture
def hidden_nic():
    # This nic is not visible to refresh caps
    with dummy_device(prefix='_dummy') as nic:
        yield nic


@pytest.mark.nmstate
class TestBridge(object):
    @nftestlib.parametrize_switch
    def test_add_bridge_with_stp(self, switch, nic0):
        if switch == 'ovs':
            pytest.xfail('stp is currently not implemented for ovs')

            NETCREATE = {
                NETWORK_NAME: {'nic': nic0, 'switch': switch, 'stp': True}
            }
            with adapter.setupNetworks(NETCREATE, {}, nftestlib.NOCHK):
                adapter.assertNetworkExists(NETWORK_NAME)
                adapter.assertNetworkBridged(NETWORK_NAME)
                adapter.assertBridgeOpts(NETWORK_NAME, NETCREATE[NETWORK_NAME])

    @nftestlib.parametrize_legacy_switch
    def test_add_bridge_with_custom_opts(self, switch, nic0):
        NET_ATTRS = {
            'nic': nic0,
            'switch': switch,
            'custom': {
                'bridge_opts': 'multicast_snooping=0 multicast_router=0'
            },
        }
        NETCREATE = {NETWORK_NAME: NET_ATTRS}
        with adapter.setupNetworks(NETCREATE, {}, nftestlib.NOCHK):
            adapter.assertBridgeOpts(NETWORK_NAME, NET_ATTRS)

    @nftestlib.parametrize_legacy_switch
    def test_create_network_over_an_existing_unowned_bridge(
        self, switch, nic0
    ):
        with _create_linux_bridge(NETWORK_NAME) as brname:
            NETCREATE = {
                brname: {'bridged': True, 'nic': nic0, 'switch': switch}
            }
            with adapter.setupNetworks(NETCREATE, {}, nftestlib.NOCHK):
                adapter.assertNetwork(brname, NETCREATE[brname])

    @pytest.mark.xfail(
        reason='Unstable link while NM is running (BZ#1498022) '
        'and on CI even with NM down',
        raises=nftestlib.UnexpectedLinkStateChangeError,
        strict=False,
        condition=not nftestlib.is_nmstate_backend(),
    )
    @nftestlib.parametrize_legacy_switch
    def test_create_network_and_reuse_existing_owned_bridge(
        self, switch, nic0, nic1, hidden_nic
    ):
        NETSETUP1 = {NETWORK_NAME: {'nic': nic0, 'switch': switch}}
        NETSETUP2 = {NETWORK_NAME: {'nic': nic1, 'switch': switch}}
        with adapter.setupNetworks(NETSETUP1, {}, nftestlib.NOCHK):
            nftestlib.attach_dev_to_bridge(hidden_nic, NETWORK_NAME)
            with nftestlib.monitor_stable_link_state(NETWORK_NAME):
                adapter.setupNetworks(NETSETUP2, {}, nftestlib.NOCHK)
                adapter.assertNetwork(NETWORK_NAME, NETSETUP2[NETWORK_NAME])

    @nftestlib.parametrize_legacy_switch
    def test_reconfigure_bridge_with_vanished_port(self, switch, nic0):
        NETCREATE = {
            NETWORK_NAME: {'nic': nic0, 'bridged': True, 'switch': switch}
        }
        with adapter.setupNetworks(NETCREATE, {}, nftestlib.NOCHK):
            with dummy_device() as nic1:
                NETCREATE[NETWORK_NAME]['nic'] = nic1
                adapter.setupNetworks(NETCREATE, {}, nftestlib.NOCHK)

            adapter.refresh_netinfo()
            assert adapter.netinfo.networks[NETWORK_NAME]['ports'] == []

            NETCREATE[NETWORK_NAME]['nic'] = nic0
            adapter.setupNetworks(NETCREATE, {}, nftestlib.NOCHK)

            net_ports = adapter.netinfo.networks[NETWORK_NAME]['ports']
            assert net_ports == [nic0]


@contextmanager
def _create_linux_bridge(brname):
    rc, _, err = exec_sync(['ip', 'link', 'add', brname, 'type', 'bridge'])
    if rc != 0:
        pytest.fail('Unable to create bridge. err: {}'.format(err))
    try:
        yield brname
    finally:
        exec_sync(['ip', 'link', 'del', brname])
