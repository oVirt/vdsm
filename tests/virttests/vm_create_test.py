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
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA
# 02110-1301  USA
#
# Refer to the README and COPYING files for full details of the license
#
from __future__ import absolute_import

import logging

from vdsm.virt import domain_descriptor
from vdsm.virt import metadata
from vdsm.virt import vmdevices

from testlib import VdsmTestCase


class DevicesFromXMLTests(VdsmTestCase):

    _log = logging.getLogger('test.vm_create')

    def setUp(self):
        self.vmid = 'testvmid'

    def test_no_devices(self):
        dev_map = self.prepare_map(u'''<?xml version="1.0" encoding="utf-8"?>
<domain type="kvm" xmlns:ovirt="http://ovirt.org/vm/tune/1.0">
  <name>vmname</name>
  <uuid>{self.vmid}</uuid>
  <metadata>
    <ovirt:qos/>
  </metadata>
  <devices/>
</domain>''')
        self._assert_empty_dev_map(dev_map)

    def test_unknown_devices(self):
        dev_map = self.prepare_map(u'''<?xml version="1.0" encoding="utf-8"?>
<domain type="kvm" xmlns:ovirt="http://ovirt.org/vm/tune/1.0">
  <name>vmname</name>
  <uuid>{self.vmid}</uuid>
  <metadata>
    <ovirt:qos/>
  </metadata>
  <devices>
    <foo/>
    <bar/>
  </devices>
</domain>''')
        self._assert_empty_dev_map(dev_map)

    def test_skip_unknown_devices(self):
        dev_map = self.prepare_map(u'''<?xml version="1.0" encoding="utf-8"?>
<domain type="kvm" xmlns:ovirt="http://ovirt.org/vm/tune/1.0">
  <name>vmname</name>
  <uuid>{self.vmid}</uuid>
  <metadata>
    <ovirt:qos/>
  </metadata>
  <devices>
    <foo/>
    <bar/>
    <video>
      <model heads="1" ram="65536" type="qxl" vgamem="16384" vram="32768"/>
    </video>
  </devices>
</domain>''')
        for dev_type, dev_objs in dev_map.items():
            if dev_type == vmdevices.hwclass.VIDEO:
                self.assertEqual(len(dev_objs), 1)
            else:
                self.assertEqual(dev_objs, [])

    def test_skip_uninteresting_devices(self):
        """
        Some devices are needed for the VM, but Vdsm doesn't handle them,
        so we skip them as they are unknown
        """
        dev_map = self.prepare_map(u'''<?xml version="1.0" encoding="utf-8"?>
<domain type="kvm" xmlns:ovirt="http://ovirt.org/vm/tune/1.0">
  <name>vmname</name>
  <uuid>{self.vmid}</uuid>
  <metadata>
    <ovirt:qos/>
  </metadata>
  <devices>
    <input bus="ps2" type="mouse"/>
    <channel type="spicevmc">
      <target name="com.redhat.spice.0" type="virtio"/>
    </channel>
    <memballoon model="none"/>
    <video>
      <model heads="1" ram="65536" type="qxl" vgamem="16384" vram="32768"/>
    </video>
  </devices>
</domain>''')
        for dev_type, dev_objs in dev_map.items():
            if dev_type in (
                vmdevices.hwclass.VIDEO,
                vmdevices.hwclass.BALLOON
            ):
                self.assertEqual(len(dev_objs), 1)
            else:
                self.assertEqual(dev_objs, [])

    def prepare_map(self, dom_xml):
        xml_str = dom_xml.format(self=self)
        dom_desc = domain_descriptor.DomainDescriptor(xml_str)
        md_desc = metadata.Descriptor.from_xml(xml_str)
        return vmdevices.common.dev_map_from_domain_xml(
            self.vmid, dom_desc, md_desc, self._log
        )

    def _assert_empty_dev_map(self, dev_map):
        for dev_type, dev_objs in dev_map.items():
            self.assertEqual(dev_objs, [])
