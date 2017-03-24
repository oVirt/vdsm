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

from vdsm.virt import metadata
from vdsm.virt import vmxml
from vdsm.virt import xmlconstants

import libvirt

from vmfakecon import Error
from testlib import permutations, expandPermutations
from testlib import XMLTestCase


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

    @permutations([
        # custom
        [{}],
        [{'str': 'a'}],
        [{'int': 42}],
        [{'float': 0.333}],
        [{'str': 'a', 'int': 42, 'float': 0.333}],
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


class DomainTests(XMLTestCase):

    def test_empty_get(self):
        dom = FakeDomain()
        with metadata.domain(
            dom,
            xmlconstants.METADATA_VM_VDSM_ELEMENT,
            namespace=xmlconstants.METADATA_VM_VDSM_PREFIX,
            namespace_uri=xmlconstants.METADATA_VM_VDSM_URI
        ) as value:
            self.assertEqual(value, {})

    def test_context_creates_missing_element(self):
        # libvirt takes care of namespace massaging
        expected_xml = u'''<vm />'''
        dom = FakeDomain()
        with metadata.domain(
            dom,
            xmlconstants.METADATA_VM_VDSM_ELEMENT,
            namespace=xmlconstants.METADATA_VM_VDSM_PREFIX,
            namespace_uri=xmlconstants.METADATA_VM_VDSM_URI
        ):
            # awkward, you want to use metadata.create()
            # in the real code
            pass  # do nothing
        self.assertXMLEqual(
            dom.xml.get(xmlconstants.METADATA_VM_VDSM_URI),
            expected_xml
        )

    def test_update(self):
        # libvirt takes care of namespace massaging
        base_xml = u'''<vm>
          <foobar type="int">21</foobar>
        </vm>'''
        expected_xml = u'''<vm>
          <foobar type="int">42</foobar>
          <beer>cold</beer>
        </vm>'''
        dom = FakeDomain()
        dom.xml[xmlconstants.METADATA_VM_VDSM_URI] = base_xml
        with metadata.domain(
            dom,
            xmlconstants.METADATA_VM_VDSM_ELEMENT,
            namespace=xmlconstants.METADATA_VM_VDSM_PREFIX,
            namespace_uri=xmlconstants.METADATA_VM_VDSM_URI
        ) as vm:
            vm['foobar'] = 42
            vm['beer'] = 'cold'
        self.assertXMLEqual(
            dom.xml.get(xmlconstants.METADATA_VM_VDSM_URI),
            expected_xml
        )


@expandPermutations
class DeviceTests(XMLTestCase):

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
         "<beer>true</beer>"
         "</device>"
         "</vm>",
         {'foo': 42, 'beer': 'true'}],
    ])
    def test_get(self, dom_xml, expected_dev):
        dom = FakeDomain.with_metadata(dom_xml)
        with metadata.device(dom, device_id='alias0') as dev:
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
    def test_update(self, dom_xml):
        expected_res = {
            'flag': 'true',
            'mode': 42,
        }
        dom = FakeDomain.with_metadata(dom_xml)
        with metadata.device(dom, device_id='alias0') as dev:
            dev['mode'] = 42
            dev['flag'] = 'true'
            dev.pop('removeme', None)

        # troubleshooting helper should the test fail
        print(dom.metadata(
            libvirt.VIR_DOMAIN_METADATA_ELEMENT,
            xmlconstants.METADATA_VM_VDSM_URI,
            0
        ))
        # our assertXMLEqual consider the order of XML attributes
        # significant, while according to XML spec is not.
        with metadata.device(dom, device_id='alias0') as dev:
            self.assertEqual(dev, expected_res)

    def test_clear(self):
        dom_xml = u'''<vm>
            <device id='alias0'>
                <mode>12</mode>
            </device>
        </vm>'''
        expected_xml = u'''<vm>
            <device id='alias0' />
        </vm>'''
        dom = FakeDomain.with_metadata(dom_xml)
        with metadata.device(dom, device_id='alias0') as dev:
            dev.clear()

        produced_xml = dom.metadata(
            libvirt.VIR_DOMAIN_METADATA_ELEMENT,
            xmlconstants.METADATA_VM_VDSM_URI,
            0
        )
        self.assertXMLEqual(produced_xml, expected_xml)


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
