#
# Copyright 2017 Red Hat, Inc.
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

from contextlib import contextmanager

import pytest

from network import nmnettestlib
from vdsm.network.cmd import exec_sync
from vdsm.network.link.iface import iface
from vdsm.network.netlink import waitfor

from . import netfunctestlib as nftestlib
from network.nettestlib import dummy_devices


NETWORK_NAME = 'test-network'


class TestBridge(nftestlib.NetFuncTestCase):
    @nftestlib.parametrize_switch
    def test_add_bridge_with_stp(self, switch):
        if switch == 'ovs':
            pytest.xfail('stp is currently not implemented for ovs')

        with dummy_devices(1) as (nic,):
            NETCREATE = {NETWORK_NAME: {'nic': nic,
                                        'switch': switch,
                                        'stp': True}}
            with self.setupNetworks(NETCREATE, {}, nftestlib.NOCHK):
                self.assertNetworkExists(NETWORK_NAME)
                self.assertNetworkBridged(NETWORK_NAME)
                self.assertBridgeOpts(NETWORK_NAME, NETCREATE[NETWORK_NAME])

    @pytest.mark.parametrize('switch', [pytest.mark.legacy_switch('legacy')])
    def test_add_bridge_with_custom_opts(self, switch):
        with dummy_devices(1) as (nic,):
            NETCREATE = {NETWORK_NAME: {
                'nic': nic,
                'switch': switch,
                'custom': {
                    'bridge_opts': 'multicast_snooping=0 multicast_router=0'}}}
            with self.setupNetworks(NETCREATE, {}, nftestlib.NOCHK):
                self.assertBridgeOpts(NETWORK_NAME, NETCREATE[NETWORK_NAME])

    @pytest.mark.parametrize('switch', [pytest.mark.legacy_switch('legacy')])
    def test_create_network_over_an_existing_unowned_bridge(self, switch):
        with _create_linux_bridge(NETWORK_NAME) as brname:
            NETCREATE = {brname: {'bridged': True, 'switch': switch}}
            with self.setupNetworks(NETCREATE, {}, nftestlib.NOCHK):
                self.assertNetwork(brname, NETCREATE[brname])

    @pytest.mark.skipif(nmnettestlib.is_networkmanager_running(),
                        reason='Unstable while NM is running (BZ#1498022)')
    @pytest.mark.parametrize('switch', [pytest.mark.legacy_switch('legacy')])
    def test_create_network_and_reuse_existing_owned_bridge(self, switch):
        with dummy_devices(2) as (nic1, nic2):
            NETSETUP1 = {NETWORK_NAME: {'nic': nic1, 'switch': switch}}
            NETSETUP2 = {NETWORK_NAME: {'nic': nic2, 'switch': switch}}
            with self.setupNetworks(NETSETUP1, {}, nftestlib.NOCHK):
                with _create_tap() as tapdev:
                    _attach_dev_to_bridge(tapdev, NETWORK_NAME)
                    with waitfor.waitfor_linkup(NETWORK_NAME):
                        pass
                    with nftestlib.monitor_stable_link_state(NETWORK_NAME):
                        self.setupNetworks(NETSETUP2, {}, nftestlib.NOCHK)
                        self.assertNetwork(NETWORK_NAME,
                                           NETSETUP2[NETWORK_NAME])


def _attach_dev_to_bridge(tapdev, bridge):
    rc, _, err = exec_sync(['ip', 'link', 'set', tapdev, 'master', bridge])
    if rc != 0:
        pytest.fail(
            'Filed to add {} to {}. err: {}'.format(tapdev, bridge, err))


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
