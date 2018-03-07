#
# Copyright 2018 Red Hat, Inc.
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

from vdsm.common import cpuarch
from vdsm.common import hooks
from vdsm.virt import domxml_preprocess
from vdsm.virt import vmdevices
from vdsm.virt import vmxml

from testlib import VdsmTestCase
from testlib import XMLTestCase
from testlib import read_data

import vmfakelib as fake

from monkeypatch import MonkeyPatchScope


class TestReplaceDiskXML(XMLTestCase):

    def test_replace_disk(self):
        """
        Replace one disk, moving from file to block; no changes to CDROM
        """
        dom_disk_file_str = read_data('domain_disk_file.xml')
        dom = vmxml.parse_xml(dom_disk_file_str)
        # taken from domain_disk_file.xml
        disk_meta = {
            'domainID': 'f3f6e278-47a1-4048-b9a5-0cf4d6ba455f',
            'imageID': 'c9865ca8-f6d2-4363-bec2-af870a87a819',
            'poolID': '59c4c3ee-0205-0227-0229-000000000155',
            'volumeID': '1dfad482-c65c-436c-aa59-87e020b63302',
        }
        disk_path = (
            '/rhev/data-center/mnt/blockSD/'
            'a67a8671-05fc-4e42-b147-f7d3a0496cd6/images/'
            'b07a37de-95d6-4682-84cd-c8c9201327e3/'
            'b3335e60-6d27-46e0-bed8-50e75cca6786'
        )
        disk_xml = u'''<disk type='block' device='disk' snapshot='no'>
            <driver name='qemu' type='qcow2' cache='none'
                error_policy='stop' io='native'/>
            <source dev='{path}'/>
          <backingStore/>
          <target dev='sda' bus='scsi'/>
          <serial>b07a37de-95d6-4682-84cd-c8c9201327e3</serial>
          <boot order='1'/>
          <alias name='scsi0-0-0-0'/>
          <address type='drive' controller='0' bus='0' target='0' unit='0'/>
        </disk>'''.format(path=disk_path)
        disk_params = vmdevices.storagexml.parse(
            vmxml.parse_xml(disk_xml), disk_meta
        )
        cdrom_xml = u'''<disk device="cdrom" type="file">
            <driver error_policy="report" name="qemu" type="raw" />
            <source startupPolicy="optional" />
            <backingStore />
            <target bus="ide" dev="hdc" />
            <readonly />
            <alias name="ide0-1-0" />
            <address bus="1" controller="0" target="0"
                type="drive" unit="0" />
        </disk>'''
        cdrom_params = vmdevices.storagexml.parse(
            vmxml.parse_xml(cdrom_xml), {}
        )
        disk_devs = [
            vmdevices.storage.Drive(self.log, **cdrom_params),
            vmdevices.storage.Drive(self.log, **disk_params)
        ]
        domxml_preprocess.replace_disks_xml(dom, disk_devs)
        self.assertXMLEqual(
            vmxml.format_xml(dom, pretty=True),
            read_data('domain_disk_block.xml')
        )


class TestReplaceDeviceXMLWithHooksXML(VdsmTestCase):

    def setUp(self):
        self._hook_params = []

    def _hook(self, dev_xml, vm_custom, dev_custom):
        self._hook_params.append((dev_xml, vm_custom, dev_custom))
        return dev_xml

    def test_replace_device_xml_with_hook_xml_no_custom(self):
        """
        Don't replace devices if they lack custom properties
        """
        dom = vmxml.parse_xml(read_data('domain_disk_file.xml'))

        with MonkeyPatchScope([
            (hooks, 'before_device_create', self._hook),
        ]):
            domxml_preprocess.replace_device_xml_with_hooks_xml(
                dom, 'test', {})

        self.assertEqual(self._hook_params, [])

    def test_replace_device_xml_with_hook_xml_empty_custom(self):
        """
        Try to replace device XML if custom properties are declared.
        """
        # invoked even if custom properties exist, but are empty.
        dom_disk_file_str = read_data('vm_replace_md_base.xml')
        dom = vmxml.parse_xml(dom_disk_file_str)

        with MonkeyPatchScope([
            (hooks, 'before_device_create', self._hook),
        ]):
            domxml_preprocess.replace_device_xml_with_hooks_xml(
                dom, 'test', {})

        addr = (
            '<address bus="0x00" domain="0x0000"'
            ' function="0x0" slot="0x03" type="pci" />'
        )
        expected_xml = u'''<?xml version='1.0' encoding='utf-8'?>
<interface type="bridge">
    <model type="virtio" />
    <link state="up" />
    <source bridge="ovirtmgmt" />
    {addr}
    <mac address="00:1a:4a:16:01:12" />
    <filterref filter="vdsm-no-mac-spoofing" />
    <bandwidth />
</interface>
'''.format(addr=addr)
        self.assertEqual(
            self._hook_params,
            [(expected_xml, {}, {})]
        )


class TestFixLease(VdsmTestCase):

    def setUp(self):
        self.cif = fake.ClientIF()
        self.xml_str = read_data('hostedengine_lease.xml')
        self.disk_devs = domxml_preprocess._make_disk_devices(
            self.xml_str, self.log)

        self.driveVolInfo = {
            'leasePath': 'drive-lease-path',
            'leaseOffset': 'drive-lease-offset',
        }
        self.vmVolInfo = {
            'leasePath': 'vm-lease-path',
            'leaseOffset': 'vm-lease-offset',
        }

    def test_passthrough(self):
        """
        test that without placeholders, the output is the same as the input.
        """
        # any XML without placeholders (need to check manually) is fine
        xml_str_in = read_data('vm_compat41.xml')
        xml_str_out = domxml_preprocess.replace_placeholders(
            xml_str_in, self.cif, cpuarch.X86_64, '0000')
        self.assertEqual(xml_str_out, xml_str_in)

    def test_drive_lease(self):
        """
        test that we fill the drive lease. Happy path.
        """
        disk_devs = self._inject_volume_chain(
            self.disk_devs, self.driveVolInfo)

        xml_str = domxml_preprocess.replace_placeholders(
            self.xml_str, self.cif, cpuarch.X86_64, '0000',
            {vmdevices.hwclass.DISK: disk_devs})

        self._check_leases(xml_str, [self.driveVolInfo])

    def test_drive_lease_without_volume_chain(self):
        """
        Should we lack volumeChain attribute (like cdroms), this
        should not raise.
        We treat leases like VM lease, because we cannot distinguish
        this case.
        """

        def _fake_lease_info(*args, **kwargs):
            return {
                'result': {
                    'path': self.vmVolInfo['leasePath'],
                    'offset': self.vmVolInfo['leaseOffset'],
                }
            }

        with MonkeyPatchScope([
            (self.cif.irs, 'lease_info', _fake_lease_info),
        ]):
            xml_str = domxml_preprocess.replace_placeholders(
                self.xml_str, self.cif, cpuarch.X86_64, '0000',
                {vmdevices.hwclass.DISK: self.disk_devs})

        self._check_leases(xml_str, [self.vmVolInfo])

    def test_drive_lease_chain_not_matches(self):
        """
        We have no choice but consider this a VM lease.
        """

        disk_devs = self._inject_volume_chain(
            self.disk_devs, self.driveVolInfo,
            domainID='unknwonDomainID',
            volumeID='unknownVolumeID')

        def _fake_lease_info(*args, **kwargs):
            return {
                'result': {
                    'path': self.vmVolInfo['leasePath'],
                    'offset': self.vmVolInfo['leaseOffset'],
                }
            }

        with MonkeyPatchScope([
            (self.cif.irs, 'lease_info', _fake_lease_info),
        ]):
            xml_str = domxml_preprocess.replace_placeholders(
                self.xml_str, self.cif, cpuarch.X86_64, '0000',
                {vmdevices.hwclass.DISK: disk_devs})

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

    def _inject_volume_chain(self, disk_devs, volInfo,
                             domainID=None, volumeID=None):
        drives = []
        for drive in disk_devs:
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

        return drives
