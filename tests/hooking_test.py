# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import
from __future__ import division

import io
import os
from xml.dom import minidom
import xml.etree.ElementTree as ET

from vdsm.common import xmlutils
from vdsm.hook import hooking

from testlib import temporaryPath


class TestHooking(object):

    _EXPECTED_XML = u"""<?xml version="1.0"?>
<domain type="kvm" xmlns:ovirt="http://ovirt.org/vm/tune/1.0">
  <devices />
  <maxMemory>16384</maxMemory>
  <metadata />
  <name>testVm</name>
  <uuid>0eceeda9-a648-441a-b113-70423d5273c6</uuid>
</domain>"""

    def test_read_domxml(self, monkeypatch):
        with temporaryPath(data=self._EXPECTED_XML.encode('utf-8')) as path:
            monkeypatch.setenv('_hook_domxml', path)
            domxml = hooking.read_domxml()
        assert xml_equal(domxml.toxml(), self._EXPECTED_XML)

    def test_write_domxml(self, monkeypatch):
        with temporaryPath() as path:
            monkeypatch.setenv('_hook_domxml', path)
            hooking.write_domxml(minidom.parseString(self._EXPECTED_XML))

            with io.open(path, 'r') as src:
                found_xml = src.read()
        assert xml_equal(found_xml, self._EXPECTED_XML)

    def test_roundtrip_domxml(self, monkeypatch):
        with temporaryPath() as path:
            os.environ['_hook_domxml'] = path
            monkeypatch.setenv('_hook_domxml', path)

            hooking.write_domxml(minidom.parseString(self._EXPECTED_XML))
            domxml = hooking.read_domxml()

        assert xml_equal(domxml.toprettyxml(), self._EXPECTED_XML)


# TODO: replace this and assertXMLEqual with better approach
def xml_equal(actual_xml, expected_xml):
    actual = ET.fromstring(actual_xml)
    xmlutils.indent(actual)
    actual_xml = ET.tostring(actual)

    expected = ET.fromstring(expected_xml)
    xmlutils.indent(expected)
    expected_xml = ET.tostring(expected)
    return actual_xml == expected_xml
