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

import unittest
from uuid import UUID

from vdsm.network.ovs.driver import vsctl


class TestOvsVsctlCommand(unittest.TestCase):

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
    """.replace(
        '\n', ''
    ).replace(
        ' ', ''
    )

    PROCESSED_VSCTL_LIST_BRIDGE_OUTPUT = [
        {
            u'datapath_id': u'0000fedf5a069f47',
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
            u'stp_enable': False,
        },
        {
            u'datapath_id': u'00000e74a24a7847',
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
            u'stp_enable': False,
        },
    ]

    def test_db_result_command_parser(self):
        cmd = vsctl.DBResultCommand(['fake', 'command'])
        cmd.set_raw_result(TestOvsVsctlCommand.RAW_VSCTL_LIST_BRIDGE_OUTPUT)

        self.assertEqual(
            TestOvsVsctlCommand.PROCESSED_VSCTL_LIST_BRIDGE_OUTPUT, cmd.result
        )
