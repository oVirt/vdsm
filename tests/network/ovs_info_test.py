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
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA
#
# Refer to the README and COPYING files for full details of the license
#
from __future__ import absolute_import

from contextlib import contextmanager

from nose.plugins.attrib import attr

from .nettestlib import dummy_device
from .ovsnettestlib import OvsService, TEST_BRIDGE, TEST_BOND
from testlib import VdsmTestCase
from testValidation import ValidateRunningAsRoot

from vdsm.network.ovs import info
from vdsm.network.ovs.driver import create


TEST_NETWORK = 'test-network'
TEST_ADDRESS = '192.168.1.10'
TEST_NETMASK = '255.255.255.0'
TEST_ADDRESS_WITH_PREFIX = '192.168.1.10/24'
TEST_VLAN = 10


@contextmanager
def _setup_ovs_network(ovsdb, nic1, nic2):

    def _bridge():
        return ovsdb.add_br(TEST_BRIDGE)

    def _bond():
        commands = []
        commands.append(ovsdb.add_bond(TEST_BRIDGE, TEST_BOND, [nic1, nic2]))
        commands.append(ovsdb.set_port_attr(
            TEST_BOND, 'bond_mode', 'active-backup'))
        commands.append(ovsdb.set_port_attr(
            TEST_BOND, 'other_config:vdsm_level', info.SOUTHBOUND))
        return commands

    def _northbound_port():
        commands = []
        commands.append(ovsdb.add_port(TEST_BRIDGE, TEST_NETWORK))
        commands.append(ovsdb.set_port_attr(TEST_NETWORK, 'tag', TEST_VLAN))
        commands.append(ovsdb.set_port_attr(
            TEST_NETWORK, 'other_config:vdsm_level', info.NORTHBOUND))
        commands.append(ovsdb.set_interface_attr(
            TEST_NETWORK, 'type', 'internal'))
        return commands

    with ovsdb.transaction() as t:
        t.add(_bridge())
        t.add(*_bond())
        t.add(*_northbound_port())

    try:
        yield
    finally:
        ovsdb.del_br(TEST_BRIDGE).execute()


@attr(type='integration')
class TestOvsInfo(VdsmTestCase):

    @ValidateRunningAsRoot
    def setUp(self):
        self.ovs_service = OvsService()
        self.ovs_service.setup()
        self.ovsdb = create()

    def tearDown(self):
        self.ovs_service.teardown()

    def test_ovs_info(self):
        with dummy_device() as nic1, dummy_device() as nic2:
            with _setup_ovs_network(self.ovsdb, nic1, nic2):
                expected_bridges = {
                    TEST_BRIDGE: {
                        'stp': False,
                        'ports': {
                            TEST_BOND: {
                                'bond': {
                                    'active_slave': None,
                                    'fake_iface': False,
                                    'lacp': None,
                                    'mode': 'active-backup',
                                    'slaves': sorted([nic1, nic2])
                                },
                                'level': info.SOUTHBOUND,
                                'tag': None
                            },
                            TEST_NETWORK: {
                                'bond': None,
                                'level': info.NORTHBOUND,
                                'tag': TEST_VLAN
                            },
                            TEST_BRIDGE: {
                                'bond': None,
                                'level': None,
                                'tag': None
                            }
                        }
                    }
                }
                obtained_bridges = info.OvsInfo().bridges
                self.assertEqual(obtained_bridges, expected_bridges)
