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

from copy import deepcopy
import six

from vdsm.network import errors as ne
from vdsm.network.ipwrapper import linkSet, addrAdd

from network.nettestlib import (dummy_device, dummy_devices,
                                veth_pair, dnsmasq_run)

from .netfunctestlib import NetFuncTestCase, SetupNetworksError, NOCHK


NET1_NAME = 'test-network1'
NET2_NAME = 'test-network2'
VLAN = 10
BOND_NAME = 'bond10'

IPv4_ADDRESS = '192.0.3.1'
IPv4_NETMASK = '255.255.255.0'
IPv4_PREFIX_LEN = '24'
IPv6_ADDRESS = 'fdb3:84e5:4ff4:55e3::1/64'

DHCPv4_RANGE_FROM = '192.0.3.2'
DHCPv4_RANGE_TO = '192.0.3.253'


class BasicSwitchChangeTemplate(NetFuncTestCase):
    __test__ = False

    def test_switch_change_basic_network(self):
        with dummy_device() as nic:
            NETSETUP_SOURCE = {NET1_NAME: {
                'nic': nic, 'switch': self.switch_type_source}}
            NETSETUP_TARGET = _change_switch_type(
                NETSETUP_SOURCE, self.switch_type_target)

            with self.setupNetworks(NETSETUP_SOURCE, {}, NOCHK):
                self.setupNetworks(NETSETUP_TARGET, {}, NOCHK)
                self.assertNetwork(NET1_NAME, NETSETUP_TARGET[NET1_NAME])

    def test_switch_change_basic_vlaned_network(self):
        with dummy_device() as nic:
            NETSETUP_SOURCE = {NET1_NAME: {
                'nic': nic, 'vlan': VLAN, 'switch': self.switch_type_source}}
            NETSETUP_TARGET = _change_switch_type(
                NETSETUP_SOURCE, self.switch_type_target)

            with self.setupNetworks(NETSETUP_SOURCE, {}, NOCHK):
                self.setupNetworks(NETSETUP_TARGET, {}, NOCHK)
                self.assertNetwork(NET1_NAME, NETSETUP_TARGET[NET1_NAME])

    def test_switch_change_bonded_network(self):
        with dummy_devices(2) as (nic1, nic2):
            NETSETUP_SOURCE = {NET1_NAME: {
                'bonding': BOND_NAME, 'switch': self.switch_type_source}}
            NETSETUP_TARGET = _change_switch_type(
                NETSETUP_SOURCE, self.switch_type_target)
            BONDSETUP_SOURCE = {BOND_NAME: {
                'nics': [nic1, nic2], 'switch': self.switch_type_source}}
            BONDSETUP_TARGET = _change_switch_type(
                BONDSETUP_SOURCE, self.switch_type_target)

            with self.setupNetworks(NETSETUP_SOURCE, BONDSETUP_SOURCE, NOCHK):
                self.setupNetworks(NETSETUP_TARGET, BONDSETUP_TARGET, NOCHK)
                self.assertNetwork(NET1_NAME, NETSETUP_TARGET[NET1_NAME])
                self.assertBond(BOND_NAME, BONDSETUP_TARGET[BOND_NAME])


class BasicSwitchChangeLegacy2OvsTest(BasicSwitchChangeTemplate):
    __test__ = True
    switch_type_source = 'legacy'
    switch_type_target = 'ovs'


class BasicSwitchChangeOvs2LegacyTest(BasicSwitchChangeTemplate):
    __test__ = True
    switch_type_source = 'ovs'
    switch_type_target = 'legacy'


class IpSwitchTemplate(NetFuncTestCase):
    __test__ = False

    def test_switch_change_bonded_network_with_static_ip(self):
        with dummy_devices(2) as (nic1, nic2):
            NETSETUP_SOURCE = {NET1_NAME: {
                'bonding': BOND_NAME,
                'ipaddr': IPv4_ADDRESS,
                'netmask': IPv4_NETMASK,
                'ipv6addr': IPv6_ADDRESS,
                'switch': self.switch_type_source}}
            NETSETUP_TARGET = _change_switch_type(
                NETSETUP_SOURCE, self.switch_type_target)
            BONDSETUP_SOURCE = {BOND_NAME: {
                'nics': [nic1, nic2], 'switch': self.switch_type_source}}
            BONDSETUP_TARGET = _change_switch_type(
                BONDSETUP_SOURCE, self.switch_type_target)

            with self.setupNetworks(NETSETUP_SOURCE, BONDSETUP_SOURCE, NOCHK):
                self.setupNetworks(NETSETUP_TARGET, BONDSETUP_TARGET, NOCHK)
                self.assertNetwork(NET1_NAME, NETSETUP_TARGET[NET1_NAME])
                self.assertBond(BOND_NAME, BONDSETUP_TARGET[BOND_NAME])

    def test_switch_change_bonded_network_with_dhclient(self):
        with veth_pair() as (server, nic1):
            with dummy_device() as nic2:
                NETSETUP_SOURCE = {NET1_NAME: {
                    'bonding': BOND_NAME,
                    'bootproto': 'dhcp',
                    'blockingdhcp': True,
                    'switch': self.switch_type_source}}
                NETSETUP_TARGET = _change_switch_type(
                    NETSETUP_SOURCE, self.switch_type_target)
                BONDSETUP_SOURCE = {BOND_NAME: {
                    'nics': [nic1, nic2], 'switch': self.switch_type_source}}
                BONDSETUP_TARGET = _change_switch_type(
                    BONDSETUP_SOURCE, self.switch_type_target)

                addrAdd(server, IPv4_ADDRESS, IPv4_PREFIX_LEN)
                linkSet(server, ['up'])

                with dnsmasq_run(server, DHCPv4_RANGE_FROM, DHCPv4_RANGE_TO):
                    with self.setupNetworks(
                            NETSETUP_SOURCE, BONDSETUP_SOURCE, NOCHK):
                        self.setupNetworks(
                            NETSETUP_TARGET, BONDSETUP_TARGET, NOCHK)
                        self.assertNetwork(
                            NET1_NAME, NETSETUP_TARGET[NET1_NAME])
                        self.assertBond(BOND_NAME, BONDSETUP_TARGET[BOND_NAME])


class IpSwitchLegacy2OvsTest(IpSwitchTemplate):
    __test__ = True
    switch_type_source = 'legacy'
    switch_type_target = 'ovs'


class IpSwitchOvs2LegacyTest(IpSwitchTemplate):
    __test__ = True
    switch_type_source = 'ovs'
    switch_type_target = 'legacy'


class SwitchRollbackTemplate(NetFuncTestCase):
    __test__ = False

    def test_rollback_target_configuration_with_invalid_ip(self):
        with dummy_device() as nic:
            NETSETUP_SOURCE = {NET1_NAME: {
                'nic': nic, 'switch': self.switch_type_source}}
            NETSETUP_TARGET = {NET1_NAME: {
                'nic': nic,
                'ipaddr': '300.300.300.300',  # invalid
                'netmask': IPv4_NETMASK,
                'switch': self.switch_type_target}}

            with self.setupNetworks(NETSETUP_SOURCE, {}, NOCHK):
                with self.assertRaises(SetupNetworksError) as e:
                    self.setupNetworks(NETSETUP_TARGET, {}, NOCHK)
                self.assertEqual(e.exception.status, ne.ERR_BAD_ADDR)
                self.assertNetwork(NET1_NAME, NETSETUP_SOURCE[NET1_NAME])

    def test_rollback_target_bond_configuration_with_invalid_ip(self):
        with dummy_devices(3) as (nic1, nic2, nic3):
            NETSETUP_SOURCE = {NET1_NAME: {
                'nic': nic1, 'switch': self.switch_type_source}}
            BONDSETUP_SOURCE = {BOND_NAME: {
                'nics': [nic2, nic3], 'switch': self.switch_type_source}}
            NETSETUP_TARGET = {NET1_NAME: {
                'nic': nic1,
                'ipaddr': '300.300.300.300',  # invalid
                'netmask': IPv4_NETMASK,
                'switch': self.switch_type_target}}
            BONDSETUP_TARGET = {BOND_NAME: {
                'nics': [nic2, nic3], 'switch': self.switch_type_target}}

            with self.setupNetworks(NETSETUP_SOURCE, BONDSETUP_SOURCE, NOCHK):
                with self.assertRaises(SetupNetworksError) as e:
                    self.setupNetworks(
                        NETSETUP_TARGET, BONDSETUP_TARGET, NOCHK)
                self.assertEqual(e.exception.status, ne.ERR_BAD_ADDR)
                self.assertNetwork(NET1_NAME, NETSETUP_SOURCE[NET1_NAME])
                self.assertBond(BOND_NAME, BONDSETUP_SOURCE[BOND_NAME])

    def test_rollback_target_configuration_failed_connectivity_check(self):
        with dummy_device() as nic:
            NETSETUP_SOURCE = {
                NET1_NAME: {
                    'nic': nic, 'switch': self.switch_type_source},
                NET2_NAME: {
                    'nic': nic, 'vlan': VLAN,
                    'switch': self.switch_type_source}}
            NETSETUP_TARGET = _change_switch_type(
                NETSETUP_SOURCE, self.switch_type_target)

            with self.setupNetworks(NETSETUP_SOURCE, {}, NOCHK):
                with self.assertRaises(SetupNetworksError) as e:
                    self.setupNetworks(NETSETUP_TARGET, {},
                                       {'connectivityCheck': True,
                                        'connectivityTimeout': 0.1})
                self.assertEqual(e.exception.status, ne.ERR_LOST_CONNECTION)
                self.assertNetwork(NET1_NAME, NETSETUP_SOURCE[NET1_NAME])
                self.assertNetwork(NET2_NAME, NETSETUP_SOURCE[NET2_NAME])


class SwitchRollbackLegacy2OvsTest(SwitchRollbackTemplate):
    __test__ = True
    switch_type_source = 'legacy'
    switch_type_target = 'ovs'


class SwitchRollbackOvs2LegacyTest(SwitchRollbackTemplate):
    __test__ = True
    switch_type_source = 'ovs'
    switch_type_target = 'legacy'


class SwitchValidationTemplate(NetFuncTestCase):
    __test__ = False

    def test_switch_change_with_not_all_existing_networks_specified(self):
        with dummy_device() as nic:
            NETSETUP_SOURCE = {
                NET1_NAME: {'nic': nic, 'switch': self.switch_type_source},
                NET2_NAME: {'nic': nic, 'vlan': VLAN,
                            'switch': self.switch_type_source}}
            NETSETUP_TARGET = {
                NET1_NAME: {'nic': nic, 'switch': self.switch_type_target}}

            with self.setupNetworks(NETSETUP_SOURCE, {}, NOCHK):
                with self.assertRaises(SetupNetworksError) as e:
                    self.setupNetworks(NETSETUP_TARGET, {}, NOCHK)
                self.assertEqual(e.exception.status, ne.ERR_BAD_PARAMS)
                self.assertNetwork(NET1_NAME, NETSETUP_SOURCE[NET1_NAME])
                self.assertNetwork(NET2_NAME, NETSETUP_SOURCE[NET2_NAME])

    def test_switch_change_setup_includes_a_network_removal(self):
        with dummy_device() as nic:
            NETSETUP_SOURCE = {
                NET1_NAME: {'nic': nic, 'switch': self.switch_type_source},
                NET2_NAME: {'nic': nic, 'vlan': VLAN,
                            'switch': self.switch_type_source}}
            NETSETUP_TARGET = {
                NET1_NAME: {'nic': nic, 'switch': self.switch_type_target},
                NET2_NAME: {'remove': True}}

            with self.setupNetworks(NETSETUP_SOURCE, {}, NOCHK):
                with self.assertRaises(SetupNetworksError) as e:
                    self.setupNetworks(NETSETUP_TARGET, {}, NOCHK)
                self.assertEqual(e.exception.status, ne.ERR_BAD_PARAMS)
                self.assertNetwork(NET1_NAME, NETSETUP_SOURCE[NET1_NAME])
                self.assertNetwork(NET2_NAME, NETSETUP_SOURCE[NET2_NAME])


class SwitchValidationLegacy2OvsTest(SwitchValidationTemplate):
    __test__ = True
    switch_type_source = 'legacy'
    switch_type_target = 'ovs'


class SwitchValidationOvs2LegacyTest(SwitchValidationTemplate):
    __test__ = True
    switch_type_source = 'ovs'
    switch_type_target = 'legacy'


def _change_switch_type(requests, target_switch):
    changed_requests = deepcopy(requests)
    for attrs in six.itervalues(changed_requests):
        attrs['switch'] = target_switch
    return changed_requests
