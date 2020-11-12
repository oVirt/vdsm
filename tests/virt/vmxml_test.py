# -*- coding: utf-8 -*-
#
# Copyright 2014-2020 Red Hat, Inc.
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

import os.path
import re
import timeit
import xml.etree.ElementTree as etree

from vdsm.common import cpuarch
from vdsm.common import xmlutils
from vdsm.virt import domain_descriptor
from vdsm.virt import vmchannels
from vdsm.virt import vmxml

from testValidation import brokentest
from testlib import VdsmTestCase as TestCaseBase
from testlib import XMLTestCase, permutations, expandPermutations

from vmTestsData import CONF_TO_DOMXML_X86_64
from vmTestsData import CONF_TO_DOMXML_PPC64
from vmTestsData import CONF_TO_DOMXML_NO_VDSM

from . import vmfakelib as fake
import pytest


class VmXmlTestCase(TestCaseBase):

    _CONFS = {
        cpuarch.X86_64: CONF_TO_DOMXML_X86_64,
        cpuarch.PPC64: CONF_TO_DOMXML_PPC64,
        'novdsm': CONF_TO_DOMXML_NO_VDSM}

    def _build_domain_xml(self, arch):
        for conf, rawXml in self._CONFS[arch]:
            domXml = rawXml % conf
            yield fake.Domain(domXml, vmId=conf['vmId']), domXml


@expandPermutations
class TestVmXmlFunctions(VmXmlTestCase):

    @permutations([[cpuarch.X86_64], [cpuarch.PPC64]])
    def test_has_channel(self, arch):
        for _, dom_xml in self._build_domain_xml(arch):
            assert vmxml.has_channel(dom_xml, vmchannels.LEGACY_DEVICE_NAME) \
                is True


@expandPermutations
class TestVmXmlHelpers(XMLTestCase):

    _XML = u'''<?xml version="1.0" encoding="utf-8"?>
    <topelement>
      <hello lang="english">hello</hello>
      <hello cyrillic="yes" lang="русский">здра́вствуйте</hello>
      <bye>good bye<hello lang="čeština">dobrý den</hello></bye>
      <container><subelement/></container>
      <container><subelement>some content</subelement></container>
      <empty/>
    </topelement>
    '''

    def setUp(self):
        self._dom = xmlutils.fromstring(self._XML)

    def test_import_export(self):
        xml = xmlutils.tostring(self._dom)
        self.assertXMLEqual(xml, self._XML)

    def test_pretty_format_formatting(self):
        xml = re.sub(' *\n *', '', self._XML)
        dom = xmlutils.fromstring(xml)
        pretty = xmlutils.tostring(dom, pretty=True)
        assert pretty == u'''<?xml version='1.0' encoding='utf-8'?>
<topelement>
    <hello lang="english">hello</hello>
    <hello cyrillic="yes" lang="русский">здра́вствуйте</hello>
    <bye>good bye<hello lang="čeština">dobrý den</hello>
    </bye>
    <container>
        <subelement />
    </container>
    <container>
        <subelement>some content</subelement>
    </container>
    <empty />
</topelement>
'''

    def test_pretty_format_safety(self):
        # Check that dom is not modified in tostring; we check that by
        # comparing the exported forms of `dom' created before and after
        # tostring call.
        xml = re.sub(' *\n *', '', self._XML)
        dom = xmlutils.fromstring(xml)
        exported_1 = etree.tostring(dom)
        xmlutils.tostring(dom, pretty=True)
        exported_2 = etree.tostring(dom)
        assert exported_1 == exported_2

    @pytest.mark.slow
    def test_pretty_format_timing(self):
        test_path = os.path.realpath(__file__)
        dir_name = os.path.split(test_path)[0]
        xml_path = os.path.join(dir_name, '..', 'devices', 'data',
                                'testComplexVm.xml')
        xml = re.sub(' *\n *', '', open(xml_path).read())
        setup = """
import re
from vdsm.common import xmlutils
from vdsm.virt import vmxml
xml = re.sub(' *\\n *', '', '''%s''')
dom = xmlutils.fromstring(xml)
def run():
    xmlutils.tostring(dom, pretty=%%s)""" % (xml,)
        repetitions = 100
        elapsed = timeit.timeit('run()', setup=(setup % ('False',)),
                                number=repetitions)
        elapsed_pretty = timeit.timeit('run()', setup=(setup % ('True',)),
                                       number=repetitions)
        print('slowdown: %d%% (%.3f s per one domain)' %
              (100 * (elapsed_pretty - elapsed) / elapsed,
               (elapsed_pretty - elapsed) / repetitions,))

    @permutations([[None, 'topelement', 1],
                   ['topelement', 'topelement', 1],
                   [None, 'hello', 3],
                   ['topelement', 'bye', 1],
                   [None, 'none', 0]])
    def test_find_all(self, start_tag, tag, number):
        dom = self._dom
        if start_tag is not None:
            dom = vmxml.find_first(self._dom, 'topelement')
        elements = vmxml.find_all(dom, tag)
        matches = [vmxml.tag(e) == tag for e in elements]
        assert all(matches)
        assert len(matches) == number

    def test_find_first_not_found(self):
        with pytest.raises(vmxml.NotFound):
            vmxml.find_first(self._dom, 'none')

    @permutations([['hello', 'lang', 'english'],
                   ['hello', 'none', ''],
                   ['none', 'lang', ''],
                   ])
    def test_find_attr(self, tag, attribute, result):
        value = vmxml.find_attr(self._dom, tag, attribute)
        assert value == result

    @permutations([['hello', 'hello'],
                   ['empty', '']])
    def test_text(self, tag, result):
        element = vmxml.find_first(self._dom, tag)
        text = vmxml.text(element)
        assert text == result

    @permutations([['topelement', 'hello', 2],
                   ['bye', 'hello', 1],
                   ['empty', 'hello', 0],
                   ['topelement', 'none', 0],
                   ['topelement', None, 6],
                   ])
    def test_children(self, start_tag, tag, number):
        element = vmxml.find_first(self._dom, start_tag)
        assert len(list(vmxml.children(element, tag))) == number

    def test_append_child(self):
        empty = vmxml.find_first(self._dom, 'empty')
        vmxml.append_child(empty, vmxml.Element('new'))
        assert vmxml.find_first(self._dom, 'new', None) is not None
        empty = vmxml.find_first(self._dom, 'empty')
        assert vmxml.find_first(empty, 'new', None) is not None

    def test_append_child_etree(self):
        empty = vmxml.find_first(self._dom, 'empty')
        vmxml.append_child(empty, etree_child=xmlutils.fromstring('<new/>'))
        assert vmxml.find_first(self._dom, 'new', None) is not None
        empty = vmxml.find_first(self._dom, 'empty')
        assert vmxml.find_first(empty, 'new', None) is not None

    def test_append_child_noargs(self):
        empty = vmxml.find_first(self._dom, 'empty')
        with pytest.raises(RuntimeError):
            vmxml.append_child(empty)

    def test_append_child_too_many_args(self):
        empty = vmxml.find_first(self._dom, 'empty')
        with pytest.raises(RuntimeError):
            vmxml.append_child(empty, vmxml.Element('new'),
                               xmlutils.fromstring('<new/>'))

    def test_remove_child(self):
        top = vmxml.find_first(self._dom, 'topelement')
        hello = list(vmxml.find_all(top, 'hello'))
        old = hello[1]
        vmxml.remove_child(top, old)
        updated_hello = list(vmxml.find_all(top, 'hello'))
        hello = hello[:1] + hello[2:]
        assert updated_hello == hello

    def test_replace_child(self):
        expected = u'''<topelement>
    <hello lang="english">hello</hello>
    <hello cyrillic="yes" lang="русский">здра́вствуйте</hello>
    <bye>good bye<hello lang="čeština">dobrý den</hello>
    </bye>
    <container>
        <foo>
            <bar>baz</bar>
        </foo>
    </container>
    <container>
        <subelement>some content</subelement>
    </container>
    <empty />
</topelement>
'''
        new_element = '<foo><bar>baz</bar></foo>'
        new_child = xmlutils.fromstring(new_element)
        container = vmxml.find_first(self._dom, 'container')
        vmxml.replace_first_child(container, new_child)
        self.assertXMLEqual(xmlutils.tostring(self._dom, pretty=True),
                            expected)

    def test_namespaces(self):
        expected_xml = '''
        <domain xmlns:ovirt-tune="http://ovirt.org/vm/tune/1.0">
          <metadata>
            <ovirt-tune:qos/>
          </metadata>
        </domain>
        '''
        domain = vmxml.Element('domain')
        metadata = vmxml.Element('metadata')
        domain.appendChild(metadata)
        qos = vmxml.Element('qos', namespace='ovirt-tune',
                            namespace_uri='http://ovirt.org/vm/tune/1.0')
        metadata.appendChild(qos)
        self.assertXMLEqual(xmlutils.tostring(domain), expected_xml)

    @brokentest('find_first returns the innermost nested element')
    def test_find_first_nested(self):
        XML = u'''<?xml version="1.0" ?>
        <topelement>
          <subelement id="1">
              <subelement id="2"/>
          </subelement>
        </topelement>
        '''
        dom = xmlutils.fromstring(XML)
        sub1 = vmxml.find_first(dom, 'subelement')  # outermost
        sub2 = vmxml.find_first(sub1, 'subelement')  # innermost
        last = vmxml.find_first(sub2, 'subelement')
        assert sub2 is not last


@expandPermutations
class TestDomainDescriptor(VmXmlTestCase):

    @permutations([[domain_descriptor.DomainDescriptor, cpuarch.X86_64],
                   [domain_descriptor.DomainDescriptor, cpuarch.PPC64],
                   [domain_descriptor.MutableDomainDescriptor, cpuarch.X86_64]]
                  )
    def test_all_channels_vdsm_domain(self, descriptor, arch):
        for _, dom_xml in self._build_domain_xml(arch):
            dom = descriptor(dom_xml)
            channels = list(dom.all_channels())
            assert len(channels) >= len(vmchannels.AGENT_DEVICE_NAMES)
            for name, path in channels:
                assert name in vmchannels.AGENT_DEVICE_NAMES

    @permutations([[domain_descriptor.DomainDescriptor],
                   [domain_descriptor.MutableDomainDescriptor]])
    def test_all_channels_extra_domain(self, descriptor):
        for conf, raw_xml in CONF_TO_DOMXML_NO_VDSM:
            dom = descriptor(raw_xml % conf)
            assert sorted(dom.all_channels()) != \
                sorted(vmchannels.AGENT_DEVICE_NAMES)

    def test_no_channels(self):
        dom = domain_descriptor.MutableDomainDescriptor('<domain/>')
        assert list(dom.all_channels()) == []
