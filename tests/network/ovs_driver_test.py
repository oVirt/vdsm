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
from uuid import UUID

from nose.plugins.attrib import attr

from .nettestlib import dummy_device
from .ovsnettestlib import OvsService, TEST_BRIDGE, TEST_BOND
from testlib import VdsmTestCase
from testValidation import ValidateRunningAsRoot

from vdsm.network.ovs.driver import create, Drivers as OvsDrivers
from vdsm.network.ovs.driver import vsctl


@attr(type='unit')
class TestOvsVsctlCommand(VdsmTestCase):

    RAW_VSCTL_LIST_BRIDGE_OUTPUT = """
    {"data":
    [
     [["uuid","065adffd-e37e-479f-94b4-1cf72e51d18a"],
      ["set",[]],
      ["set",[]],
      "0000fedf5a069f47",
      "",
      "<unknown>",
      ["map",[]],
      ["set",[]],
      ["set",[]],
      ["map",[]],
      ["set",[]],
      false,
      ["set",[]],
      "ovstest1",
      ["set",[]],
      ["map",[]],
      ["uuid","ac6dfc76-a346-4de0-a234-f2ebc4c9269f"],
      ["set",[]],
      false,
      ["map",[]],
      ["set",[]],
      ["map",[]],
      false
     ],
     [["uuid","4aa2740e-e28f-4778-95c9-427df114c6a2"],
      ["set",[]],
      ["set",[]],
      "00000e74a24a7847",
      "",
      "<unknown>",
      ["map",[]],
      ["set",[]],
      ["set",[]],
      ["map",[]],
      ["set",[]],
      false,
      ["set",[]],
      "ovstest0",
      ["set",[]],
      ["map",[]],
      ["uuid","a7c4c945-b8da-44c2-b911-4f43d9f71bc7"],
      ["set",[]],
      false,
      ["map",[]],
      ["set",[]],
      ["map",[]],
      false
     ]
    ],
    "headings":
    ["_uuid",
     "auto_attach",
     "controller",
     "datapath_id",
     "datapath_type",
     "datapath_version",
     "external_ids",
     "fail_mode",
     "flood_vlans",
     "flow_tables",
     "ipfix",
     "mcast_snooping_enable",
     "mirrors",
     "name",
     "netflow",
     "other_config",
     "ports",
     "protocols",
     "rstp_enable",
     "rstp_status",
     "sflow",
     "status",
     "stp_enable"]}
    """.replace('\n', '').replace(' ', '')

    PROCESSED_VSCTL_LIST_BRIDGE_OUTPUT = [
        {u'datapath_id': u'0000fedf5a069f47',
         u'datapath_type': u'',
         u'mirrors': [],
         u'rstp_status': {},
         u'netflow': [],
         u'rstp_enable': False,
         u'flood_vlans': [],
         u'datapath_version': u'<unknown>',
         u'status': {},
         u'ipfix': [],
         u'_uuid': UUID('065adffd-e37e-479f-94b4-1cf72e51d18a'),
         u'controller': [],
         u'auto_attach': [],
         u'mcast_snooping_enable': False,
         u'external_ids': {},
         u'protocols': [],
         u'fail_mode': [],
         u'name': u'ovstest1',
         u'sflow': [],
         u'other_config': {},
         u'flow_tables': {},
         u'ports': [UUID('ac6dfc76-a346-4de0-a234-f2ebc4c9269f')],
         u'stp_enable': False},
        {u'datapath_id': u'00000e74a24a7847',
         u'datapath_type': u'',
         u'mirrors': [],
         u'rstp_status': {},
         u'netflow': [],
         u'rstp_enable': False,
         u'flood_vlans': [],
         u'datapath_version': u'<unknown>',
         u'status': {},
         u'ipfix': [],
         u'_uuid': UUID('4aa2740e-e28f-4778-95c9-427df114c6a2'),
         u'controller': [],
         u'auto_attach': [],
         u'mcast_snooping_enable': False,
         u'external_ids': {},
         u'protocols': [],
         u'fail_mode': [],
         u'name': u'ovstest0',
         u'sflow': [],
         u'other_config': {},
         u'flow_tables': {},
         u'ports': [UUID('a7c4c945-b8da-44c2-b911-4f43d9f71bc7')],
         u'stp_enable': False}]

    def test_db_result_command_parser(self):
        cmd = vsctl.DBResultCommand(['fake', 'command'])
        cmd.set_raw_result(TestOvsVsctlCommand.RAW_VSCTL_LIST_BRIDGE_OUTPUT)

        self.assertEqual(
            TestOvsVsctlCommand.PROCESSED_VSCTL_LIST_BRIDGE_OUTPUT, cmd.result)


@attr(type='integration')
class TestOvsApiBase(VdsmTestCase):

    @ValidateRunningAsRoot
    def setUp(self):
        self.ovs_service = OvsService()
        self.ovs_service.setup()

    def tearDown(self):
        self.ovs_service.teardown()

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


@attr(type='integration')
class TestOvsApiWithSingleRealBridge(VdsmTestCase):

    @ValidateRunningAsRoot
    def setUp(self):
        self.ovs_service = OvsService()
        self.ovs_service.setup()

        self.ovsdb = create()
        self.ovsdb.add_br(TEST_BRIDGE).execute()

    def tearDown(self):
        self.ovsdb.del_br(TEST_BRIDGE).execute()
        self.ovs_service.teardown()

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
        with dummy_device() as dev0, dummy_device() as dev1:
            with ovs_bond(self.ovsdb, TEST_BRIDGE, TEST_BOND, [dev0, dev1]):
                self.assertEqual([TEST_BOND],
                                 self.ovsdb.list_ports(TEST_BRIDGE).execute())

            self.assertEqual([], self.ovsdb.list_ports(TEST_BRIDGE).execute())

    def test_add_slave_to_bond(self):
        with dummy_device() as dev0,\
                dummy_device() as dev1,\
                dummy_device() as dev2:
            with ovs_bond(self.ovsdb, TEST_BRIDGE, TEST_BOND, [dev0, dev1]):
                with self.ovsdb.transaction() as t:
                    t.add(*self.ovsdb.attach_bond_slave(TEST_BOND, dev2))

                bond_data = self.ovsdb.list_port_info(TEST_BOND).execute()
                self.assertEqual(1, len(bond_data))
                self.assertEqual(3, len(bond_data[0]['interfaces']))

    def test_remove_slave_from_bond(self):
        with dummy_device() as dev0,\
                dummy_device() as dev1,\
                dummy_device() as dev2:
            with ovs_bond(self.ovsdb, TEST_BRIDGE, TEST_BOND,
                          [dev0, dev1, dev2]):
                with self.ovsdb.transaction() as t:
                    t.add(*self.ovsdb.detach_bond_slave(TEST_BOND, dev2))

                bond_data = self.ovsdb.list_port_info(TEST_BOND).execute()
                self.assertEqual(1, len(bond_data))
                self.assertEqual(2, len(bond_data[0]['interfaces']))


# TODO: We may want to move it to a more common location, per need.
@contextmanager
def ovs_bond(ovsdb, bridge, bond, ports):
    ovsdb.add_bond(bridge, bond, ports).execute()
    yield bond
    ovsdb.del_port(bond, bridge=bridge).execute()
