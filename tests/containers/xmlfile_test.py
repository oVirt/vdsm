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

import contextlib
import os
import uuid
import xml.etree.ElementTree as ET

from vdsm.virt.containers import xmlfile

from . import conttestlib


class XMLFileTests(conttestlib.TestCase):

    def setUp(self):
        self.vm_uuid = str(uuid.uuid4())

    @contextlib.contextmanager
    def test_env(self):
        with conttestlib.tmp_run_dir():
            yield xmlfile.XMLFile(self.vm_uuid)

    def test_path(self):
        with self.test_env() as xf:
            self.assertTrue(xf.path.endswith('xml'))
            self.assertIn(self.vm_uuid, xf.path)

    def test_save(self):
        root = ET.fromstring(conttestlib.minimal_dom_xml())
        with self.test_env() as xf:
            self.assertEqual(os.listdir(xmlfile.STATE_DIR), [])
            self.assertNotRaises(xf.save, root)
            self.assertTrue(len(os.listdir(xmlfile.STATE_DIR)), 1)

    def test_load(self):
        xml_data = conttestlib.minimal_dom_xml()
        root = ET.fromstring(xml_data)
        with self.test_env() as xf:
            xf.save(root)
            new_root = xf.load()
            xml_copy = xmlfile.XMLFile.encode(new_root)
            # FIXME: nasty trick to tidy up the XML
            xml_ref = xmlfile.XMLFile.encode(root)
            self.assertEqual(xml_ref, xml_copy)

    def test_clear(self):
        xml_data = conttestlib.minimal_dom_xml()
        root = ET.fromstring(xml_data)
        with self.test_env() as xf:
            xf.save(root)
            self.assertTrue(len(os.listdir(xmlfile.STATE_DIR)), 1)
            self.assertNotRaises(xf.clear)
            self.assertEqual(os.listdir(xmlfile.STATE_DIR), [])
