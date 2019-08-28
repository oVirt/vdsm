#
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
#

from __future__ import absolute_import
from __future__ import division

from contextlib import contextmanager

import six

import pytest

from vdsm.network import errors as ne
from vdsm.network import nmstate

from . import netfunctestlib as nftestlib
from .netfunctestlib import NetFuncTestAdapter, NOCHK, SetupNetworksError
from network.nettestlib import dummy_devices, veth_pair

BOND_NAME = 'bond1_name'
NETWORK_NAME = 'test-network'
NETWORK2_NAME = 'test-network2'
NETWORK3_NAME = 'test-network3'

adapter = None


@pytest.fixture(scope='module', autouse=True)
def create_adapter(target):
    global adapter
    adapter = NetFuncTestAdapter(target)


@pytest.mark.nmstate
@nftestlib.parametrize_switch
class TestBondBasic(object):

    def test_add_bond_with_two_nics(self, switch):
        with dummy_devices(2) as (nic1, nic2):
            BONDCREATE = {
                BOND_NAME: {'nics': [nic1, nic2], 'switch': switch}}

            with adapter.setupNetworks({}, BONDCREATE, NOCHK):
                adapter.assertBond(BOND_NAME, BONDCREATE[BOND_NAME])

    def test_add_bond_with_two_nics_and_options(self, switch):
        with dummy_devices(2) as (nic1, nic2):
            BONDCREATE = {BOND_NAME: {
                'nics': [nic1, nic2], 'options': 'mode=3 miimon=150',
                'switch': switch}}

            with adapter.setupNetworks({}, BONDCREATE, NOCHK):
                adapter.assertBond(BOND_NAME, BONDCREATE[BOND_NAME])

    def test_remove_bond(self, switch):
        with dummy_devices(2) as (nic1, nic2):
            BONDCREATE = {
                BOND_NAME: {'nics': [nic1, nic2], 'switch': switch}}
            BONDREMOVE = {BOND_NAME: {'remove': True}}

            with adapter.setupNetworks({}, BONDCREATE, NOCHK):
                adapter.setupNetworks({}, BONDREMOVE, NOCHK)
                adapter.assertNoBond(BOND_NAME)

    @pytest.mark.xfail(condition=nmstate.is_nmstate_backend(),
                       reason='Links stability not supported by nmstate/NM',
                       raises=nftestlib.UnexpectedLinkStateChangeError,
                       strict=True)
    def test_change_bond_slaves(self, switch):
        with dummy_devices(3) as (nic1, nic2, nic3):
            BONDCREATE = {
                BOND_NAME: {'nics': [nic1, nic2], 'switch': switch}}
            BONDEDIT = {
                BOND_NAME: {'nics': [nic1, nic3], 'switch': switch}}

            with adapter.setupNetworks({}, BONDCREATE, NOCHK):
                with nftestlib.monitor_stable_link_state(BOND_NAME):
                    adapter.setupNetworks({}, BONDEDIT, NOCHK)
                    adapter.assertBond(BOND_NAME, BONDEDIT[BOND_NAME])

    def test_swap_slaves_between_bonds(self, switch):
        BOND1 = BOND_NAME + '1'
        BOND2 = BOND_NAME + '2'

        with dummy_devices(4) as (nic1, nic2, nic3, nic4):
            BONDCREATE = {
                BOND1: {'nics': [nic1, nic2], 'switch': switch},
                BOND2: {'nics': [nic3, nic4], 'switch': switch}}
            BONDEDIT = {
                BOND1: {'nics': [nic1, nic3], 'switch': switch},
                BOND2: {'nics': [nic2, nic4], 'switch': switch}}
            with adapter.setupNetworks({}, BONDCREATE, NOCHK):
                adapter.setupNetworks({}, BONDEDIT, NOCHK)
                adapter.assertBond(BOND1, BONDEDIT[BOND1])
                adapter.assertBond(BOND2, BONDEDIT[BOND2])

    def test_resize_bond(self, switch):
        with dummy_devices(4) as (nic1, nic2, nic3, nic4):
            bond = {BOND_NAME: {'nics': [nic1, nic2],
                                'switch': switch}}
            with adapter.setupNetworks({}, bond, NOCHK):
                bond[BOND_NAME]['nics'] += [nic3, nic4]
                adapter.setupNetworks({}, bond, NOCHK)
                adapter.assertBond(BOND_NAME, bond[BOND_NAME])

                bond[BOND_NAME]['nics'].remove(nic4)
                adapter.setupNetworks({}, bond, NOCHK)
                adapter.assertBond(BOND_NAME, bond[BOND_NAME])

    def test_add_bond_with_bad_name_fails(self, switch):
        INVALID_BOND_NAMES = ('bond',
                              'bond bad',
                              'jamesbond007')

        with dummy_devices(2) as (nic1, nic2):
            for bond_name in INVALID_BOND_NAMES:
                BONDCREATE = {bond_name: {'nics': [nic1, nic2],
                                          'switch': switch}}
                with pytest.raises(SetupNetworksError) as cm:
                    with adapter.setupNetworks({}, BONDCREATE, NOCHK):
                        pass
                assert cm.value.status == ne.ERR_BAD_BONDING

    def test_add_bond_with_no_nics_fails(self, switch):
        BONDCREATE = {BOND_NAME: {'nics': [], 'switch': switch}}

        with pytest.raises(SetupNetworksError) as err:
            with adapter.setupNetworks({}, BONDCREATE, NOCHK):
                pass
        assert err.value.status == ne.ERR_BAD_PARAMS

    def test_add_bond_with_enforced_mac_address(self, switch):
        if switch == 'ovs':
            pytest.xfail(
                'Bond mac enforcement is currently not implemented for ovs')
        HWADDRESS = 'ce:0c:46:59:c9:d1'
        with dummy_devices(2) as (nic1, nic2):
            BONDCREATE = {
                BOND_NAME: {'nics': [nic1, nic2],
                            'hwaddr': HWADDRESS,
                            'switch': switch}}

            with adapter.setupNetworks({}, BONDCREATE, NOCHK):
                adapter.assertBond(BOND_NAME, BONDCREATE[BOND_NAME])

    def test_bond_slaves_order_does_not_affect_the_mac_address(self, switch):
        with dummy_devices(2) as (nic1, nic2):
            bond1 = {BOND_NAME: {'nics': [nic1, nic2], 'switch': switch}}
            bond2 = {BOND_NAME: {'nics': [nic2, nic1], 'switch': switch}}

            with adapter.setupNetworks({}, bond1, NOCHK):
                bond1_hwaddr = adapter.netinfo.bondings[BOND_NAME]['hwaddr']
            with adapter.setupNetworks({}, bond2, NOCHK):
                bond2_hwaddr = adapter.netinfo.bondings[BOND_NAME]['hwaddr']

            assert bond1_hwaddr == bond2_hwaddr


@pytest.mark.nmstate
@nftestlib.parametrize_switch
class TestBondOptions(object):

    def test_bond_mode_1(self, switch):
        with dummy_devices(2) as (nic1, nic2):
            BONDCREATE = {BOND_NAME: {
                'nics': [nic1, nic2],
                'options': 'mode=1 primary=' + nic1,
                'switch': switch}}

            with adapter.setupNetworks({}, BONDCREATE, NOCHK):
                adapter.assertBond(BOND_NAME, BONDCREATE[BOND_NAME])

    def test_bond_active_slave_report(self, switch):
        with dummy_devices(2) as nics:
            BONDCREATE = {BOND_NAME: {'nics': nics,
                                      'switch': switch,
                                      'options': 'mode=1'}}
            BONDEDIT = {BOND_NAME: {'nics': nics,
                                    'switch': switch,
                                    'options': 'mode=4'}}
            with adapter.setupNetworks({}, BONDCREATE, NOCHK):
                adapter.assertBondActiveSlaveExists(BOND_NAME, nics)
                adapter.setupNetworks({}, BONDEDIT, NOCHK)
                adapter.assertBondNoActiveSlaveExists(BOND_NAME)

    def test_bond_mode_change(self, switch):
        with dummy_devices(2) as nics:
            BONDCREATE = {BOND_NAME: {'nics': nics,
                                      'switch': switch,
                                      'options': 'mode=1 miimon=150'}}
            BONDEDIT = {BOND_NAME: {'nics': nics,
                                    'switch': switch,
                                    'options': 'mode=3'}}
            with adapter.setupNetworks({}, BONDCREATE, NOCHK):
                adapter.setupNetworks({}, BONDEDIT, NOCHK)
                adapter.assertBond(BOND_NAME, BONDEDIT[BOND_NAME])

    def test_bond_options_with_the_mode_specified_last(self, switch):
        with dummy_devices(2) as (nic1, nic2):
            BONDCREATE = {BOND_NAME: {
                'nics': [nic1, nic2],
                'options': 'lacp_rate=fast mode=802.3ad',
                'switch': switch}}

            with adapter.setupNetworks({}, BONDCREATE, NOCHK):
                adapter.assertBond(BOND_NAME, BONDCREATE[BOND_NAME])

    def test_bond_arp_ip_target_change(self, switch):
        with dummy_devices(2) as nics:
            create_options = ('mode=1 arp_interval=1000 '
                              'arp_ip_target=192.168.122.1')
            BONDCREATE = {BOND_NAME: {'nics': nics,
                                      'switch': switch,
                                      'options': create_options}}
            edit_options = ('mode=1 arp_interval=1000 '
                            'arp_ip_target=10.1.3.1,10.1.2.1')
            BONDEDIT = {BOND_NAME: {'nics': nics,
                                    'switch': switch,
                                    'options': edit_options}}
            with adapter.setupNetworks({}, BONDCREATE, NOCHK):
                adapter.setupNetworks({}, BONDEDIT, NOCHK)
                adapter.assertBond(BOND_NAME, BONDEDIT[BOND_NAME])

    def test_bond_mode4_caps_aggregator_id(self, switch):
        with two_connected_pair_of_bond_slaves() as (
                bond0_slaves, bond1_slaves):
            nics = bond0_slaves + bond1_slaves
            BONDCREATE = {
                BOND_NAME + '0': {
                    'nics': bond0_slaves,
                    'options': 'mode=4 lacp_rate=1',
                    'switch': switch},
                BOND_NAME + '1': {
                    'nics': bond1_slaves,
                    'options': 'mode=4 lacp_rate=1',
                    'switch': switch
                }}
            bond1, bond2 = BONDCREATE
            with adapter.setupNetworks({}, BONDCREATE, NOCHK):
                nftestlib.wait_bonds_lp_interval()
                adapter.refresh_netinfo()

                adapter.assertLACPConfigured(BONDCREATE, nics)
                adapter.assertBondHwaddrToPartnerMac(bond1, bond2)
                adapter.assertBondHwaddrToPartnerMac(bond2, bond1)

    def test_bond_mode0_no_lacp_configuration(self, switch):
        with two_connected_pair_of_bond_slaves() as (
                bond0_slaves, bond1_slaves):
            nics = bond0_slaves + bond1_slaves
            BONDCREATE = {
                BOND_NAME + '0': {
                    'nics': bond0_slaves,
                    'options': 'mode=0',
                    'switch': switch},
                BOND_NAME + '1': {
                    'nics': bond1_slaves,
                    'options': 'mode=0',
                    'switch': switch
                }}
            with adapter.setupNetworks({}, BONDCREATE, NOCHK):
                nftestlib.wait_bonds_lp_interval()
                for bond_name, bond_options in six.viewitems(BONDCREATE):
                    adapter.assertBond(bond_name, bond_options)
                adapter.assertNoLACPConfigured(BONDCREATE, nics)


@contextmanager
def two_connected_pair_of_bond_slaves():
    with veth_pair() as (n1, n2), veth_pair() as (n3, n4):
        yield [n1, n3], [n2, n4]
