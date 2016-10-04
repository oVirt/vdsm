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


@attr(type='unit')
class LibvirtTests(TestCaseBase):

    def assertEqualXml(self, a, b, msg=None):
        """
        Compare two xml strings for equality.
        """

        aXml = ET.tostring(ET.fromstring(a))
        bXml = ET.tostring(ET.fromstring(b))

        aXmlNrml = re.sub(b'>\s*\n\s*<', b'><', aXml).strip()
        bXmlNrml = re.sub(b'>\s*\n\s*<', b'><', bXml).strip()

        self.assertEqual(aXmlNrml, bXmlNrml, msg)

    def testCreateNetXmlBridged(self):
        expectedDoc = """<network>
                           <name>vdsm-awesome_net</name>
                           <forward mode='bridge'/>
                           <bridge name='awesome_net'/>
                         </network>"""
        actualDoc = libvirt.createNetworkDef('awesome_net', bridged=True)

        self.assertEqualXml(expectedDoc, actualDoc)

    def testCreateNetXml(self):
        iface = "dummy"
        expectedDoc = ("""<network>
                            <name>vdsm-awesome_net</name>
                            <forward mode='passthrough'>
                            <interface dev='%s'/>
                            </forward>
                          </network>""" % iface)
        actualDoc = libvirt.createNetworkDef('awesome_net', bridged=False,
                                             iface=iface)

        self.assertEqualXml(expectedDoc, actualDoc)
