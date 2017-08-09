#
# Copyright 2017 Red Hat, Inc.
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

from collections import namedtuple

from vdsm.virt.vmdevices import common
from vdsm.virt import metadata

from testlib import XMLTestCase
# ugly, temporary hack until we need to keep around those tests
from .metadata_test import FakeDomain


# NOTE:
# unless otherwise specified, UUIDs are randomly generated
# and have no special meaning


_TestData = namedtuple('_TestData', ('conf', 'metadata_xml',))


_CDROM_DATA = _TestData(
    conf={
        'address': {
            'bus': '1',
            'controller': '0',
            'target': '0',
            'type': 'drive',
            'unit': '0'
        },
        'device': 'cdrom',
        'deviceId': 'e59c985c-46c2-4489-b355-a6f374125eb9',
        'iface': 'ide',
        'index': '2',
        'path': '',
        'readonly': 'true',
        'shared': 'false',
        'specParams': {'path': ''},
        'type': 'disk',
        'vm_custom': {},
    },
    metadata_xml="""<?xml version='1.0' encoding='UTF-8'?>
    <vm>
    <device iface="ide" index="2" type="disk">
        <device>cdrom</device>
        <deviceId>e59c985c-46c2-4489-b355-a6f374125eb9</deviceId>
        <iface>ide</iface>
        <index>2</index>
        <path />
        <readonly>true</readonly>
        <shared>false</shared>
        <type>disk</type>
        <address>
            <bus>1</bus>
            <controller>0</controller>
            <target>0</target>
            <type>drive</type>
            <unit>0</unit>
        </address>
        <specParams>
            <path />
        </specParams>
        <vm_custom />
    </device>
    </vm>""",
)

_CDROM_PAYLOAD_DATA = _TestData(
    conf={
        'index': '3',
        'iface': 'ide',
        'specParams': {
            'vmPayload': {
                'volId': 'config-2',
                'file': {
                    'openstack/content/0000': 'something',
                    'openstack/latest/meta_data.json': 'something',
                    'openstack/latest/user_data': 'something',
                }
            }
        },
        'readonly': 'true',
        'deviceId': '423af2b3-5d02-44c5-9d2e-9e69de6eef44',
        'path': '',
        'device': 'cdrom',
        'shared': 'false',
        'type': 'disk',
        'vm_custom': {},
    },
    metadata_xml="""<?xml version='1.0' encoding='UTF-8'?>
    <vm>
    <device iface="ide" index="3" type="disk">
        <device>cdrom</device>
        <deviceId>423af2b3-5d02-44c5-9d2e-9e69de6eef44</deviceId>
        <iface>ide</iface>
        <index>3</index>
        <path />
        <readonly>true</readonly>
        <shared>false</shared>
        <type>disk</type>
        <specParams>
            <vmPayload>
                <volId>config-2</volId>
                <file path='openstack/content/0000'>something</file>
                <file path='openstack/latest/meta_data.json'>something</file>
                <file path='openstack/latest/user_data'>something</file>
            </vmPayload>
        </specParams>
        <vm_custom />
    </device>
    </vm>""",
)


_DISK_DATA = _TestData(
    conf={
        'address': {
            'bus': '0x00',
            'domain': '0x0000',
            'function': '0x0',
            'slot': '0x05',
            'type': 'pci'
        },
        'bootOrder': '1',
        'device': 'disk',
        'deviceId': '66441539-f7ac-4946-8a25-75e422f939d4',
        'domainID': 'c578566d-bc61-420c-8f1e-8dfa0a18efd5',
        'format': 'raw',
        'iface': 'virtio',
        'imageID': '66441539-f7ac-4946-8a25-75e422f939d4',
        'index': '0',
        'optional': 'false',
        'poolID': '5890a292-0390-01d2-01ed-00000000029a',
        'propagateErrors': 'off',
        'readonly': 'false',
        'shared': 'false',
        'specParams': {},
        'type': 'disk',
        'volumeID': '5c4eeed4-f2a7-490a-ab57-a0d6f3a711cc',
        'vm_custom': {},
        'volumeChain': [{
            'domainID': 'c578566d-bc61-420c-8f1e-8dfa0a18efd5',
            'imageID': '66441539-f7ac-4946-8a25-75e422f939d4',
            'leaseOffset': 109051904,
            'leasePath': '/dev/UUID/leases',
            'path': '/rhev/data-center/omitted/for/brevity',
            'volumeID': '5c4eeed4-f2a7-490a-ab57-a0d6f3a711cc',
        }],
        'volumeInfo': {
            'path': '/rhev/data-center/omitted/for/brevity',
            'volType': 'path',
        }
    },
    metadata_xml="""<?xml version='1.0' encoding='UTF-8'?>
    <vm>
    <device iface="virtio" index="0" type="disk">
        <bootOrder>1</bootOrder>
        <device>disk</device>
        <deviceId>66441539-f7ac-4946-8a25-75e422f939d4</deviceId>
        <domainID>c578566d-bc61-420c-8f1e-8dfa0a18efd5</domainID>
        <format>raw</format>
        <iface>virtio</iface>
        <imageID>66441539-f7ac-4946-8a25-75e422f939d4</imageID>
        <index>0</index>
        <optional>false</optional>
        <poolID>5890a292-0390-01d2-01ed-00000000029a</poolID>
        <propagateErrors>off</propagateErrors>
        <readonly>false</readonly>
        <shared>false</shared>
        <type>disk</type>
        <volumeID>5c4eeed4-f2a7-490a-ab57-a0d6f3a711cc</volumeID>
        <address>
            <bus>0x00</bus>
            <domain>0x0000</domain>
            <function>0x0</function>
            <slot>0x05</slot>
            <type>pci</type>
        </address>
        <specParams />
        <vm_custom />
        <volumeChain>
            <volumeChainNode>
                <domainID>c578566d-bc61-420c-8f1e-8dfa0a18efd5</domainID>
                <imageID>66441539-f7ac-4946-8a25-75e422f939d4</imageID>
                <leaseOffset type="int">109051904</leaseOffset>
                <leasePath>/dev/UUID/leases</leasePath>
                <path>/rhev/data-center/omitted/for/brevity</path>
                <volumeID>5c4eeed4-f2a7-490a-ab57-a0d6f3a711cc</volumeID>
            </volumeChainNode>
        </volumeChain>
        <volumeInfo>
            <path>/rhev/data-center/omitted/for/brevity</path>
            <volType>path</volType>
        </volumeInfo>
    </device>
    </vm>""",
)


_DISK_DATA_CUSTOM = _TestData(
    conf={
        'address': {
            'bus': '0x00',
            'domain': '0x0000',
            'function': '0x0',
            'slot': '0x04',
            'type': 'pci'
        },
        'bootOrder': '2',
        'device': 'disk',
        'deviceId': 'b4f8d9d7-2701-47ae-ab9a-d1e0194eb796',
        'domainID': 'c578566d-bc61-420c-8f1e-8dfa0a18efd5',
        'format': 'raw',
        'iface': 'virtio',
        'imageID': 'b001920b-8c20-4286-a1b7-50cf51bcf7f4',
        'index': '1',
        'optional': 'false',
        'poolID': '5890a292-0390-01d2-01ed-00000000029a',
        'propagateErrors': 'off',
        'readonly': 'false',
        'shared': 'false',
        'specParams': {},
        'type': 'disk',
        'volumeID': '845e57e7-3b16-4cf3-a812-7175f956d2bb',
        'vm_custom': {'viodiskcache': 'writethrough'}
    },
    metadata_xml="""<?xml version='1.0' encoding='UTF-8'?>
    <vm>
    <device iface="virtio" index="1" type="disk">
        <bootOrder>2</bootOrder>
        <device>disk</device>
        <deviceId>b4f8d9d7-2701-47ae-ab9a-d1e0194eb796</deviceId>
        <domainID>c578566d-bc61-420c-8f1e-8dfa0a18efd5</domainID>
        <format>raw</format>
        <iface>virtio</iface>
        <imageID>b001920b-8c20-4286-a1b7-50cf51bcf7f4</imageID>
        <index>1</index>
        <optional>false</optional>
        <poolID>5890a292-0390-01d2-01ed-00000000029a</poolID>
        <propagateErrors>off</propagateErrors>
        <readonly>false</readonly>
        <shared>false</shared>
        <type>disk</type>
        <volumeID>845e57e7-3b16-4cf3-a812-7175f956d2bb</volumeID>
        <address>
            <bus>0x00</bus>
            <domain>0x0000</domain>
            <function>0x0</function>
            <slot>0x04</slot>
            <type>pci</type>
        </address>
        <specParams />
        <vm_custom>
          <viodiskcache>writethrough</viodiskcache>
        </vm_custom>
    </device>
    </vm>""",
)

_DISK_DATA_SGIO = _TestData(
    conf={
        'propagateErrors': 'off',
        'format': 'raw',
        'shared': 'false',
        'type': 'disk',
        'readonly': 'false',
        'specParams': {},
        'sgio': 'unfiltered',
        'iface': 'scsi',
        'deviceId': '07749931-667c-4388-8ba5-4f63ad84a0d7',
        'address': {
            'bus': '0',
            'controller': '0',
            'type': 'drive',
            'target': '0',
            'unit': '0'
        },
        'device': 'lun',
        'discard': False,
        'GUID': '36001405e9bebaa680864c98a280e6544',
        'optional': 'false',
        'vm_custom': {},
    },
    metadata_xml="""<?xml version='1.0' encoding='UTF-8'?>
    <vm>
    <device iface="scsi" guid="36001405e9bebaa680864c98a280e6544" type="disk">
        <GUID>36001405e9bebaa680864c98a280e6544</GUID>
        <device>lun</device>
        <deviceId>07749931-667c-4388-8ba5-4f63ad84a0d7</deviceId>
        <discard type="bool">False</discard>
        <format>raw</format>
        <iface>scsi</iface>
        <optional>false</optional>
        <propagateErrors>off</propagateErrors>
        <readonly>false</readonly>
        <sgio>unfiltered</sgio>
        <shared>false</shared>
        <type>disk</type>
        <address>
            <bus>0</bus>
            <controller>0</controller>
            <target>0</target>
            <type>drive</type>
            <unit>0</unit>
        </address>
        <specParams />
        <vm_custom />
    </device>
    </vm>""",
)


class DescriptorStorageMetadataTests(XMLTestCase):
    # parameters are too long to use permutations

    def test_disk_from_metadata_xml(self):
        self._check_drive_from_metadata_xml(_DISK_DATA)

    def test_disk_to_metadata_xml(self):
        self._check_drive_to_metadata_xml(_DISK_DATA)

    def test_disk_custom_from_metadata_xml(self):
        self._check_drive_from_metadata_xml(_DISK_DATA_CUSTOM)

    def test_disk_custom_to_metadata_xml(self):
        self._check_drive_to_metadata_xml(_DISK_DATA_CUSTOM)

    def test_disk_sgio_from_metadata_xml(self):
        self._check_drive_from_metadata_xml(_DISK_DATA_SGIO)

    def test_disk_sgio_to_metadata_xml(self):
        self._check_drive_to_metadata_xml(_DISK_DATA_SGIO)

    def test_cdrom_from_metadata_xml(self):
        self._check_drive_from_metadata_xml(_CDROM_DATA)

    def test_cdrom_to_metadata_xml(self):
        self._check_drive_to_metadata_xml(_CDROM_DATA)

    def test_cdrom_payload_from_metadata_xml(self):
        self._check_drive_from_metadata_xml(_CDROM_PAYLOAD_DATA)

    def test_cdrom_payload_to_metadata_xml(self):
        self._check_drive_to_metadata_xml(_CDROM_PAYLOAD_DATA)

    def _check_drive_from_metadata_xml(self, data):
        desc = metadata.Descriptor()
        dom = FakeDomain.with_metadata(data.metadata_xml)
        desc.load(dom)
        attrs = common.get_drive_conf_identifying_attrs(data.conf)
        with desc.device(**attrs) as dev:
            self.assertEqual(dev, data.conf)

    def _check_drive_to_metadata_xml(self, data):
        desc = metadata.Descriptor()
        attrs = common.get_drive_conf_identifying_attrs(data.conf)
        with desc.device(**attrs) as dev:
            dev.update(data.conf)
        dom = FakeDomain()
        desc.dump(dom)
        self.assertXMLEqual(
            list(dom.xml.values())[0],
            data.metadata_xml
        )
