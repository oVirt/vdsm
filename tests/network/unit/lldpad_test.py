#
# Copyright 2017-2019 Red Hat, Inc.
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

from vdsm.network.lldpad import lldptool

from network.compat import mock
from testlib import VdsmTestCase


LLDP_CHASSIS_ID_TLV = 'Chassis ID TLV\n\tMAC: 01:23:45:67:89:ab'

LLDP_MANAGEMENT_ADDRESS_TLV = """
Management Address TLV
\tIPv4: 10.35.23.241
\tIfindex: 83886080
"""

LLDP_MULTIPLE_TLVS = """
Chassis ID TLV
\tMAC: 01:23:45:67:89:ab
Port ID TLV
\tLocal: 588
Time to Live TLV
\t120
System Name TLV
\tsite1-row2-rack3
System Description TLV
\tmanufacturer, Build date: 2016-01-20 05:03:06 UTC
System Capabilities TLV
\tSystem capabilities:  Bridge, Router
\tEnabled capabilities: Bridge, Router
Management Address TLV
\tIPv4: 10.21.0.40
\tIfindex: 36
\tOID: $
Port Description TLV
\tsome important server, port 4
MAC/PHY Configuration Status TLV
\tAuto-negotiation supported and enabled
\tPMD auto-negotiation capabilities: 0x0001
\tMAU type: Unknown [0x0000]
Link Aggregation TLV
\tAggregation capable
\tCurrently aggregated
\tAggregated Port ID: 600
Maximum Frame Size TLV
\t9216
Port VLAN ID TLV
\tPVID: 2000
VLAN Name TLV
\tVID 2000: Name foo
VLAN Name TLV
\tVID 2001: Name bar
LLDP-MED Capabilities TLV
\tDevice Type:  netcon
\tCapabilities: LLDP-MED, Network Policy, Location Identification, '
\tExtended Power via MDI-PSE
Unidentified Org Specific TLV
\tOUI: 0x009069, Subtype: 1, Info: 504533373135323130333833
End of LLDPDU TLV
"""


class TestLldpadReport(VdsmTestCase):
    TLVS_REPORT = [
        {
            'type': 1,
            'name': 'Chassis ID',
            'properties': {
                'chassis ID': '01:23:45:67:89:ab',
                'chassis ID subtype': 'MAC',
            },
        },
        {
            'type': 2,
            'name': 'Port ID',
            'properties': {'port ID': '588', 'port ID subtype': 'Local'},
        },
        {
            'type': 3,
            'name': 'Time to Live',
            'properties': {'time to live': '120'},
        },
        {
            'type': 5,
            'name': 'System Name',
            'properties': {'system name': 'site1-row2-rack3'},
        },
        {
            'type': 6,
            'name': 'System Description',
            'properties': {
                'system description': 'manufacturer, Build date: '
                '2016-01-20 05:03:06 UTC'
            },
        },
        {
            'type': 7,
            'name': 'System Capabilities',
            'properties': {
                'system capabilities': 'Bridge, Router',
                'enabled capabilities': 'Bridge, Router',
            },
        },
        {
            'type': 8,
            'name': 'Management Address',
            'properties': {
                'object identifier': '$',
                'interface numbering subtype': 'Ifindex',
                'interface numbering': '36',
                'management address subtype': 'IPv4',
                'management address': '10.21.0.40',
            },
        },
        {
            'type': 4,
            'name': 'Port Description',
            'properties': {
                'port description': 'some important server, port 4'
            },
        },
        {
            'type': 127,
            'oui': 32962,
            'subtype': 7,
            'name': 'Link Aggregation',
            'properties': {
                'Currently aggregated': 'True',
                'Aggregation capable': 'True',
                'Aggregated Port ID': '600',
            },
        },
        {
            'type': 127,
            'oui': 4623,
            'subtype': 4,
            'name': 'MTU',
            'properties': {'mtu': '9216'},
        },
        {
            'subtype': 1,
            'oui': 32962,
            'type': 127,
            'name': 'Port VLAN ID',
            'properties': {'Port VLAN ID': '2000'},
        },
        {
            'subtype': 3,
            'oui': 32962,
            'type': 127,
            'name': 'VLAN Name',
            'properties': {'VLAN ID': '2000', 'VLAN Name': 'Name foo'},
        },
        {
            'subtype': 3,
            'oui': 32962,
            'type': 127,
            'name': 'VLAN Name',
            'properties': {'VLAN ID': '2001', 'VLAN Name': 'Name bar'},
        },
    ]

    @mock.patch.object(
        lldptool.cmd, 'exec_sync', lambda x: (0, LLDP_CHASSIS_ID_TLV, '')
    )
    def test_get_single_lldp_tlv(self):
        expected = [self.TLVS_REPORT[0]]
        self.assertEqual(expected, lldptool.get_tlvs('iface0'))

    @mock.patch.object(
        lldptool.cmd,
        'exec_sync',
        lambda x: (0, LLDP_MANAGEMENT_ADDRESS_TLV, ''),
    )
    def test_get_management_address_tlv_without_oid(self):
        expected = [
            {
                'type': 8,
                'name': 'Management Address',
                'properties': {
                    'interface numbering subtype': 'Ifindex',
                    'interface numbering': '83886080',
                    'management address subtype': 'IPv4',
                    'management address': '10.35.23.241',
                },
            }
        ]
        self.assertEqual(expected, lldptool.get_tlvs('iface0'))

    @mock.patch.object(
        lldptool.cmd, 'exec_sync', lambda x: (0, LLDP_MULTIPLE_TLVS, '')
    )
    def test_get_multiple_lldp_tlvs(self):
        self.assertEqual(self.TLVS_REPORT, lldptool.get_tlvs('iface0'))
