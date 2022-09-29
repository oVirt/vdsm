# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import
from __future__ import division

import logging

from vdsm.virt import domain_descriptor
from vdsm.virt import metadata
from vdsm.virt import vmdevices

from testlib import VdsmTestCase
from testlib import read_data

from . import vmfakelib as fake


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
            assert dev_objs == []

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
            assert dev_objs == []

    def prepare_map(self, dom_xml):
        xml_str = dom_xml.format(self=self)
        dom_desc = domain_descriptor.DomainDescriptor(xml_str)
        md_desc = metadata.Descriptor.from_xml(xml_str)
        return vmdevices.common.dev_map_from_domain_xml(
            self.vmid, dom_desc, md_desc, self._log
        )

    def _assert_empty_dev_map(self, dev_map):
        for dev_type, dev_objs in dev_map.items():
            assert dev_objs == []


class RestoreStateTests(VdsmTestCase):

    _log = logging.getLogger('test.vm_create')

    def test_correct_disk_and_metadata(self):
        vmParams = {
            'vmId': '627f1f31-752b-4e7c-bfb5-4313d191ed7b',  # from XML
            'restoreState': '/dev/null',  # unused here
            'restoreFromSnapshot': True,
            '_srcDomXML': read_data('vm_replace_md_base.xml'),
            'xml': read_data('vm_replace_md_update.xml'),
        }
        with fake.VM(vmParams) as testvm:
            updated_dom_xml = testvm.conf['xml']

        # shortcut
        make_params = vmdevices.common.storage_device_params_from_domain_xml

        dom_desc = domain_descriptor.DomainDescriptor(updated_dom_xml)
        with dom_desc.metadata_descriptor() as md_desc:

            disk_params = make_params(
                dom_desc.id, dom_desc, md_desc, self._log)

        sda = find_drive_conf_by_name(disk_params, 'sda')

        assert sda is not None
        assert sda['path'] == '/rhev/data-center/path/updated'
        assert sda['imageID'] == 'imageID_updated'
        assert sda['poolID'] == 'poolID_updated'
        assert sda['domainID'] == 'domainID_updated'
        assert sda['volumeID'] == 'volumeID_updated'


# TODO: almost dupe of Vm._findDriveConfigByName
def find_drive_conf_by_name(disk_params, name):
    for disk_param in disk_params:
        if disk_param['name'] == name:
            return disk_param
    return None
