#
# Copyright 2015-2016 Red Hat, Inc.
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License as published
# by the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA
#
# Refer to the README and COPYING files for full details of the license
#
from __future__ import absolute_import
from __future__ import division

import xml.etree.ElementTree as ET

import libvirt

from vdsm.virt.containers.connection import Connection
from vdsm.virt.containers import doms

from . import conttestlib


class ConnectionAPITests(conttestlib.RunnableTestCase):

    def test_get_lib_version(self):
        conn = Connection()
        ver = conn.getLibVersion()
        self.assertGreater(ver, 0)

    def test_lookup_by_name_missing(self):
        conn = Connection()
        self.assertRaises(libvirt.libvirtError,
                          conn.lookupByName,
                          "foobar")

    def test_lookup_by_id_missing(self):
        conn = Connection()
        self.assertRaises(libvirt.libvirtError,
                          conn.lookupByID,
                          42)

    def test_lookup_by_uuid_string(self):
        with conttestlib.fake_runtime_domain() as dom:
            doms.add(dom)
            conn = Connection()
            guid = dom.UUIDString()
            dom2 = conn.lookupByUUIDString(guid)
        self.assertEqual(dom2.UUIDString(), guid)

    def test_list_all_domains_none(self):
        conn = Connection()
        self.assertEqual(conn.listAllDomains(0), [])

    def test_list_domains_id_none(self):
        conn = Connection()
        self.assertEqual(conn.listDomainsID(), [])


class FakeDomain(object):
    def __init__(self, vm_uuid):
        self._vm_uuid = vm_uuid

    def UUIDString(self):
        return self._vm_uuid


def save_xml(xf, xml_str):
    root = ET.fromstring(xml_str)
    xf.save(root)
