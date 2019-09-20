# Copyright 2016-2018 Red Hat, Inc.
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
from __future__ import division

from contextlib import contextmanager
import unittest

from network.nettestlib import dummy_device, bond_device
from network.ovsnettestlib import TEST_BRIDGE

from vdsm.network.ovs import info
from vdsm.network.ovs.driver import create


TEST_VLAN = 10
TEST_VLANED_NETWORK = 'test-network' + str(TEST_VLAN)


@contextmanager
def _setup_ovs_network(ovsdb, sb_iface):
    def _bridge():
        return ovsdb.add_br(TEST_BRIDGE)

    def _attach_southbound():
        commands = []
        commands.append(ovsdb.add_port(TEST_BRIDGE, sb_iface))
        commands.append(
            ovsdb.set_port_attr(
                sb_iface, 'other_config:vdsm_level', info.SOUTHBOUND
            )
        )
        return commands

    def _northbound_port():
        commands = []
        commands.append(ovsdb.add_port(TEST_BRIDGE, TEST_VLANED_NETWORK))
        commands.append(
            ovsdb.set_port_attr(TEST_VLANED_NETWORK, 'tag', TEST_VLAN)
        )
        commands.append(
            ovsdb.set_port_attr(
                TEST_VLANED_NETWORK, 'other_config:vdsm_level', info.NORTHBOUND
            )
        )
        commands.append(
            ovsdb.set_interface_attr(TEST_VLANED_NETWORK, 'type', 'internal')
        )
        return commands

    with ovsdb.transaction() as t:
        t.add(_bridge())
        t.add(*_attach_southbound())
        t.add(*_northbound_port())

    try:
        yield
    finally:
        ovsdb.del_br(TEST_BRIDGE).execute()


class TestOvsInfo(unittest.TestCase):
    def setUp(self):
        self.ovsdb = create()

    def test_ovs_info_with_sb_nic(self):
        with dummy_device() as nic:
            with _setup_ovs_network(self.ovsdb, nic):
                expected_bridges = {
                    TEST_BRIDGE: {
                        'stp': False,
                        'dpdk_enabled': False,
                        'ports': {
                            nic: {'level': info.SOUTHBOUND, 'tag': None},
                            TEST_VLANED_NETWORK: {
                                'level': info.NORTHBOUND,
                                'tag': TEST_VLAN,
                            },
                            TEST_BRIDGE: {'level': None, 'tag': None},
                        },
                    }
                }

                ovs_info = info.OvsInfo()

                obtained_bridges = ovs_info.bridges
                self.assertEqual(obtained_bridges, expected_bridges)

                obtained_bridges_by_sb = ovs_info.bridges_by_sb
                self.assertEqual(obtained_bridges_by_sb, {nic: TEST_BRIDGE})

    def test_ovs_info_with_sb_bond(self):
        with dummy_device() as nic:
            with bond_device([nic]) as bond:
                with _setup_ovs_network(self.ovsdb, bond):
                    expected_bridges = {
                        TEST_BRIDGE: {
                            'stp': False,
                            'dpdk_enabled': False,
                            'ports': {
                                TEST_VLANED_NETWORK: {
                                    'level': info.NORTHBOUND,
                                    'tag': TEST_VLAN,
                                },
                                TEST_BRIDGE: {'level': None, 'tag': None},
                                bond: {'level': info.SOUTHBOUND, 'tag': None},
                            },
                        }
                    }

                    ovs_info = info.OvsInfo()

                    obtained_bridges = ovs_info.bridges
                    self.assertEqual(obtained_bridges, expected_bridges)

                    obtained_bridges_by_sb = ovs_info.bridges_by_sb
                    self.assertEqual(
                        obtained_bridges_by_sb, {bond: TEST_BRIDGE}
                    )
