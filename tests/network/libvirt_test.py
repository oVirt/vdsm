# Copyright 2016 Red Hat, Inc.
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
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA
# 02110-1301  USA
#
# Refer to the README and COPYING files for full details of the license
#
from __future__ import absolute_import

import re
import xml.etree.ElementTree as ET

from vdsm.network import libvirt

from nose.plugins.attrib import attr
from testlib import VdsmTestCase as TestCaseBase


NETWORK = 'awesome_net'
LIBVIRT_NETWORK = 'vdsm-' + NETWORK
IFACE = 'dummy'


class LibvirtTestCase(TestCaseBase):
    def assertEqualXml(self, a, b):
        """Compare two xml strings for equality"""

        a_xml = ET.tostring(ET.fromstring(a))
        b_xml = ET.tostring(ET.fromstring(b))

        a_xml_normalized = re.sub(b'>\s*\n\s*<', b'><', a_xml).strip()
        b_xml_normalized = re.sub(b'>\s*\n\s*<', b'><', b_xml).strip()

        self.assertEqual(a_xml_normalized, b_xml_normalized)


@attr(type='unit')
class LibvirtTests(LibvirtTestCase):

    def test_create_net_xml_with_bridge(self):
        expected_doc = """<network>
                            <name>{}</name>
                            <forward mode='bridge'/>
                            <bridge name='{}'/>
                         </network>""".format(LIBVIRT_NETWORK, NETWORK)
        actual_doc = libvirt.createNetworkDef(NETWORK, bridged=True)
        self.assertEqualXml(expected_doc, actual_doc)

    def test_create_net_xml_with_iface(self):
        expected_doc = """<network>
                            <name>{}</name>
                            <forward mode='passthrough'>
                              <interface dev='{}'/>
                            </forward>
                          </network>""".format(LIBVIRT_NETWORK, IFACE)
        actual_doc = libvirt.createNetworkDef(
            NETWORK, bridged=False, iface=IFACE)
        self.assertEqualXml(expected_doc, actual_doc)
