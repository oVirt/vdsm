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
from vdsm.virt import vmxml
from vdsm.virt import xmlconstants

import libvirt

from vmfakecon import Error
from testlib import permutations, expandPermutations
from testlib import XMLTestCase


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


@expandPermutations
class MetadataTests(XMLTestCase):

    def setUp(self):
        self.md = metadata.Metadata()

    @permutations([
        # custom
        [{'none': None}],
        [{'dict': {}}],
        [{'list': []}],
        [{'set': set()}],
    ])
    def test_unsupported_types(self, custom):
        self.assertRaises(
            metadata.UnsupportedType,
            self.md.dump,
            'test',
            **custom
        )

    def test_unsupported_type_dump_key_in_exception(self):
        KEY = "versions"
        data = {KEY: set()}
        metadata_obj = metadata.Metadata(
            'ovirt-vm', 'http://ovirt.org/vm/1.0'
        )
        try:
            metadata_obj.dump('test', **data)
        except metadata.UnsupportedType as exc:
            self.assertIn(KEY, str(exc))
        else:
            raise AssertionError('dump() did not raise!')

    @permutations([
        # custom
        [{}],
        [{'str': 'a'}],
        [{'int': 42}],
        [{'float': 0.333}],
        [{'bool': True}],
        [{'bool2': False}],
        [{'str': 'a', 'int': 42, 'float': 0.333, 'bool': True}],
        [{'name': 'something'}],
    ])
    def test_roundtrip(self, custom):
        elem = self.md.dump('test', **custom)
        restored = self.md.load(elem)
        self.assertEqual(custom, restored)

    def test_load_ns(self):
        test_xml = u'''<ovirt-vm:vm xmlns:ovirt-vm="http://ovirt.org/vm/1.0">
          <ovirt-vm:version type="float">4.2</ovirt-vm:version>
        </ovirt-vm:vm>'''
        metadata_obj = metadata.Metadata(
            'ovirt-vm', 'http://ovirt.org/vm/1.0'
        )
        self.assertEqual(
            metadata_obj.load(vmxml.parse_xml(test_xml)),
            {'version': 4.2}
        )

    def test_dump_ns(self):
        expected_xml = u'''<ovirt-vm:vm xmlns:ovirt-vm="http://ovirt.org/vm/1.0">
          <ovirt-vm:version type="float">4.2</ovirt-vm:version>
        </ovirt-vm:vm>'''
        metadata_obj = metadata.Metadata(
            'ovirt-vm', 'http://ovirt.org/vm/1.0'
        )
        self.assertXMLEqual(
            vmxml.format_xml(metadata_obj.dump('vm', version=4.2)),
            expected_xml
        )

    @permutations([
        # custom
        [{}],
        [{'version': 4.2}],
        [{'mode': 'fast', 'version': 5.0}],
    ])
    def test_create(self, custom):
        namespace = 'ovirt-vm'
        namespace_uri = 'http://ovirt.org/vm/1.0'
        elem = metadata.create(
            'vm', namespace, namespace_uri,
            **custom
        )
        metadata_obj = metadata.Metadata(namespace, namespace_uri)
        self.assertEqual(metadata_obj.load(elem), custom)

    @permutations([
        # elem_type
        ['str'],
        [None],
    ])
    def test_load_empty(self, elem_type):
        elem_spec = (
            '' if elem_type is None else
            'type="{elem_type}"'.format(elem_type=elem_type)
        )
        test_xml = u'''<ovirt-vm:vm xmlns:ovirt-vm="http://ovirt.org/vm/1.0">
          <ovirt-vm:param {elem_spec}/>
        </ovirt-vm:vm>'''.format(elem_spec=elem_spec)
        metadata_obj = metadata.Metadata(
            'ovirt-vm', 'http://ovirt.org/vm/1.0'
        )
        self.assertEqual(
            metadata_obj.load(vmxml.parse_xml(test_xml)),
            {'param': ''}
        )

    @permutations([
        # elem_type
        ['bool'],
        ['int'],
        ['float'],
    ])
    def test_load_empty_not_string(self, elem_type):
        test_xml = u'''<ovirt-vm:vm xmlns:ovirt-vm="http://ovirt.org/vm/1.0">
          <ovirt-vm:param type="{elem_type}" />
        </ovirt-vm:vm>'''.format(elem_type=elem_type)
        metadata_obj = metadata.Metadata(
            'ovirt-vm', 'http://ovirt.org/vm/1.0'
        )
        self.assertRaises(
            ValueError,
            metadata_obj.load,
            vmxml.parse_xml(test_xml)
        )

    @permutations([
        # elem_type
        ['str'],
        [None],
    ])
    def test_roundtrip_empty(self, elem_type):
        elem_spec = (
            '' if elem_type is None else
            'type="{elem_type}"'.format(elem_type=elem_type)
        )
        test_xml = u'''<ovirt-vm:vm xmlns:ovirt-vm="http://ovirt.org/vm/1.0">
          <ovirt-vm:param {elem_spec}/>
        </ovirt-vm:vm>'''.format(elem_spec=elem_spec)
        expected_xml = u'''<ovirt-vm:vm xmlns:ovirt-vm="http://ovirt.org/vm/1.0">
          <ovirt-vm:param />
        </ovirt-vm:vm>'''

        metadata_src = metadata.Metadata(
            'ovirt-vm', 'http://ovirt.org/vm/1.0'
        )
        metadata_dst = metadata.Metadata(
            'ovirt-vm', 'http://ovirt.org/vm/1.0'
        )
        data = metadata_src.load(vmxml.parse_xml(test_xml))
        out_xml = vmxml.format_xml(metadata_dst.dump('vm', **data))
        self.assertXMLEqual(out_xml, expected_xml)


@expandPermutations
class DescriptorTests(XMLTestCase):

    def setUp(self):
        # empty descriptor
        self.md_desc = metadata.Descriptor()

    def test_from_xml(self):
        test_xml = u"""<?xml version="1.0" encoding="utf-8"?>
<domain type="kvm" xmlns:ovirt-vm="http://ovirt.org/vm/1.0">
  <metadata>
    <ovirt-vm:vm>
      <ovirt-vm:version type="float">4.2</ovirt-vm:version>
      <ovirt-vm:custom>
        <ovirt-vm:foo>bar</ovirt-vm:foo>
      </ovirt-vm:custom>
    </ovirt-vm:vm>
  </metadata>
</domain>"""
        md_desc = metadata.Descriptor.from_xml(test_xml)
        with md_desc.values() as vals:
            self.assertEqual(vals, {'version': 4.2})
        self.assertEqual(md_desc.custom, {'foo': 'bar'})

    def test_load_overwrites_content(self):
        test_xml = u"""<?xml version="1.0" encoding="utf-8"?>
<domain type="kvm" xmlns:ovirt-vm="http://ovirt.org/vm/1.0">
  <metadata>
    <ovirt-vm:vm>
      <ovirt-vm:version type="float">4.2</ovirt-vm:version>
      <ovirt-vm:custom>
        <ovirt-vm:foo>bar</ovirt-vm:foo>
      </ovirt-vm:custom>
    </ovirt-vm:vm>
  </metadata>
</domain>"""
        md_desc = metadata.Descriptor.from_xml(test_xml)
        dom = FakeDomain()
        md_desc.load(dom)
        with md_desc.values() as vals:
            self.assertEqual(vals, {})
        self.assertEqual(md_desc.custom, {})

    def test_empty_get(self):
        dom = FakeDomain()
        self.md_desc.load(dom)
        with self.md_desc.values() as vals:
            self.assertEqual(vals, {})

    def test_context_creates_missing_element(self):
        # libvirt takes care of namespace massaging
        expected_xml = u'''<vm />'''
        dom = FakeDomain()
        self.md_desc.dump(dom)
        self.assertXMLEqual(
            dom.xml.get(xmlconstants.METADATA_VM_VDSM_URI),
            expected_xml
        )

    def test_update_domain(self):
        # libvirt takes care of namespace massaging
        base_xml = u'''<vm>
          <foobar type="int">21</foobar>
        </vm>'''
        expected_xml = u'''<vm>
          <beer>cold</beer>
          <foobar type="int">42</foobar>
        </vm>'''
        dom = FakeDomain()
        dom.xml[xmlconstants.METADATA_VM_VDSM_URI] = base_xml
        self.md_desc.load(dom)
        with self.md_desc.values() as vals:
            vals['foobar'] = 42
            vals['beer'] = 'cold'
        self.md_desc.dump(dom)
        self.assertXMLEqual(
            dom.xml.get(xmlconstants.METADATA_VM_VDSM_URI),
            expected_xml
        )

    @permutations([
        # dom_xml, expected_dev
        [None, {}],
        ["<vm>"
         "<device id='NOMATCH'>"
         "<foobar type='int'>42</foobar>"
         "</device>"
         "</vm>",
         {}],
        ["<vm>"
         "<device id='alias0'>"
         "<foobar type='int'>42</foobar>"
         "</device>"
         "</vm>",
         {'foobar': 42}],
        ["<vm>"
         "<device id='alias0'>"
         "<foo type='int'>42</foo>"
         "<beer type='bool'>true</beer>"
         "</device>"
         "</vm>",
         {'foo': 42, 'beer': True}],
    ])
    def test_get_device(self, dom_xml, expected_dev):
        dom = FakeDomain.with_metadata(dom_xml)
        self.md_desc.load(dom)
        with self.md_desc.device(id='alias0') as dev:
            self.assertEqual(dev, expected_dev)

    @permutations([
        # dom_xml
        [None],
        ["<vm>"
         "<device id='alias0'>"
         "<mode>12</mode>"
         "<removeme>true</removeme>"
         "</device>"
         "</vm>"],
    ])
    def test_update_device(self, dom_xml):
        expected_res = {
            'flag': True,
            'mode': 42,
        }
        dom = FakeDomain.with_metadata(dom_xml)
        self.md_desc.load(dom)
        with self.md_desc.device(id='alias0') as dev:
            dev['mode'] = 42
            dev['flag'] = True
            dev.pop('removeme', None)
        self.md_desc.dump(dom)

        # troubleshooting helper should the test fail
        print(dom.metadata(
            libvirt.VIR_DOMAIN_METADATA_ELEMENT,
            xmlconstants.METADATA_VM_VDSM_URI,
            0
        ))
        # our assertXMLEqual consider the order of XML attributes
        # significant, while according to XML spec is not.
        with self.md_desc.device(id='alias0') as dev:
            self.assertEqual(dev, expected_res)

    def test_clear(self):
        dom_xml = u'''<vm>
            <device id='alias0'>
                <mode>12</mode>
            </device>
        </vm>'''
        expected_xml = u'''<vm/>'''
        dom = FakeDomain.with_metadata(dom_xml)
        self.md_desc.load(dom)
        with self.md_desc.device(id='alias0') as dev:
            dev.clear()
        self.md_desc.dump(dom)

        produced_xml = dom.metadata(
            libvirt.VIR_DOMAIN_METADATA_ELEMENT,
            xmlconstants.METADATA_VM_VDSM_URI,
            0
        )
        self.assertXMLEqual(produced_xml, expected_xml)

    def test_lookup_partial_attributes(self):
        dom_xml = u'''<vm>
            <device id='alias0' type='fancydev'>
                <mode type="int">42</mode>
            </device>
            <device id='alias1'>
                <mode type="int">33</mode>
            </device>
        </vm>'''
        dom = FakeDomain.with_metadata(dom_xml)
        self.md_desc.load(dom)
        with self.md_desc.device(type='fancydev') as dev:
            self.assertEqual(dev, {'mode': 42})

    def test_lookup_fail(self):
        dom_xml = u'''<vm>
            <device id='alias0' type='fancydev'>
                <mode type="int">42</mode>
            </device>
        </vm>'''
        dom = FakeDomain.with_metadata(dom_xml)
        self.md_desc.load(dom)
        with self.md_desc.device(id='alias999', type='fancydev') as dev:
            self.assertEqual(dev, {})

    def test_lookup_ambiguous_raises(self):
        dom_xml = u'''<vm>
            <device type='fancydev'>
                <mode type="int">1</mode>
            </device>
            <device type='fancydev'>
                <mode type="int">2</mode>
            </device>
        </vm>'''
        dom = FakeDomain.with_metadata(dom_xml)
        self.md_desc.load(dom)
        with self.assertRaises(metadata.MissingDevice):
            with self.md_desc.device(type='fancydev'):
                pass

    def test_lookup_multiple_devices(self):
        dom_xml = u'''<vm>
            <device id='alias0' type='devA' addr='pci_0000_00_1a_0'>
                <mode type="int">1200</mode>
            </device>
            <device id='alias1' type='devA' addr='pci_0000_00_02_0'>
                <mode type="int">900</mode>
            </device>
            <device id='alias2' type='devC' addr='pci_0000_00_1f_2'>
                <mode type="int">1440</mode>
            </device>
        </vm>'''
        dom = FakeDomain.with_metadata(dom_xml)
        self.md_desc.load(dom)
        with self.md_desc.device(addr='pci_0000_00_02_0') as dev:
            self.assertEqual(dev, {'mode': 900})

    def test_all_devices(self):
        dom_xml = u'''<vm>
            <device type='fancydev'>
                <mode type="int">1</mode>
            </device>
            <device class='otherdev' foo='1'>
                <mode type="float">0.333</mode>
            </device>
            <device type='fancydev' extra='ignored'>
                <mode type="int">2</mode>
            </device>
        </vm>'''
        dom = FakeDomain.with_metadata(dom_xml)
        self.md_desc.load(dom)
        self.assertEqual(
            list(self.md_desc.all_devices(type='fancydev')),
            [{'mode': 1}, {'mode': 2}]
        )

    def test_all_devices_copy_data(self):
        dom_xml = u'''<vm>
            <device type='fancydev'>
                <mode type="int">1</mode>
            </device>
            <device type='fancydev' extra='ignored'>
                <mode type="int">2</mode>
            </device>
        </vm>'''
        dom = FakeDomain.with_metadata(dom_xml)
        self.md_desc.load(dom)

        for dev in self.md_desc.all_devices(type='fancydev'):
            dev['mode'] = 3

        self.assertEqual(
            list(self.md_desc.all_devices(type='fancydev')),
            [{'mode': 1}, {'mode': 2}]
        )

    def test_device_from_xml_tree(self):
        test_xml = u'''<?xml version="1.0" encoding="utf-8"?>
<domain type="kvm" xmlns:ovirt-vm="http://ovirt.org/vm/1.0">
  <metadata>
    <ovirt-vm:vm>
      <ovirt-vm:version type="float">4.2</ovirt-vm:version>
      <ovirt-vm:device id="dev0">
        <ovirt-vm:foo>bar</ovirt-vm:foo>
      </ovirt-vm:device>
    </ovirt-vm:vm>
  </metadata>
</domain>'''
        md_desc = metadata.Descriptor.from_xml(test_xml)
        with md_desc.device(id='dev0') as dev:
            self.assertEqual(dev, {'foo': 'bar'})

    def test_multiple_device_from_xml_tree(self):
        test_xml = u'''<?xml version="1.0" encoding="utf-8"?>
<domain type="kvm" xmlns:ovirt-vm="http://ovirt.org/vm/1.0">
  <metadata>
    <ovirt-vm:vm>
      <ovirt-vm:version type="float">4.2</ovirt-vm:version>
      <ovirt-vm:device id="dev0">
        <ovirt-vm:foo>bar</ovirt-vm:foo>
      </ovirt-vm:device>
      <ovirt-vm:device id="dev1">
        <ovirt-vm:number type="int">42</ovirt-vm:number>
      </ovirt-vm:device>
    </ovirt-vm:vm>
  </metadata>
</domain>'''
        md_desc = metadata.Descriptor.from_xml(test_xml)
        found = []
        for dev_id in ('dev0', 'dev1'):
            with md_desc.device(id=dev_id) as dev:
                found.append(dev.copy())
        self.assertEqual(
            found,
            [{'foo': 'bar'}, {'number': 42}]
        )

    def test_unknown_device_from_xml_tree(self):
        test_xml = u'''<?xml version="1.0" encoding="utf-8"?>
<domain type="kvm" xmlns:ovirt-vm="http://ovirt.org/vm/1.0">
  <metadata>
    <ovirt-vm:vm>
      <ovirt-vm:version type="float">4.2</ovirt-vm:version>
    </ovirt-vm:vm>
  </metadata>
</domain>'''
        md_desc = metadata.Descriptor.from_xml(test_xml)
        with md_desc.device(id='mydev') as dev:
            self.assertEqual(dev, {})

    def test_nested_device_from_xml_tree(self):
        test_xml = u'''<?xml version="1.0" encoding="utf-8"?>
<domain type="kvm" xmlns:ovirt-vm="http://ovirt.org/vm/1.0">
  <metadata>
    <ovirt-vm:vm>
      <ovirt-vm:version type="float">4.2</ovirt-vm:version>
      <ovirt-vm:device id="dev0">
        <ovirt-vm:foo>bar</ovirt-vm:foo>
      </ovirt-vm:device>
    </ovirt-vm:vm>
  </metadata>
</domain>'''
        md_desc = metadata.Descriptor.from_xml(test_xml)
        with md_desc.device(id='dev0') as dev:
            self.assertEqual(dev, {'foo': 'bar'})


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


class FakeDomain(object):

    @classmethod
    def with_metadata(
        cls,
        xml_string,
        prefix=xmlconstants.METADATA_VM_VDSM_PREFIX,
        uri=xmlconstants.METADATA_VM_VDSM_URI
    ):
        dom = cls()
        if xml_string:
            dom.setMetadata(
                libvirt.VIR_DOMAIN_METADATA_ELEMENT,
                xml_string, prefix, uri, 0
            )
        return dom

    def __init__(self):
        self.xml = {}

    def metadata(self, xml_type, uri, flags):
        # we only support METADATA_ELEMENT
        assert xml_type == libvirt.VIR_DOMAIN_METADATA_ELEMENT
        xml_string = self.xml.get(uri, None)
        if xml_string is None:
            raise Error(libvirt.VIR_ERR_NO_DOMAIN_METADATA)
        return xml_string

    def setMetadata(self, xml_type, xml_string, prefix, uri, flags):
        self.xml[uri] = xml_string
