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

from .netfunctestlib import NetFuncTestCase, NOCHK
from .nettestlib import dummy_device

NETWORK_NAME = 'test-network'
VLAN = 10


@attr(type='functional')
class NetworkBasicTemplate(NetFuncTestCase):
    __test__ = False

    def test_add_net_based_on_nic(self):
        with dummy_device() as nic:
            NETCREATE = {NETWORK_NAME: {'nic': nic, 'switch': self.switch}}
            with self.setupNetworks(NETCREATE, {}, NOCHK):
                self.assertNetwork(NETWORK_NAME, NETCREATE[NETWORK_NAME])

    def test_remove_net_based_on_nic(self):
        with dummy_device() as nic:
            NETCREATE = {NETWORK_NAME: {'nic': nic, 'switch': self.switch}}
            NETREMOVE = {NETWORK_NAME: {'remove': True}}
            with self.setupNetworks(NETCREATE, {}, NOCHK):
                self.setupNetworks(NETREMOVE, {}, NOCHK)
                self.assertNoNetwork(NETWORK_NAME)

    def test_add_net_based_on_vlan(self):
        with dummy_device() as nic:
            NETCREATE = {NETWORK_NAME: {'nic': nic, 'vlan': VLAN,
                                        'switch': self.switch}}
            with self.setupNetworks(NETCREATE, {}, NOCHK):
                self.assertNetwork(NETWORK_NAME, NETCREATE[NETWORK_NAME])

    def test_remove_net_based_on_vlan(self):
        with dummy_device() as nic:
            NETCREATE = {NETWORK_NAME: {'nic': nic, 'vlan': VLAN,
                                        'switch': self.switch}}
            NETREMOVE = {NETWORK_NAME: {'remove': True}}
            with self.setupNetworks(NETCREATE, {}, NOCHK):
                self.setupNetworks(NETREMOVE, {}, NOCHK)
                self.assertNoNetwork(NETWORK_NAME)
                self.assertNoVlan(nic, VLAN)


@attr(type='functional', switch='legacy')
class NetworkBasicLegacyTest(NetworkBasicTemplate):
    __test__ = True
    switch = 'legacy'


@attr(type='functional', switch='ovs')
class NetworkBasicOvsTest(NetworkBasicTemplate):
    __test__ = True
    switch = 'ovs'
