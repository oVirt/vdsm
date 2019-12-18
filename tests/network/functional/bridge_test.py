#
# Copyright 2017-2018 Red Hat, Inc.
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

from contextlib import contextmanager

import pytest

from vdsm.network.cmd import exec_sync
from vdsm.network.link.iface import iface

from . import netfunctestlib as nftestlib
from network.nettestlib import dummy_devices, dummy_device, running_on_fedora


NETWORK_NAME = 'test-network'


adapter = None


@pytest.fixture(scope='module', autouse=True)
def create_adapter(target):
    global adapter
    adapter = nftestlib.NetFuncTestAdapter(target)


class TestBridge(object):
    @pytest.mark.nmstate
    @nftestlib.parametrize_switch
    def test_add_bridge_with_stp(self, switch):
        if switch == 'ovs':
            pytest.xfail('stp is currently not implemented for ovs')
        if switch == 'legacy' and running_on_fedora(29):
            pytest.xfail('Fails on Fedora 29')

        with dummy_devices(1) as (nic,):
            NETCREATE = {
                NETWORK_NAME: {'nic': nic, 'switch': switch, 'stp': True}
            }
            with adapter.setupNetworks(NETCREATE, {}, nftestlib.NOCHK):
                adapter.assertNetworkExists(NETWORK_NAME)
                adapter.assertNetworkBridged(NETWORK_NAME)
                adapter.assertBridgeOpts(NETWORK_NAME, NETCREATE[NETWORK_NAME])

    @nftestlib.parametrize_legacy_switch
    def test_add_bridge_with_custom_opts(self, switch):
        with dummy_devices(1) as (nic,):
            NET_ATTRS = {
                'nic': nic,
                'switch': switch,
                'custom': {
                    'bridge_opts': 'multicast_snooping=0 multicast_router=0'
                },
            }
            NETCREATE = {NETWORK_NAME: NET_ATTRS}
            with adapter.setupNetworks(NETCREATE, {}, nftestlib.NOCHK):
                adapter.assertBridgeOpts(NETWORK_NAME, NET_ATTRS)

    @pytest.mark.nmstate
    @nftestlib.parametrize_legacy_switch
    def test_create_network_over_an_existing_unowned_bridge(self, switch):
        with _create_linux_bridge(NETWORK_NAME) as brname:
            with dummy_devices(1) as (nic,):
                NETCREATE = {
                    brname: {'bridged': True, 'nic': nic, 'switch': switch}
                }
                with adapter.setupNetworks(NETCREATE, {}, nftestlib.NOCHK):
                    adapter.assertNetwork(brname, NETCREATE[brname])

    @pytest.mark.xfail(
        reason='Unstable link while NM is running (BZ#1498022) '
        'and on CI even with NM down',
        raises=nftestlib.UnexpectedLinkStateChangeError,
        strict=False,
    )
    @nftestlib.parametrize_legacy_switch
    def test_create_network_and_reuse_existing_owned_bridge(self, switch):
        with dummy_devices(2) as (nic1, nic2):
            NETSETUP1 = {NETWORK_NAME: {'nic': nic1, 'switch': switch}}
            NETSETUP2 = {NETWORK_NAME: {'nic': nic2, 'switch': switch}}
            with adapter.setupNetworks(NETSETUP1, {}, nftestlib.NOCHK):
                with _create_tap() as tapdev:
                    _attach_dev_to_bridge(tapdev, NETWORK_NAME)
                    with nftestlib.monitor_stable_link_state(NETWORK_NAME):
                        adapter.setupNetworks(NETSETUP2, {}, nftestlib.NOCHK)
                        adapter.assertNetwork(
                            NETWORK_NAME, NETSETUP2[NETWORK_NAME]
                        )

    @pytest.mark.nmstate
    @nftestlib.parametrize_legacy_switch
    @pytest.mark.xfail(
        condition=running_on_fedora(29),
        reason='Failing on legacy switch, fedora 29',
        strict=True,
    )
    def test_reconfigure_bridge_with_vanished_port(self, switch):
        with dummy_device() as nic1:
            NETCREATE = {
                NETWORK_NAME: {'nic': nic1, 'bridged': True, 'switch': switch}
            }
            with adapter.setupNetworks(NETCREATE, {}, nftestlib.NOCHK):
                with dummy_device() as nic2:
                    NETCREATE[NETWORK_NAME]['nic'] = nic2
                    adapter.setupNetworks(NETCREATE, {}, nftestlib.NOCHK)

                adapter.refresh_netinfo()
                assert adapter.netinfo.networks[NETWORK_NAME]['ports'] == []

                NETCREATE[NETWORK_NAME]['nic'] = nic1
                adapter.setupNetworks(NETCREATE, {}, nftestlib.NOCHK)

                net_ports = adapter.netinfo.networks[NETWORK_NAME]['ports']
                assert net_ports == [nic1]


def _attach_dev_to_bridge(tapdev, bridge):
    rc, _, err = exec_sync(['ip', 'link', 'set', tapdev, 'master', bridge])
    if rc != 0:
        pytest.fail(
            'Filed to add {} to {}. err: {}'.format(tapdev, bridge, err)
        )


@contextmanager
def _create_linux_bridge(brname):
    rc, _, err = exec_sync(['ip', 'link', 'add', brname, 'type', 'bridge'])
    if rc != 0:
        pytest.fail('Unable to create bridge. err: {}'.format(err))
    try:
        yield brname
    finally:
        exec_sync(['ip', 'link', 'del', brname])


@contextmanager
def _create_tap():
    devname = '_tap99'
    rc, _, err = exec_sync(['ip', 'tuntap', 'add', devname, 'mode', 'tap'])
    if rc != 0:
        pytest.fail('Unable to create tap device. err: {}'.format(err))
    try:
        iface(devname).up()
        yield devname
    finally:
        exec_sync(['ip', 'tuntap', 'del', devname, 'mode', 'tap'])
