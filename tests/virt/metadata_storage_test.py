#
# Copyright 2017-2020 Red Hat, Inc.
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

from collections import namedtuple
import copy

from vdsm.common import xmlutils
from vdsm.virt.vmdevices import drivename
from vdsm.virt import metadata
from vdsm.virt import vmxml

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
    },
    metadata_xml="""<?xml version='1.0' encoding='UTF-8'?>
    <vm>
    <device devtype="disk" name="hdc">
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
    },
    metadata_xml="""<?xml version='1.0' encoding='UTF-8'?>
    <vm>
    <device devtype="disk" name="hdd">
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
        'type': 'disk',
        'volumeID': '5c4eeed4-f2a7-490a-ab57-a0d6f3a711cc',
        'volumeChain': [{
            'domainID': 'c578566d-bc61-420c-8f1e-8dfa0a18efd5',
            'imageID': '66441539-f7ac-4946-8a25-75e422f939d4',
            'leaseOffset': 109051904,
            'leasePath': '/dev/UUID/leases',
            'path': '/rhev/data-center/omitted/for/brevity',
            'volumeID': '5c4eeed4-f2a7-490a-ab57-a0d6f3a711cc',
        }],
    },
    metadata_xml="""<?xml version='1.0' encoding='UTF-8'?>
    <vm>
    <device devtype="disk" name="vda">
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
        'type': 'disk',
        'volumeID': '845e57e7-3b16-4cf3-a812-7175f956d2bb',
        'vm_custom': {'viodiskcache': 'writethrough'}
    },
    metadata_xml="""<?xml version='1.0' encoding='UTF-8'?>
    <vm>
    <device  devtype="disk" name="vdb">
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
        'sgio': 'unfiltered',
        'index': 1,
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
    },
    metadata_xml="""<?xml version='1.0' encoding='UTF-8'?>
    <vm>
    <device devtype="disk" name="sdb">
        <GUID>36001405e9bebaa680864c98a280e6544</GUID>
        <device>lun</device>
        <deviceId>07749931-667c-4388-8ba5-4f63ad84a0d7</deviceId>
        <discard type="bool">False</discard>
        <format>raw</format>
        <iface>scsi</iface>
        <index type="int">1</index>
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
    </device>
    </vm>""",
)

_DISK_DATA_NETWORK = _TestData(
    conf={
        'device': 'disk',
        'format': 'raw',
        'iface': 'virtio',
        'index': '0',
        'propagateErrors': 'off',
        'readonly': 'False',
        'shared': 'none',
        'type': 'disk',
        'auth': {
            'type': 'ceph',
            'uuid': 'abcdef',
            'username': 'cinder',
        },
        'diskType': 'network',
        'hosts': [
            {
                'name': '1.2.3.41',
                'port': '6789',
                'transport': 'tcp',
            },
            {
                'name': '1.2.3.42',
                'port': '6789',
                'transport': 'tcp',
            },
        ],
        'path': 'poolname/volumename',
        'protocol': 'rbd',
        'serial': '54-a672-23e5b495a9ea',
    },
    metadata_xml="""<?xml version='1.0' encoding='UTF-8'?>
    <vm>
    <device devtype="disk" name="vda">
        <device>disk</device>
        <diskType>network</diskType>
        <format>raw</format>
        <iface>virtio</iface>
        <index>0</index>
        <path>poolname/volumename</path>
        <propagateErrors>off</propagateErrors>
        <protocol>rbd</protocol>
        <readonly>False</readonly>
        <serial>54-a672-23e5b495a9ea</serial>
        <shared>none</shared>
        <type>disk</type>
        <auth>
            <type>ceph</type>
            <username>cinder</username>
            <uuid>abcdef</uuid>
        </auth>
        <hosts>
            <hostInfo>
                <name>1.2.3.41</name>
                <port>6789</port>
                <transport>tcp</transport>
            </hostInfo>
            <hostInfo>
                <name>1.2.3.42</name>
                <port>6789</port>
                <transport>tcp</transport>
            </hostInfo>
        </hosts>
    </device>
    </vm>"""
)

_DISK_DATA_REPLICA = _TestData(
    conf={
        'device': 'disk',
        'format': 'cow',
        'iface': 'virtio',
        'index': '0',
        'path': '/path/to/volume',
        'propagateErrors': 'off',
        'readonly': 'False',
        'shared': 'none',
        'type': 'disk',
        'vm_custom': {'viodiskcache': 'writethrough'},
        'diskReplicate': {
            'cache': 'none',
            'device': 'disk',
            'diskType': 'block',
            'format': 'cow',
            'path': '/path/to/replica',
            'propagateErrors': 'off',
        }
    },
    metadata_xml="""<?xml version='1.0' encoding='UTF-8'?>
    <vm>
    <device devtype="disk" name="vda">
        <device>disk</device>
        <format>cow</format>
        <iface>virtio</iface>
        <index>0</index>
        <path>/path/to/volume</path>
        <propagateErrors>off</propagateErrors>
        <readonly>False</readonly>
        <shared>none</shared>
        <type>disk</type>
        <diskReplicate>
            <cache>none</cache>
            <device>disk</device>
            <diskType>block</diskType>
            <format>cow</format>
            <path>/path/to/replica</path>
            <propagateErrors>off</propagateErrors>
        </diskReplicate>
        <vm_custom>
            <viodiskcache>writethrough</viodiskcache>
        </vm_custom>
    </device>
    </vm>"""
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

    def test_disk_network_from_metadata_xml(self):
        self._check_drive_from_metadata_xml(_DISK_DATA_NETWORK)

    def test_disk_network_to_metadata_xml(self):
        self._check_drive_to_metadata_xml(_DISK_DATA_NETWORK)

    def test_disk_replica_from_metadata_xml(self):
        self._check_drive_from_metadata_xml(_DISK_DATA_REPLICA)

    def test_disk_replica_to_metadata_xml(self):
        self._check_drive_to_metadata_xml(_DISK_DATA_REPLICA)

    def test_disk_ignore_volumeinfo_from_metadata_xml(self):
        xml_snippet = u'''<volumeInfo>
            <path>/rhev/data-center/omitted/for/brevity</path>
            <volType>path</volType>
        </volumeInfo>'''

        root = xmlutils.fromstring(_DISK_DATA.metadata_xml)
        dev = vmxml.find_first(root, 'device')
        vmxml.append_child(dev, etree_child=xmlutils.fromstring(xml_snippet))
        data = _TestData(
            copy.deepcopy(_DISK_DATA.conf), xmlutils.tostring(root))
        self._check_drive_from_metadata_xml(data)

    def test_disk_ignore_volumeinfo_to_metadata_xml(self):
        conf = copy.deepcopy(_DISK_DATA.conf)
        conf['volumeInfo'] = {
            'path': '/rhev/data-center/omitted/for/brevity',
            'volType': 'path',
        }
        data = _TestData(conf, _DISK_DATA.metadata_xml)
        self._check_drive_to_metadata_xml(data)

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
        attrs = _get_drive_conf_identifying_attrs(data.conf)
        with desc.device(**attrs) as dev:
            assert dev == data.conf

    def _check_drive_to_metadata_xml(self, data):
        desc = metadata.Descriptor()
        attrs = _get_drive_conf_identifying_attrs(data.conf)
        with desc.device(**attrs) as dev:
            dev.update(data.conf)
        dom = FakeDomain()
        desc.dump(dom)
        self.assertXMLEqual(
            list(dom.xml.values())[0],
            data.metadata_xml
        )


def _get_drive_conf_identifying_attrs(conf):
    return {
        'devtype': conf['type'],
        'name': drivename.make(conf['iface'], conf['index']),
    }
