#
# Copyright 2016-2017 Red Hat, Inc.
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

import pytest

from vdsm.network import errors as ne

from . import netfunctestlib as nftestlib
from .netfunctestlib import NetFuncTestCase, NOCHK, SetupNetworksError
from network.nettestlib import dummy_devices

BOND_NAME = 'bond1'


@nftestlib.parametrize_switch
class TestBondBasic(NetFuncTestCase):

    def test_add_bond_with_two_nics(self, switch):
        with dummy_devices(2) as (nic1, nic2):
            BONDCREATE = {
                BOND_NAME: {'nics': [nic1, nic2], 'switch': switch}}

            with self.setupNetworks({}, BONDCREATE, NOCHK):
                self.assertBond(BOND_NAME, BONDCREATE[BOND_NAME])

    def test_add_bond_with_two_nics_and_options(self, switch):
        with dummy_devices(2) as (nic1, nic2):
            BONDCREATE = {BOND_NAME: {
                'nics': [nic1, nic2], 'options': 'mode=3 miimon=150',
                'switch': switch}}

            with self.setupNetworks({}, BONDCREATE, NOCHK):
                self.assertBond(BOND_NAME, BONDCREATE[BOND_NAME])

    def test_remove_bond(self, switch):
        with dummy_devices(2) as (nic1, nic2):
            BONDCREATE = {
                BOND_NAME: {'nics': [nic1, nic2], 'switch': switch}}
            BONDREMOVE = {BOND_NAME: {'remove': True}}

            with self.setupNetworks({}, BONDCREATE, NOCHK):
                self.setupNetworks({}, BONDREMOVE, NOCHK)
                self.assertNoBond(BOND_NAME)

    def test_change_bond_slaves(self, switch):
        with dummy_devices(3) as (nic1, nic2, nic3):
            BONDCREATE = {
                BOND_NAME: {'nics': [nic1, nic2], 'switch': switch}}
            BONDEDIT = {
                BOND_NAME: {'nics': [nic1, nic3], 'switch': switch}}

            with self.setupNetworks({}, BONDCREATE, NOCHK):
                with nftestlib.monitor_stable_link_state(BOND_NAME):
                    self.setupNetworks({}, BONDEDIT, NOCHK)
                self.assertBond(BOND_NAME, BONDEDIT[BOND_NAME])

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
            with self.setupNetworks({}, BONDCREATE, NOCHK):
                self.setupNetworks({}, BONDEDIT, NOCHK)
                self.assertBond(BOND1, BONDEDIT[BOND1])
                self.assertBond(BOND2, BONDEDIT[BOND2])

    def test_resize_bond(self, switch):
        with dummy_devices(4) as (nic1, nic2, nic3, nic4):
            bond = {BOND_NAME: {'nics': [nic1, nic2],
                                'switch': switch}}

            with self.setupNetworks({}, bond, NOCHK):
                bond[BOND_NAME]['nics'] += [nic3, nic4]
                self.setupNetworks({}, bond, NOCHK)
                self.assertBond(BOND_NAME, bond[BOND_NAME])

                bond[BOND_NAME]['nics'].remove(nic4)
                self.setupNetworks({}, bond, NOCHK)
                self.assertBond(BOND_NAME, bond[BOND_NAME])

    def test_add_bond_with_bad_name_fails(self, switch):
        INVALID_BOND_NAMES = ('bond',
                              'bonda',
                              'bond0a',
                              'jamesbond007')

        with dummy_devices(2) as (nic1, nic2):
            for bond_name in INVALID_BOND_NAMES:
                BONDCREATE = {bond_name: {'nics': [nic1, nic2],
                                          'switch': switch}}
                with pytest.raises(SetupNetworksError) as cm:
                    with self.setupNetworks({}, BONDCREATE, NOCHK):
                        pass
                assert cm.value.status == ne.ERR_BAD_BONDING

    def test_add_bond_with_no_nics_fails(self, switch):
        BONDCREATE = {BOND_NAME: {'nics': [], 'switch': switch}}

        with pytest.raises(SetupNetworksError) as err:
            with self.setupNetworks({}, BONDCREATE, NOCHK):
                pass
        assert err.value.status == ne.ERR_BAD_PARAMS


@nftestlib.parametrize_switch
class TestBondOptions(NetFuncTestCase):

    def test_bond_mode_1(self, switch):
        with dummy_devices(2) as (nic1, nic2):
            BONDCREATE = {BOND_NAME: {
                'nics': [nic1, nic2],
                'options': 'mode=1 primary=' + nic1,
                'switch': switch}}

            with self.setupNetworks({}, BONDCREATE, NOCHK):
                self.assertBond(BOND_NAME, BONDCREATE[BOND_NAME])

    def test_bond_mode_change(self, switch):
        with dummy_devices(2) as nics:
            BONDCREATE = {BOND_NAME: {'nics': nics,
                                      'switch': switch,
                                      'options': 'mode=1 miimon=150'}}
            BONDEDIT = {BOND_NAME: {'nics': nics,
                                    'switch': switch,
                                    'options': 'mode=3'}}
            with self.setupNetworks({}, BONDCREATE, NOCHK):
                self.setupNetworks({}, BONDEDIT, NOCHK)
                self.assertBond(BOND_NAME, BONDEDIT[BOND_NAME])

    def test_bond_options_with_the_mode_specified_last(self, switch):
        with dummy_devices(2) as (nic1, nic2):
            BONDCREATE = {BOND_NAME: {
                'nics': [nic1, nic2],
                'options': 'lacp_rate=fast mode=802.3ad',
                'switch': switch}}

            with self.setupNetworks({}, BONDCREATE, NOCHK):
                self.assertBond(BOND_NAME, BONDCREATE[BOND_NAME])

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
            with self.setupNetworks({}, BONDCREATE, NOCHK):
                self.setupNetworks({}, BONDEDIT, NOCHK)
                self.assertBond(BOND_NAME, BONDEDIT[BOND_NAME])
