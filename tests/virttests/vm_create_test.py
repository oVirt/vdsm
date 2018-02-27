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
from vdsm.virt import vmxml

from testlib import VdsmTestCase
from testlib import read_data

import vmfakelib as fake


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

        self.assertIsNotNone(sda)
        self.assertEqual(sda['path'], '/rhev/data-center/path/updated')
        self.assertEqual(sda['imageID'], 'imageID_updated')
        self.assertEqual(sda['poolID'], 'poolID_updated')
        self.assertEqual(sda['domainID'], 'domainID_updated')
        self.assertEqual(sda['volumeID'], 'volumeID_updated')


class TestFixLease(VdsmTestCase):

    def setUp(self):
        self.vmParams = {
            'vmId': 'ac698e04-d30b-426a-9f3c-0e95641b2b79',  # from XML
            'xml': read_data('hostedengine_lease.xml'),
        }
        self.driveVolInfo = {
            'leasePath': 'drive-lease-path',
            'leaseOffset': 'drive-lease-offset',
        }
        self.vmVolInfo = {
            'leasePath': 'vm-lease-path',
            'leaseOffset': 'vm-lease-offset',
        }

    def test_drive_lease(self):
        """
        test that we fill the drive lease. Happy path.
        """
        with fake.VM(self.vmParams) as testvm:
            testvm._devices = testvm._make_devices()

            self._inject_volume_chain(testvm, self.driveVolInfo)

            xml_str = vmdevices.lease.fixLeases(
                testvm.cif.irs,
                testvm.conf['xml'],
                testvm._devices.get(vmdevices.hwclass.DISK, []))

        self._check_leases(xml_str, [self.driveVolInfo])

    def test_drive_lease_without_volume_chain(self):
        """
        Should we lack volumeChain attribute (like cdroms), this
        should not raise.
        We treat leases like VM lease, because we cannot distinguish
        this case.
        """
        with fake.VM(self.vmParams) as testvm:
            testvm._devices = testvm._make_devices()

            def _fake_lease_info(*args, **kwargs):
                return {
                    'result': {
                        'path': self.vmVolInfo['leasePath'],
                        'offset': self.vmVolInfo['leaseOffset'],
                    }
                }

            testvm.cif.irs.lease_info = _fake_lease_info

            xml_str = vmdevices.lease.fixLeases(
                testvm.cif.irs,
                testvm.conf['xml'],
                testvm._devices.get(vmdevices.hwclass.DISK, []))

        self._check_leases(xml_str, [self.vmVolInfo])

    def test_drive_lease_chain_not_matches(self):
        """
        We have no choice but consider this a VM lease.
        """
        with fake.VM(self.vmParams) as testvm:
            testvm._devices = testvm._make_devices()

            self._inject_volume_chain(
                testvm, self.driveVolInfo,
                domainID='unknwonDomainID',
                volumeID='unknownVolumeID')

            def _fake_lease_info(*args, **kwargs):
                return {
                    'result': {
                        'path': self.vmVolInfo['leasePath'],
                        'offset': self.vmVolInfo['leaseOffset'],
                    }
                }

            testvm.cif.irs.lease_info = _fake_lease_info

            xml_str = vmdevices.lease.fixLeases(
                testvm.cif.irs,
                testvm.conf['xml'],
                testvm._devices.get(vmdevices.hwclass.DISK, []))

        self._check_leases(xml_str, [self.vmVolInfo])

    def _check_leases(self, xml_str, vol_infos):
        xml_dom = vmxml.parse_xml(xml_str)
        lease_elems = xml_dom.findall('./devices/lease')
        self.assertEqual(len(lease_elems), len(vol_infos))

        for lease_elem, vol_info in zip(lease_elems, vol_infos):
            target = vmxml.find_first(lease_elem, 'target')
            self.assertEqual(
                target.attrib['path'], vol_info['leasePath'])
            self.assertEqual(
                target.attrib['offset'], vol_info['leaseOffset'])

    def _inject_volume_chain(self, testvm, volInfo,
                             domainID=None, volumeID=None):
        drives = []
        for drive in testvm._devices.get(vmdevices.hwclass.DISK, []):
            if drive.device != 'disk':
                continue

            # the leases code uses only the leaf node. So let's use
            # obviously bogus nodes which we should ignore
            bogus = {
                'domainID': 'bogus because should be ignored',
                'volumeID': 'bogus because should be ignored',
                'leasePath': 'bogus because should be ignored',
                'leaseOffset': 'bogus because should be ignored',
            }
            # the leaf node is the only one used, so we fill it with
            # meaningful data
            leaf = {
                'domainID': domainID or drive.domainID,
                'volumeID': volumeID or drive.volumeID,
            }
            leaf.update(volInfo)
            drive.volumeChain = [bogus, bogus, leaf]
            drives.append(drive)
            break  # assume only one disk

        testvm._devices[vmdevices.hwclass.DISK] = drives


# TODO: almost dupe of Vm._findDriveConfigByName
def find_drive_conf_by_name(disk_params, name):
    for disk_param in disk_params:
        if disk_param['name'] == name:
            return disk_param
    return None
