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


class FakeDomain(object):

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
