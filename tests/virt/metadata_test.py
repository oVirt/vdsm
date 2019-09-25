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
from __future__ import print_function

import logging

from vdsm.common import xmlutils
from vdsm.virt.vmdevices import common
from vdsm.virt.vmdevices import drivename
from vdsm.virt.vmdevices import hwclass
from vdsm.virt.vmdevices import lease
from vdsm.virt.vmdevices import network
from vdsm.virt.vmdevices import storage
from vdsm.virt.vmdevices import storagexml
from vdsm.virt import metadata
from vdsm.virt import xmlconstants

import libvirt

from testlib import permutations, expandPermutations
from testlib import XMLTestCase
from fakemetadatalib import FakeDomain


# NOTE:
# unless otherwise specified, UUIDs are randomly generated
# and have no special meaning


_NESTED_XML = (
    # spacing *IS* significant: here root.text is like '\n       '
    (u'''<ovirt-vm:vm xmlns:ovirt-vm="http://ovirt.org/vm/1.0">
      <ovirt-vm:root type="unknown">
        <ovirt-vm:leaf type="int">42</ovirt-vm:leaf>
      </ovirt-vm:root>
    </ovirt-vm:vm>''',),
    # spacing *IS* significant: here root.text is None
    (u'''<ovirt-vm:vm xmlns:ovirt-vm="http://ovirt.org/vm/1.0">
<ovirt-vm:root type="unknown"><ovirt-vm:leaf type="int">42</ovirt-vm:leaf>
      </ovirt-vm:root>
    </ovirt-vm:vm>''',),
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
            metadata_obj.load(xmlutils.fromstring(test_xml)),
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
            xmlutils.tostring(metadata_obj.dump('vm', version=4.2)),
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
            metadata_obj.load(xmlutils.fromstring(test_xml)),
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
            xmlutils.fromstring(test_xml)
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
        data = metadata_src.load(xmlutils.fromstring(test_xml))
        out_xml = xmlutils.tostring(metadata_dst.dump('vm', **data))
        self.assertXMLEqual(out_xml, expected_xml)

    def test_dump_all(self):
        expected_xml = u'''<ovirt-vm:sequence xmlns:ovirt-vm="http://ovirt.org/vm/1.0">
          <ovirt-vm:item>foo</ovirt-vm:item>
          <ovirt-vm:item>bar</ovirt-vm:item>
          <ovirt-vm:item>True</ovirt-vm:item>
          <ovirt-vm:item>42</ovirt-vm:item>
          <ovirt-vm:item>0.25</ovirt-vm:item>
        </ovirt-vm:sequence>'''
        metadata_obj = metadata.Metadata(
            'ovirt-vm', 'http://ovirt.org/vm/1.0'
        )
        self.assertXMLEqual(
            xmlutils.tostring(
                metadata_obj.dump_sequence(
                    'sequence', 'item', ('foo', 'bar', True, 42, 0.25,)
                )
            ),
            expected_xml
        )

    @permutations(_NESTED_XML)
    def test_skip_nested(self, test_xml):
        metadata_obj = metadata.Metadata(
            'ovirt-vm', 'http://ovirt.org/vm/1.0'
        )
        self.assertEqual(
            metadata_obj.load(xmlutils.fromstring(test_xml)),
            {}
        )


@expandPermutations
class DescriptorTests(XMLTestCase):

    def setUp(self):
        # empty descriptor
        self.md_desc = metadata.Descriptor()

    @permutations([
        # snippet_xml, expected
        ("<ovirt-vm:vm><ovirt-vm:foo>bar</ovirt-vm:foo></ovirt-vm:vm>",
         True),
        ("""<ovirt-vm:vm>
         <ovirt-vm:custom><ovirt-vm:foo>bar</ovirt-vm:foo></ovirt-vm:custom>
         </ovirt-vm:vm>""", True),
        ("", False),
    ])
    def test_bool(self, snippet_xml, expected):
        test_xml = u"""<?xml version="1.0" encoding="utf-8"?>
<domain type="kvm" xmlns:ovirt-vm="http://ovirt.org/vm/1.0">
  <uuid>68c1f97c-9336-4e7a-a8a9-b4f052ababf1</uuid>
  <metadata>
    {snippet}
  </metadata>
</domain>""".format(snippet=snippet_xml)
        md_desc = metadata.Descriptor.from_xml(test_xml)
        self.assertEqual(bool(md_desc), expected)

    def test_from_xml(self):
        test_xml = u"""<?xml version="1.0" encoding="utf-8"?>
<domain type="kvm" xmlns:ovirt-vm="http://ovirt.org/vm/1.0">
  <uuid>68c1f97c-9336-4e7a-a8a9-b4f052ababf1</uuid>
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

    def test_add_custom(self):
        test_xml = u"""<?xml version="1.0" encoding="utf-8"?>
<domain type="kvm" xmlns:ovirt-vm="http://ovirt.org/vm/1.0">
  <uuid>68c1f97c-9336-4e7a-a8a9-b4f052ababf1</uuid>
  <metadata>
    <ovirt-vm:vm>
      <ovirt-vm:custom>
        <ovirt-vm:foo>bar</ovirt-vm:foo>
      </ovirt-vm:custom>
    </ovirt-vm:vm>
  </metadata>
</domain>"""
        md_desc = metadata.Descriptor.from_xml(test_xml)
        md_desc.add_custom({'bee': 'bop'})
        self.assertEqual(md_desc.custom,
                         {'foo': 'bar', 'bee': 'bop'})

    def test_load_overwrites_content(self):
        test_xml = u"""<?xml version="1.0" encoding="utf-8"?>
<domain type="kvm" xmlns:ovirt-vm="http://ovirt.org/vm/1.0">
  <uuid>68c1f97c-9336-4e7a-a8a9-b4f052ababf1</uuid>
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
  <uuid>68c1f97c-9336-4e7a-a8a9-b4f052ababf1</uuid>
  <metadata>
    <ovirt-vm:vm>
      <ovirt-vm:version type="float">4.2</ovirt-vm:version>
      <ovirt-vm:device id="dev0">
        <ovirt-vm:foo>bar</ovirt-vm:foo>
        <ovirt-vm:custom>
          <ovirt-vm:baz type='int'>42</ovirt-vm:baz>
        </ovirt-vm:custom>
      </ovirt-vm:device>
    </ovirt-vm:vm>
  </metadata>
</domain>'''
        md_desc = metadata.Descriptor.from_xml(test_xml)
        with md_desc.device(id='dev0') as dev:
            self.assertEqual(
                dev,
                {'foo': 'bar', 'custom': {'baz': 42}}
            )

    def test_multiple_device_from_xml_tree(self):
        test_xml = u'''<?xml version="1.0" encoding="utf-8"?>
<domain type="kvm" xmlns:ovirt-vm="http://ovirt.org/vm/1.0">
  <uuid>68c1f97c-9336-4e7a-a8a9-b4f052ababf1</uuid>
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
  <uuid>68c1f97c-9336-4e7a-a8a9-b4f052ababf1</uuid>
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
  <uuid>68c1f97c-9336-4e7a-a8a9-b4f052ababf1</uuid>
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

    def test_network_device_custom(self):
        test_xml = u'''<?xml version="1.0" encoding="utf-8"?>
<domain type="kvm" xmlns:ovirt-vm="http://ovirt.org/vm/1.0">
  <uuid>68c1f97c-9336-4e7a-a8a9-b4f052ababf1</uuid>
  <metadata>
    <ovirt-vm:vm>
      <ovirt-vm:device mac_address='00:1a:4a:16:20:30'>
        <ovirt-vm:custom>
  <ovirt-vm:vnic_id>da688238-8b79-4a5f-9f0e-0b207463ff1f</ovirt-vm:vnic_id>
  <ovirt-vm:provider_type>EXTERNAL_NETWORK</ovirt-vm:provider_type>
        </ovirt-vm:custom>
      </ovirt-vm:device>
    </ovirt-vm:vm>
  </metadata>
</domain>'''
        md_desc = metadata.Descriptor.from_xml(test_xml)
        with md_desc.device(mac_address='00:1a:4a:16:20:30') as dev:
            self.assertEqual(
                dev,
                {
                    'custom': {
                        'vnic_id': 'da688238-8b79-4a5f-9f0e-0b207463ff1f',
                        'provider_type': 'EXTERNAL_NETWORK',
                    },
                }
            )

    def test_deeply_nested_metadata_preserved(self):
        conf = {
            'device': 'cdrom',
            'iface': 'ide',
            'index': '3',
            'path': '',
            'readonly': 'true',
            'specParams': {
                'vmPayload': {
                    'file': {
                        'openstack/content/0000': 'something',
                        'openstack/latest/meta_data.json': 'something',
                        'openstack/latest/user_data': 'something',
                    },
                    'volId': 'config-2',
                }
            },
            'type': 'disk',
        }

        expected_xml = """<?xml version='1.0' encoding='UTF-8'?>
<vm>
<device name="hdd" devtype="disk">
  <device>cdrom</device>
    <iface>ide</iface>
    <index>3</index>
    <path />
    <readonly>true</readonly>
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
</vm>"""

        desc = metadata.Descriptor()
        with desc.device(
                devtype=conf['type'],
                name=drivename.make(conf['iface'], conf['index'])) as dev:
            dev.update(conf)
        dom = FakeDomain()
        desc.dump(dom)
        # the first dump() used to -wrongly- modify the supplied data
        desc.dump(dom)
        for produced_xml in dom.xml.values():
            self.assertXMLEqual(produced_xml, expected_xml)

    def test_specParams_ignore_empty(self):
        conf = {
            'device': 'cdrom',
            'iface': 'ide',
            'index': '3',
            'path': '',
            'readonly': 'true',
            'type': 'disk',
        }
        source_xml = """<?xml version='1.0' encoding='UTF-8'?>
<domain type="kvm" xmlns:ovirt-vm="http://ovirt.org/vm/1.0">
<metadata>
  <ovirt-vm:vm>
    <ovirt-vm:device name="hdd" devtype="disk">
      <ovirt-vm:specParams />
      </ovirt-vm:device>
    </ovirt-vm:vm>
  </metadata>
</domain>"""
        desc = metadata.Descriptor.from_xml(source_xml)
        with desc.device(
                devtype=conf['type'],
                name=drivename.make(conf['iface'], conf['index'])) as dev:
            # test we ignore IO tune settings from metadata, should we
            # (unexpectedly) found it (see below to learn why)
            self.assertEqual(dev, {'specParams': {}})

    def test_specParams_ignore_iotune(self):
        conf = {
            'device': 'cdrom',
            'iface': 'ide',
            'index': '3',
            'path': '',
            'readonly': 'true',
            'type': 'disk',
        }
        source_xml = """<?xml version='1.0' encoding='UTF-8'?>
<domain type="kvm" xmlns:ovirt-vm="http://ovirt.org/vm/1.0">
<metadata>
  <ovirt-vm:vm>
    <ovirt-vm:device name="hdd" devtype="disk">
      <ovirt-vm:specParams>
        <ovirt-vm:ioTune>
          <ovirt-vm:read_iops_sec>100</ovirt-vm:read_iops_sec>
        </ovirt-vm:ioTune>
      </ovirt-vm:specParams>
      </ovirt-vm:device>
    </ovirt-vm:vm>
  </metadata>
</domain>"""
        desc = metadata.Descriptor.from_xml(source_xml)
        with desc.device(
                devtype=conf['type'],
                name=drivename.make(conf['iface'], conf['index'])) as dev:
            # test we ignore IO tune settings from metadata, should we
            # (unexpectedly) found it (see below to learn why)
            self.assertEqual(dev, {'specParams': {}})

        expected_xml = u"""<?xml version='1.0' encoding='utf-8'?>
<ovirt-vm:vm xmlns:ovirt-vm="http://ovirt.org/vm/1.0">
    <ovirt-vm:device devtype="disk" name="hdd" />
</ovirt-vm:vm>"""
        # test we never serialize IO tune settings - they belong
        # in the plain regular libvirt domain XML.
        self.assertXMLEqual(desc.to_xml(), expected_xml)


class SaveDeviceMetadataTests(XMLTestCase):

    EMPTY_XML = """<?xml version='1.0' encoding='UTF-8'?>
<ovirt-vm:vm xmlns:ovirt-vm="http://ovirt.org/vm/1.0" />"""

    def setUp(self):
        # empty descriptor
        self.md_desc = metadata.Descriptor()
        self.log = logging.getLogger('test')

    def test_no_devices(self):
        dev_map = common.empty_dev_map()
        common.save_device_metadata(self.md_desc, dev_map, self.log)
        self.assertXMLEqual(self.md_desc.to_xml(), self.EMPTY_XML)

    def test_device_no_metadata(self):
        dev_map = common.empty_dev_map()
        dev_map[hwclass.LEASE].append(lease.Device(
            self.log, lease_id='l', sd_id='s', path='p', offset='0')
        )
        common.save_device_metadata(self.md_desc, dev_map, self.log)
        self.assertXMLEqual(self.md_desc.to_xml(), self.EMPTY_XML)

    def test_devices_network(self):
        nic_params = {
            'device': hwclass.NIC,
            'macAddr': '00:00:00:00:00:00',
            'network': 'test',
            'portMirroring': ['net1', 'net2'],
        }
        expected_xml = """<ovirt-vm:vm xmlns:ovirt-vm="http://ovirt.org/vm/1.0">
    <ovirt-vm:device mac_address="00:00:00:00:00:00">
        <ovirt-vm:network>test</ovirt-vm:network>
        <ovirt-vm:portMirroring>
          <ovirt-vm:network>net1</ovirt-vm:network>
          <ovirt-vm:network>net2</ovirt-vm:network>
        </ovirt-vm:portMirroring>
    </ovirt-vm:device>
</ovirt-vm:vm>"""
        dev_map = common.empty_dev_map()
        dev_map[hwclass.NIC].append(network.Interface(self.log, **nic_params))
        common.save_device_metadata(self.md_desc, dev_map, self.log)
        self.assertXMLEqual(self.md_desc.to_xml(), expected_xml)

    def test_devices_storage(self):
        drive_params = {
            'device': hwclass.DISK,
            'iface': 'sata',
            'index': 0,
            # *ID are not validated by the Drive class
            'shared': 'transient',
            'domainID': 'domainID',
            'imageID': 'imageID',
            'poolID': 'poolID',
            'volumeID': 'volumeID',
        }
        expected_xml = """<ovirt-vm:vm xmlns:ovirt-vm="http://ovirt.org/vm/1.0">
    <ovirt-vm:device devtype="disk" name="sda">
        <ovirt-vm:domainID>domainID</ovirt-vm:domainID>
        <ovirt-vm:imageID>imageID</ovirt-vm:imageID>
        <ovirt-vm:poolID>poolID</ovirt-vm:poolID>
        <ovirt-vm:shared>transient</ovirt-vm:shared>
        <ovirt-vm:volumeID>volumeID</ovirt-vm:volumeID>
    </ovirt-vm:device>
</ovirt-vm:vm>"""
        dev_map = common.empty_dev_map()
        dev_map[hwclass.DISK].append(storage.Drive(self.log, **drive_params))
        common.save_device_metadata(self.md_desc, dev_map, self.log)
        self.assertXMLEqual(self.md_desc.to_xml(), expected_xml)

    def test_devices_storage_iotune(self):
        drive_params = {
            'index': 0,
            'cache': 'none',
            'name': 'vda',
            'format': 'raw',
            'bootOrder': '1',
            'discard': False,
            'diskType': 'block',
            'specParams': {
                'ioTune': {
                    'write_bytes_sec': 0,
                    'total_iops_sec': 0,
                    'read_iops_sec': 100,
                    'read_bytes_sec': 0,
                    'write_iops_sec': 100,
                    'total_bytes_sec': 0
                }
            },
            'iface': 'virtio',
            'device': 'disk',
            'path': '/some/path',
            'serial': 'random-serial',
            'propagateErrors': 'off',
            'type': 'disk',
            # *ID are not validated by the Drive class
            'shared': 'transient',
            'domainID': 'domainID',
            'imageID': 'imageID',
            'poolID': 'poolID',
            'volumeID': 'volumeID',
        }
        expected_xml = """<ovirt-vm:vm xmlns:ovirt-vm="http://ovirt.org/vm/1.0">
    <ovirt-vm:device devtype="disk" name="vda">
        <ovirt-vm:domainID>domainID</ovirt-vm:domainID>
        <ovirt-vm:imageID>imageID</ovirt-vm:imageID>
        <ovirt-vm:poolID>poolID</ovirt-vm:poolID>
        <ovirt-vm:shared>transient</ovirt-vm:shared>
        <ovirt-vm:volumeID>volumeID</ovirt-vm:volumeID>
        <ovirt-vm:specParams />
    </ovirt-vm:device>
</ovirt-vm:vm>"""
        dev_map = common.empty_dev_map()
        dev_map[hwclass.DISK].append(storage.Drive(self.log, **drive_params))
        common.save_device_metadata(self.md_desc, dev_map, self.log)
        self.assertXMLEqual(self.md_desc.to_xml(), expected_xml)

    def test_overwrite_existing_data(self):
        test_xml = u'''<?xml version="1.0" encoding="utf-8"?>
<domain type="kvm" xmlns:ovirt-vm="http://ovirt.org/vm/1.0">
  <uuid>68c1f97c-9336-4e7a-a8a9-b4f052ababf1</uuid>
  <metadata>
    <ovirt-vm:vm>
      <ovirt-vm:device mac_address='00:1a:4a:16:20:30'>
        <ovirt-vm:custom>
  <ovirt-vm:vnic_id>da688238-8b79-4a5f-9f0e-0b207463ff1f</ovirt-vm:vnic_id>
  <ovirt-vm:provider_type>EXTERNAL_NETWORK</ovirt-vm:provider_type>
        </ovirt-vm:custom>
      </ovirt-vm:device>
    </ovirt-vm:vm>
  </metadata>
</domain>'''
        md_desc = metadata.Descriptor.from_xml(test_xml)
        nic_params = {
            'device': hwclass.NIC,
            'macAddr': '00:1a:4a:16:20:30',
            'network': 'test',
        }
        expected_xml = """<ovirt-vm:vm xmlns:ovirt-vm="http://ovirt.org/vm/1.0">
    <ovirt-vm:device mac_address="00:1a:4a:16:20:30">
        <ovirt-vm:network>test</ovirt-vm:network>
    </ovirt-vm:device>
</ovirt-vm:vm>"""
        dev_map = common.empty_dev_map()
        dev_map[hwclass.NIC].append(network.Interface(self.log, **nic_params))
        common.save_device_metadata(md_desc, dev_map, self.log)
        self.assertXMLEqual(md_desc.to_xml(), expected_xml)

    def test_disk_specParams_payload_device(self):
        payload_params = {
            u'index': u'3',
            u'iface': u'ide',
            u'specParams': {
                u'vmPayload': {
                    u'volId': u'config-2',
                    u'file': {
                        u'openstack/content/0000': u'A',
                        u'openstack/latest/meta_data.json': u'B',
                        u'openstack/latest/user_data': u'C',
                    }
                }
            },
            u'readonly': u'true',
            u'deviceId': u'e5cd57aa-6eb6-424b-b16b-83ef07bf9cca',
            u'shared': u'false',
            u'device': u'cdrom',
            u'path': u'',
            u'type': u'disk'
        }
        expected_xml = """<ovirt-vm:vm xmlns:ovirt-vm="http://ovirt.org/vm/1.0">
<ovirt-vm:device name="hdd" devtype="disk">
  <ovirt-vm:deviceId>e5cd57aa-6eb6-424b-b16b-83ef07bf9cca</ovirt-vm:deviceId>
    <ovirt-vm:shared>false</ovirt-vm:shared>
    <ovirt-vm:specParams>
     <ovirt-vm:vmPayload>
       <ovirt-vm:volId>config-2</ovirt-vm:volId>
       <ovirt-vm:file path='openstack/content/0000'>A</ovirt-vm:file>
       <ovirt-vm:file path='openstack/latest/meta_data.json'>B</ovirt-vm:file>
       <ovirt-vm:file path='openstack/latest/user_data'>C</ovirt-vm:file>
     </ovirt-vm:vmPayload>
    </ovirt-vm:specParams>
  </ovirt-vm:device>
</ovirt-vm:vm>"""
        dev_map = common.empty_dev_map()
        dev_map[hwclass.DISK].append(
            storage.Drive(self.log, **payload_params))
        common.save_device_metadata(self.md_desc, dev_map, self.log)
        self.assertXMLEqual(self.md_desc.to_xml(), expected_xml)

    # TODO: simulate read-create-write cycle with network or storage device


_DRIVE_TRANSIENT_DISK_XML = u"""<?xml version="1.0" encoding="utf-8"?>
<domain type="kvm" xmlns:ovirt-vm="http://ovirt.org/vm/1.0">
  <uuid>68c1f97c-9336-4e7a-a8a9-b4f052ababf1</uuid>
  <devices>
      <disk device="disk" snapshot="no" type="block">
        <source dev="/var/lib/vdsm/transient"/>
        <target bus="scsi" dev="sda"/>
        <serial>54-a672-23e5b495a9ea</serial>
        <driver cache="writethrough" error_policy="stop"
                io="native" name="qemu" type="qcow2"/>
      </disk>
  </devices>
  <metadata>
    <ovirt-vm:vm>
        <ovirt-vm:device devtype="disk" name="sda">
            <ovirt-vm:domainID>domainID</ovirt-vm:domainID>
            <ovirt-vm:imageID>imageID</ovirt-vm:imageID>
            <ovirt-vm:poolID>poolID</ovirt-vm:poolID>
            <ovirt-vm:shared>transient</ovirt-vm:shared>
            <ovirt-vm:volumeID>volumeID</ovirt-vm:volumeID>
        </ovirt-vm:device>
    </ovirt-vm:vm>
  </metadata>
</domain>"""


class MetadataFromXMLTests(XMLTestCase):

    def test_shared_from_metadata(self):

        md_desc = metadata.Descriptor.from_xml(_DRIVE_TRANSIENT_DISK_XML)
        root = xmlutils.fromstring(_DRIVE_TRANSIENT_DISK_XML)

        dev_xml = root.find('./devices/disk')

        with md_desc.device(devtype='disk', name='sda') as meta:
            dev_obj = storage.Drive(
                self.log, **storagexml.parse(dev_xml, meta)
            )
            self.assertEqual(
                dev_obj['shared'], storage.DRIVE_SHARED_TYPE.TRANSIENT
            )
