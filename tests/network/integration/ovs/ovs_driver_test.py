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

from network.nettestlib import dummy_devices
from .ovsnettestlib import TEST_BRIDGE
from .ovsnettestlib import TEST_BOND

from vdsm.network.ovs.driver import create, Drivers as OvsDrivers


class TestOvsApiBase(unittest.TestCase):
    def test_instantiate_vsctl_implementation(self):
        self.assertIsNotNone(create(OvsDrivers.VSCTL))

    def test_execute_a_single_command(self):
        ovsdb = create()
        out = ovsdb.list_bridge_info().execute()

        # No bridges defined
        self.assertEqual([], out)

    def test_execute_a_transaction(self):
        ovsdb = create()
        cmd_add_br = ovsdb.add_br(TEST_BRIDGE)
        cmd_list_bridge_info = ovsdb.list_bridge_info()
        t = ovsdb.transaction()
        t.add(cmd_add_br)
        t.add(cmd_list_bridge_info)
        t.commit()

        self.assertEqual(1, len(cmd_list_bridge_info.result))
        bridge_name = cmd_list_bridge_info.result[0]['name']
        self.assertIn(TEST_BRIDGE, bridge_name)

        cmd_del_br = ovsdb.del_br(TEST_BRIDGE)
        with ovsdb.transaction() as trans:
            trans.add(cmd_del_br)
            trans.add(cmd_list_bridge_info)

        self.assertEqual([], cmd_list_bridge_info.result)


class TestOvsApiWithSingleRealBridge(unittest.TestCase):
    def setUp(self):
        self.ovsdb = create()
        self.ovsdb.add_br(TEST_BRIDGE).execute()

    def tearDown(self):
        self.ovsdb.del_br(TEST_BRIDGE).execute()

    def test_create_vlan_as_fake_bridge(self):
        with self.ovsdb.transaction() as t:
            t.add(self.ovsdb.add_vlan(TEST_BRIDGE, 100))
            t.add(self.ovsdb.add_vlan(TEST_BRIDGE, 101))

        bridges = self.ovsdb.list_br().execute()
        self.assertEqual(3, len(bridges))

        with self.ovsdb.transaction() as t:
            t.add(self.ovsdb.del_vlan(101))
            t.add(self.ovsdb.del_vlan(100))

    def test_create_remove_bond(self):
        with dummy_devices(2) as (dev0, dev1):
            with ovs_bond(self.ovsdb, TEST_BRIDGE, TEST_BOND, [dev0, dev1]):
                self.assertEqual(
                    [TEST_BOND], self.ovsdb.list_ports(TEST_BRIDGE).execute()
                )

            self.assertEqual([], self.ovsdb.list_ports(TEST_BRIDGE).execute())

    def test_add_slave_to_bond(self):
        with dummy_devices(3) as (dev0, dev1, dev2):
            with ovs_bond(self.ovsdb, TEST_BRIDGE, TEST_BOND, [dev0, dev1]):
                with self.ovsdb.transaction() as t:
                    t.add(*self.ovsdb.attach_bond_slave(TEST_BOND, dev2))

                bond_data = self.ovsdb.list_port_info(TEST_BOND).execute()
                self.assertEqual(1, len(bond_data))
                self.assertEqual(3, len(bond_data[0]['interfaces']))

    def test_remove_slave_from_bond(self):
        with dummy_devices(3) as (dev0, dev1, dev2):
            with ovs_bond(
                self.ovsdb, TEST_BRIDGE, TEST_BOND, [dev0, dev1, dev2]
            ):
                with self.ovsdb.transaction() as t:
                    t.add(*self.ovsdb.detach_bond_slave(TEST_BOND, dev2))

                bond_data = self.ovsdb.list_port_info(TEST_BOND).execute()
                self.assertEqual(1, len(bond_data))
                self.assertEqual(2, len(bond_data[0]['interfaces']))

    def test_create_remove_port_mirroring(self):
        with dummy_devices(1) as (dev0,):
            with ovs_port(self.ovsdb, TEST_BRIDGE, dev0), ovs_mirror(
                self.ovsdb, TEST_BRIDGE, 'm0', dev0
            ):
                mirror_data = self.ovsdb.list_mirror_info().execute()
                port_data = self.ovsdb.list_port_info(dev0).execute()

                self.assertEqual(1, len(mirror_data))
                self.assertEqual(
                    port_data[0]['_uuid'], mirror_data[0]['output_port']
                )

            bridge_data = self.ovsdb.list_bridge_info(TEST_BRIDGE).execute()
            self.assertNotIn(
                mirror_data[0]['_uuid'], bridge_data[0]['mirrors']
            )

    def test_set_mirrored_port(self):
        with dummy_devices(2) as (dev0, dev1):
            with ovs_port(self.ovsdb, TEST_BRIDGE, dev0), ovs_port(
                self.ovsdb, TEST_BRIDGE, dev1
            ), ovs_mirror(self.ovsdb, TEST_BRIDGE, 'm0', dev0):
                mirror_data = self.ovsdb.list_mirror_info().execute()
                port_data = self.ovsdb.list_port_info(dev1).execute()

                mirror_id = mirror_data[0]['_uuid']
                port_id = port_data[0]['_uuid']

                self.ovsdb.set_mirror_attr(
                    str(mirror_id), 'select-dst-port', str(port_id)
                ).execute()

                mirror_data = self.ovsdb.list_mirror_info().execute()
                self.assertEqual(port_id, mirror_data[0]['select_dst_port'])


# TODO: We may want to move it to a more common location, per need.
@contextmanager
def ovs_bond(ovsdb, bridge, bond, ports):
    ovsdb.add_bond(bridge, bond, ports).execute()
    yield bond
    ovsdb.del_port(bond, bridge=bridge).execute()


@contextmanager
def ovs_port(ovsdb, bridge, port):
    ovsdb.add_port(bridge, port).execute()
    try:
        yield port
    finally:
        ovsdb.del_port(port, bridge).execute()


@contextmanager
def ovs_mirror(ovsdb, bridge, mirror, port):
    with ovsdb.transaction() as t:
        t.add(*ovsdb.add_mirror(bridge, mirror, port))
    try:
        yield mirror
    finally:
        with ovsdb.transaction() as t:
            t.add(*ovsdb.del_mirror(bridge, mirror))
