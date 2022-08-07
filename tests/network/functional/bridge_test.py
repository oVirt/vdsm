#
# Copyright 2017-2022 Red Hat, Inc.
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
from network.nettestlib import running_on_ovirt_ci


NETWORK_NAME = 'test-network'


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


class TestBridge(object):
    DEFAULT_STP_OPTS = (
        'forward_delay=1500 hello_time=200 max_age=2000 priority=32768'
    )
    NON_DEFAULT_STP_OPTS = (
        'forward_delay=1600 hello_time=300 max_age=3000 priority=1'
    )

    @nftestlib.parametrize_switch
    @pytest.mark.parametrize(argnames='stp_state', argvalues=[True, False])
    def test_add_bridge_with_stp(self, adapter, switch, nic0, stp_state):
        if switch == 'ovs':
            pytest.xfail('stp is currently not implemented for ovs')

        NETCREATE = {
            NETWORK_NAME: {'nic': nic0, 'switch': switch, 'stp': stp_state}
        }
        with adapter.setupNetworks(NETCREATE, {}, nftestlib.NOCHK):
            adapter.assertNetworkExists(NETWORK_NAME)
            adapter.assertNetworkBridged(NETWORK_NAME)
            adapter.assertBridgeOpts(NETWORK_NAME, NETCREATE[NETWORK_NAME])

    @pytest.mark.parametrize(
        argnames='stp_opts',
        argvalues=[
            {'stp': False, 'opts': DEFAULT_STP_OPTS},
            {'stp': True, 'opts': DEFAULT_STP_OPTS},
            {'stp': True, 'opts': NON_DEFAULT_STP_OPTS},
        ],
    )
    @nftestlib.parametrize_legacy_switch
    def test_add_bridge_stp_with_opts(self, adapter, switch, nic0, stp_opts):
        net_attrs = self._net_attrs_with_bridge_opts(
            switch, nic0, stp_opts['opts']
        )
        net_attrs['stp'] = stp_opts['stp']
        NETCREATE = {NETWORK_NAME: net_attrs}
        with adapter.setupNetworks(NETCREATE, {}, nftestlib.NOCHK):
            adapter.assertBridgeOpts(NETWORK_NAME, net_attrs)

    @nftestlib.parametrize_legacy_switch
    def test_add_bridge_with_custom_opts(self, adapter, switch, nic0):
        opts = 'multicast_snooping=0 multicast_router=0'
        net_attrs = self._net_attrs_with_bridge_opts(switch, nic0, opts)
        NETCREATE = {NETWORK_NAME: net_attrs}
        with adapter.setupNetworks(NETCREATE, {}, nftestlib.NOCHK):
            adapter.assertBridgeOpts(NETWORK_NAME, net_attrs)

    def _net_attrs_with_bridge_opts(self, switch, nic0, bridge_opts):
        return {
            'nic': nic0,
            'switch': switch,
            'custom': {'bridge_opts': bridge_opts},
        }

    @pytest.mark.xfail(
        reason='Unstable on oVirt CI',
        strict=False,
        condition=running_on_ovirt_ci(),
    )
    @nftestlib.parametrize_legacy_switch
    def test_create_network_over_an_existing_unowned_bridge(
        self, adapter, switch, nic0
    ):
        with _create_linux_bridge(NETWORK_NAME) as brname:
            NETCREATE = {
                brname: {'bridged': True, 'nic': nic0, 'switch': switch}
            }
            with adapter.setupNetworks(NETCREATE, {}, nftestlib.NOCHK):
                adapter.assertNetwork(brname, NETCREATE[brname])

    @pytest.mark.xfail(
        reason='Unstable link on oVirt CI',
        raises=nftestlib.UnexpectedLinkStateChangeError,
        strict=False,
        condition=running_on_ovirt_ci(),
    )
    @nftestlib.parametrize_legacy_switch
    def test_create_network_and_reuse_existing_owned_bridge(
        self, adapter, switch, nic0, nic1, hidden_nic
    ):
        NETSETUP1 = {NETWORK_NAME: {'nic': nic0, 'switch': switch}}
        NETSETUP2 = {NETWORK_NAME: {'nic': nic1, 'switch': switch}}
        with adapter.setupNetworks(NETSETUP1, {}, nftestlib.NOCHK):
            nftestlib.attach_dev_to_bridge(hidden_nic, NETWORK_NAME)
            with nftestlib.monitor_stable_link_state(NETWORK_NAME):
                adapter.setupNetworks(NETSETUP2, {}, nftestlib.NOCHK)
                adapter.assertNetwork(NETWORK_NAME, NETSETUP2[NETWORK_NAME])

    @nftestlib.parametrize_legacy_switch
    def test_reconfigure_bridge_with_vanished_port(
        self, adapter, switch, nic0
    ):
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
