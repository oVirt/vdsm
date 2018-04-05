#
# Copyright 2015 Red Hat, Inc.
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
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA
#
# Refer to the README and COPYING files for full details of the license
#
from __future__ import absolute_import
from __future__ import division

import libvirt
from testlib import VdsmTestCase
import vmfakelib


class SecretsTests(VdsmTestCase):

    def test_define_new(self):
        con = vmfakelib.Connection()
        xml = """
        <secret>
            <uuid>uuid</uuid>
            <usage type="ceph">
                <name>name</name>
            </usage>
        </secret>
        """
        con.secretDefineXML(xml)
        sec = con.secrets['uuid']
        self.assertEqual(sec.uuid, "uuid")
        self.assertEqual(sec.usage_type, "ceph")
        self.assertEqual(sec.usage_id, "name")
        self.assertEqual(sec.description, None)

    def test_define_new_with_description(self):
        con = vmfakelib.Connection()
        xml = """
        <secret>
            <description>description</description>
            <uuid>uuid</uuid>
            <usage type="ceph">
                <name>name</name>
            </usage>
        </secret>
        """
        con.secretDefineXML(xml)
        sec = con.secrets['uuid']
        self.assertEqual(sec.description, "description")

    def test_define_replace(self):
        con = vmfakelib.Connection()
        xml1 = """
        <secret>
            <description>old description</description>
            <uuid>uuid</uuid>
            <usage type="ceph">
                <name>name</name>
            </usage>
        </secret>
        """
        xml2 = """
        <secret>
            <description>new description</description>
            <uuid>uuid</uuid>
            <usage type="ceph">
                <name>name</name>
            </usage>
        </secret>
        """
        con.secretDefineXML(xml1)
        con.secretDefineXML(xml2)
        sec = con.secrets['uuid']
        self.assertEqual(sec.description, "new description")

    def test_define_cannot_change_usage_id(self):
        con = vmfakelib.Connection()
        xml1 = """
        <secret>
            <uuid>uuid</uuid>
            <usage type="ceph">
                <name>name 1</name>
            </usage>
        </secret>
        """
        xml2 = """
        <secret>
            <uuid>uuid</uuid>
            <usage type="ceph">
                <name>name 2</name>
            </usage>
        </secret>
        """
        con.secretDefineXML(xml1)
        try:
            con.secretDefineXML(xml2)
        except libvirt.libvirtError as e:
            self.assertEqual(e.get_error_code(),
                             libvirt.VIR_ERR_INTERNAL_ERROR)
        else:
            self.fail("libvirtError was not raised")

    def test_define_usage_not_unique(self):
        con = vmfakelib.Connection()
        xml1 = """
        <secret>
            <uuid>uuid 1</uuid>
            <usage type="ceph">
                <name>name</name>
            </usage>
        </secret>
        """
        xml2 = """
        <secret>
            <uuid>uuid 2</uuid>
            <usage type="ceph">
                <name>name</name>
            </usage>
        </secret>
        """
        con.secretDefineXML(xml1)
        try:
            con.secretDefineXML(xml2)
        except libvirt.libvirtError as e:
            self.assertEqual(e.get_error_code(),
                             libvirt.VIR_ERR_INTERNAL_ERROR)
        else:
            self.fail("libvirtError was not raised")

    def test_lookup(self):
        con = vmfakelib.Connection()
        xml = """
        <secret>
            <uuid>uuid</uuid>
            <usage type="ceph">
                <name>name</name>
            </usage>
        </secret>
        """
        con.secretDefineXML(xml)
        sec = con.secretLookupByUUIDString('uuid')
        self.assertEqual(sec.usage_id, "name")

    def test_lookup_error(self):
        con = vmfakelib.Connection()
        try:
            con.secretLookupByUUIDString('no-such-uuid')
        except libvirt.libvirtError as e:
            self.assertEqual(e.get_error_code(), libvirt.VIR_ERR_NO_SECRET)
        else:
            self.fail("libvirtError was not raised")
