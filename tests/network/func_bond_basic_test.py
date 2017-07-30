#
# Copyright 2016 Red Hat, Inc.
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

from nose.plugins.attrib import attr

from vdsm.network import errors as ne

from .netfunctestlib import NetFuncTestCase, NOCHK, SetupNetworksError
from .nettestlib import dummy_devices

BOND_NAME = 'bond1'


class BondBasicTemplate(NetFuncTestCase):
    __test__ = False

    def test_add_bond_with_two_nics(self):
        with dummy_devices(2) as (nic1, nic2):
            BONDCREATE = {
                BOND_NAME: {'nics': [nic1, nic2], 'switch': self.switch}}

            with self.setupNetworks({}, BONDCREATE, NOCHK):
                self.assertBond(BOND_NAME, BONDCREATE[BOND_NAME])

    def test_add_bond_with_two_nics_and_options(self):
        with dummy_devices(2) as (nic1, nic2):
            BONDCREATE = {BOND_NAME: {
                'nics': [nic1, nic2], 'options': 'mode=3 miimon=150',
                'switch': self.switch}}

            with self.setupNetworks({}, BONDCREATE, NOCHK):
                self.assertBond(BOND_NAME, BONDCREATE[BOND_NAME])

    def test_remove_bond(self):
        with dummy_devices(2) as (nic1, nic2):
            BONDCREATE = {
                BOND_NAME: {'nics': [nic1, nic2], 'switch': self.switch}}
            BONDREMOVE = {BOND_NAME: {'remove': True}}

            with self.setupNetworks({}, BONDCREATE, NOCHK):
                self.setupNetworks({}, BONDREMOVE, NOCHK)
                self.assertNoBond(BOND_NAME)

    def test_change_bond_slaves(self):
        with dummy_devices(3) as (nic1, nic2, nic3):
            BONDCREATE = {
                BOND_NAME: {'nics': [nic1, nic2], 'switch': self.switch}}
            BONDEDIT = {
                BOND_NAME: {'nics': [nic1, nic3], 'switch': self.switch}}

            with self.setupNetworks({}, BONDCREATE, NOCHK):
                self.setupNetworks({}, BONDEDIT, NOCHK)
                self.assertBond(BOND_NAME, BONDEDIT[BOND_NAME])

    def test_swap_slaves_between_bonds(self):
        BOND1 = BOND_NAME + '1'
        BOND2 = BOND_NAME + '2'

        with dummy_devices(4) as (nic1, nic2, nic3, nic4):
            BONDCREATE = {
                BOND1: {'nics': [nic1, nic2], 'switch': self.switch},
                BOND2: {'nics': [nic3, nic4], 'switch': self.switch}}
            BONDEDIT = {
                BOND1: {'nics': [nic1, nic3], 'switch': self.switch},
                BOND2: {'nics': [nic2, nic4], 'switch': self.switch}}
            with self.setupNetworks({}, BONDCREATE, NOCHK):
                self.setupNetworks({}, BONDEDIT, NOCHK)
                self.assertBond(BOND1, BONDEDIT[BOND1])
                self.assertBond(BOND2, BONDEDIT[BOND2])

    def test_resize_bond(self):
        with dummy_devices(4) as (nic1, nic2, nic3, nic4):
            bond = {BOND_NAME: {'nics': [nic1, nic2],
                                'switch': self.switch}}

            with self.setupNetworks({}, bond, NOCHK):
                bond[BOND_NAME]['nics'] += [nic3, nic4]
                self.setupNetworks({}, bond, NOCHK)
                self.assertBond(BOND_NAME, bond[BOND_NAME])

                bond[BOND_NAME]['nics'].remove(nic4)
                self.setupNetworks({}, bond, NOCHK)
                self.assertBond(BOND_NAME, bond[BOND_NAME])

    def test_add_bond_with_bad_name_fails(self):
        INVALID_BOND_NAMES = ('bond',
                              'bonda',
                              'bond0a',
                              'jamesbond007')

        with dummy_devices(2) as (nic1, nic2):
            for bond_name in INVALID_BOND_NAMES:
                BONDCREATE = {bond_name: {'nics': [nic1, nic2],
                                          'switch': self.switch}}
                with self.assertRaises(SetupNetworksError) as cm:
                    with self.setupNetworks({}, BONDCREATE, NOCHK):
                        pass
                self.assertEqual(cm.exception.status, ne.ERR_BAD_BONDING)

    def test_add_bond_with_no_nics_fails(self):
        BONDCREATE = {BOND_NAME: {'nics': [], 'switch': self.switch}}

        with self.assertRaises(SetupNetworksError) as err:
            with self.setupNetworks({}, BONDCREATE, NOCHK):
                pass
        self.assertEqual(err.exception.status, ne.ERR_BAD_PARAMS)


@attr(type='functional', switch='legacy')
class BondBasicLegacyTest(BondBasicTemplate):
    __test__ = True
    switch = 'legacy'


@attr(type='functional', switch='ovs')
class BondBasicOvsTest(BondBasicTemplate):
    __test__ = True
    switch = 'ovs'


class BondOptionsTestTemplate(NetFuncTestCase):
    __test__ = False

    def test_bond_mode_1(self):
        with dummy_devices(2) as (nic1, nic2):
            BONDCREATE = {BOND_NAME: {
                'nics': [nic1, nic2],
                'options': 'mode=1 primary=' + nic1,
                'switch': self.switch}}

            with self.setupNetworks({}, BONDCREATE, NOCHK):
                self.assertBond(BOND_NAME, BONDCREATE[BOND_NAME])

    def test_bond_mode_change(self):
        with dummy_devices(2) as nics:
            BONDCREATE = {BOND_NAME: {'nics': nics,
                                      'switch': self.switch,
                                      'options': 'mode=1 miimon=150'}}
            BONDEDIT = {BOND_NAME: {'nics': nics,
                                    'switch': self.switch,
                                    'options': 'mode=3'}}
            with self.setupNetworks({}, BONDCREATE, NOCHK):
                self.setupNetworks({}, BONDEDIT, NOCHK)
                self.assertBond(BOND_NAME, BONDEDIT[BOND_NAME])

    def test_bond_arp_ip_target_change(self):
        with dummy_devices(2) as nics:
            create_options = ('mode=1 arp_interval=1000 '
                              'arp_ip_target=192.168.122.1')
            BONDCREATE = {BOND_NAME: {'nics': nics,
                                      'switch': self.switch,
                                      'options': create_options}}
            edit_options = ('mode=1 arp_interval=1000 '
                            'arp_ip_target=10.1.3.1,10.1.2.1')
            BONDEDIT = {BOND_NAME: {'nics': nics,
                                    'switch': self.switch,
                                    'options': edit_options}}
            with self.setupNetworks({}, BONDCREATE, NOCHK):
                self.setupNetworks({}, BONDEDIT, NOCHK)
                self.assertBond(BOND_NAME, BONDEDIT[BOND_NAME])


@attr(type='functional', switch='legacy')
class BondOptionsLegacyTest(BondOptionsTestTemplate):
    __test__ = True
    switch = 'legacy'


@attr(type='functional', switch='ovs')
class BondOptionsOvsTest(BondOptionsTestTemplate):
    __test__ = True
    switch = 'ovs'
