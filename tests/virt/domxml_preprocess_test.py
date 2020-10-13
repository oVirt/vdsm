#
# Copyright 2018, 2019 Red Hat, Inc.
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
from __future__ import division

from vdsm.common import cpuarch
from vdsm.common import hooks
from vdsm.common import xmlutils
from vdsm.virt import domxml_preprocess
from vdsm.virt import vmdevices
from vdsm.virt import vmxml
from vdsm import osinfo

from testlib import VdsmTestCase
from testlib import XMLTestCase
from testlib import read_data

from . import vmfakelib as fake

from monkeypatch import MonkeyPatchScope


class TestReplacePlaceholders(XMLTestCase):

    def test_replace_values(self):
        xml_str = read_data('sysinfo_snippet_template.xml')
        dom = xmlutils.fromstring(xml_str)
        with MonkeyPatchScope([
            (osinfo, 'version', self._version),
        ]):
            domxml_preprocess.replace_placeholders(
                dom, cpuarch.X86_64, serial='test-serial')
        self.assertXMLEqual(
            xmlutils.tostring(dom, pretty=True),
            read_data('sysinfo_snippet_filled.xml')
        )

    def test_skip_without_placeholders(self):
        # any domain without placeholders is fine, picked random one
        xml_str = read_data('vm_hosted_engine_42.xml')
        dom = xmlutils.fromstring(xml_str)
        with MonkeyPatchScope([
            (osinfo, 'version', self._version),
        ]):
            domxml_preprocess.replace_placeholders(
                dom, cpuarch.X86_64, serial='test-serial')
        self.assertXMLEqual(
            xmlutils.tostring(dom, pretty=True),
            xml_str
        )

    def _version(self):
        return {
            'version': '42',
            'release': '1',
            'name': 'test-product',
        }


class TestReplaceDiskXML(XMLTestCase):

    def test_update_disks_xml(self):
        """
        Replace one disk, moving from file to block; no changes to CDROM
        """
        dom, disk_devs = self._make_env()
        domxml_preprocess.update_disks_xml_from_objs(
            FakeVM(self.log), dom, disk_devs)
        self.assertXMLEqual(
            extract_device_snippet(
                'disk',
                dom=dom),
            read_data('disk_updated_snippet.xml')
        )

    def test_replace_disks_xml(self):
        dom, disk_devs = self._make_env()
        domxml_preprocess.replace_disks_xml(dom, disk_devs)
        self.assertXMLEqual(
            xmlutils.tostring(dom, pretty=True),
            read_data('domain_disk_block.xml')
        )

    def test_replace_cdrom_withoutource_file(self):
        dom_str = read_data('vm_hibernated.xml')
        dom = xmlutils.fromstring(dom_str)
        cdrom_xml = u'''<disk device="cdrom" type="file">
            <driver error_policy="report" name="qemu" type="raw" />
            <source {file_src}startupPolicy="optional">
                <seclabel model="dac" relabel="no" type="none" />
            </source>
            <backingStore />
            <target bus="ide" dev="hdc" />
            <readonly />
            <alias name="ide0-1-0" />
            <address bus="1" controller="0" target="0"
                type="drive" unit="0" />
        </disk>'''
        cdrom_params = vmdevices.storagexml.parse(
            xmlutils.fromstring(cdrom_xml.format(file_src='')), {}
        )
        disk_devs = [
            vmdevices.storage.Drive(self.log, **cdrom_params),
        ]
        domxml_preprocess.update_disks_xml_from_objs(
            FakeVM(self.log), dom, disk_devs)
        cdrom_elem = dom.find('./devices/disk[@device="cdrom"]')
        self.assertXMLEqual(
            xmlutils.tostring(cdrom_elem, pretty=True),
            cdrom_xml.format(file_src="file='' ")
        )

    def test_replace_cdrom_with_minimal_drive(self):
        dom_str = read_data('vm_hibernated_390.xml')
        dom = xmlutils.fromstring(dom_str)
        # taken from the test XML and amended manually
        # please note:
        # - the lack of empty 'backingStore' element
        # - the 'driver' elements lack name="qemu" (default)
        cdrom_xml = u'''<disk device="cdrom" type="file">
            <driver error_policy="report" type="raw" />
            <source file="" startupPolicy="optional">
                <seclabel model="dac" relabel="no" type="none" />
            </source>
            <target bus="ide" dev="hdc" />
            <readonly />
            <alias name="ua-096534a7-5fbd-4bd1-add0-65501bce51f9" />
            <address bus="1" controller="0" target="0"
                type="drive" unit="0" />
        </disk>'''
        cdrom_params = vmdevices.storagexml.parse(
            xmlutils.fromstring(cdrom_xml), {}
        )
        disk_devs = [
            vmdevices.storage.Drive(self.log, **cdrom_params),
        ]
        domxml_preprocess.update_disks_xml_from_objs(
            FakeVM(self.log), dom, disk_devs)
        cdrom_elem = dom.find('./devices/disk[@device="cdrom"]')
        self.assertXMLEqual(
            xmlutils.tostring(cdrom_elem, pretty=True),
            cdrom_xml
        )

    def test_with_sysprep_floppy(self):
        dom_str = read_data('vm_sysprep_floppy.xml')
        dom = xmlutils.fromstring(dom_str)
        # taken and amended from vm_sysprep_floppy.xml
        floppy_params = {
            'index': 0,
            'iface': 'fdc',
            'name': 'fda',
            'alias': 'ua-cc9acd76-7b44-4adf-881d-e98e1cf4e639',
            'vmid': 'e9252f48-6b22-4c9b-8d9a-8531fcf71f4c',
            'diskType': 'file',
            'readonly': True,
            'device': 'floppy',
            'path': 'PAYLOAD:',
            'propagateErrors': 'off',
            'type': 'disk'
        }
        floppy_obj = vmdevices.storage.Drive(self.log, **floppy_params)
        disk_devs = [floppy_obj]
        domxml_preprocess.update_disks_xml_from_objs(
            FakeVM(self.log), dom, disk_devs)

        floppy_elem = dom.find('./devices/disk[@device="floppy"]')
        self.assertXMLEqual(
            xmlutils.tostring(floppy_elem, pretty=True),
            xmlutils.tostring(floppy_obj.getXML(), pretty=True),
        )

    def _make_env(self):
        dom_disk_file_str = read_data('domain_disk_file.xml')
        dom = xmlutils.fromstring(dom_disk_file_str)
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
            xmlutils.fromstring(disk_xml), disk_meta
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
            xmlutils.fromstring(cdrom_xml), {}
        )
        disk_devs = [
            vmdevices.storage.Drive(self.log, **cdrom_params),
            vmdevices.storage.Drive(self.log, **disk_params)
        ]
        return dom, disk_devs


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
        dom = xmlutils.fromstring(read_data('domain_disk_file.xml'))

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
        dom = xmlutils.fromstring(dom_disk_file_str)

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


class TestReplaceLeaseXML(XMLTestCase):

    def setUp(self):
        self.vm = FakeVM(self.log)
        self.cif = fake.ClientIF()
        self.xml_str = read_data('hostedengine_lease.xml')
        self.dom = xmlutils.fromstring(self.xml_str)
        self.disk_devs = domxml_preprocess._make_disk_devices(
            self.xml_str, self.log)

        self.driveVolInfo = {
            'leasePath': '/fake/drive/lease/path',
            'leaseOffset': 42,
        }
        self.vmVolInfo = {
            # from XML
            'leasePath': 'LEASE-PATH:'
                         '9eaa286e-37d6-429e-a46b-63bec1dd4868:'
                         '4f0a775f-ed16-4832-ab9f-f0427f33ab92',
            'leaseOffset': 'LEASE-OFFSET:'
                           '9eaa286e-37d6-429e-a46b-63bec1dd4868:'
                           '4f0a775f-ed16-4832-ab9f-f0427f33ab92',
        }

    def test_no_leases(self):
        """
        without leases, do nothing
        """
        # any VM without leases is fine
        xml_str = read_data('vm_compat41.xml')
        self.assertXMLEqual(
            extract_device_snippet('lease', xml_str=xml_str),
            u'''<?xml version='1.0' encoding='utf-8'?><devices />'''
        )

        dom = xmlutils.fromstring(xml_str)
        disk_devs = domxml_preprocess._make_disk_devices(
            xml_str, self.log)
        disk_devs = self._inject_volume_chain(
            disk_devs, self.driveVolInfo,
            domainID='unknwonDomainID',
            volumeID='unknownVolumeID')

        domxml_preprocess.update_leases_xml_from_disk_objs(
            self.vm, dom, disk_devs)

        self.assertXMLEqual(
            extract_device_snippet('lease', dom=dom),
            u'''<?xml version='1.0' encoding='utf-8'?><devices />'''
        )

    def test_drive_lease(self):
        """
        we fill the drive lease. Happy path.
        """
        disk_devs = self._inject_volume_chain(
            self.disk_devs, self.driveVolInfo)

        domxml_preprocess.update_leases_xml_from_disk_objs(
            self.vm, self.dom, disk_devs)

        xml_str = xmlutils.tostring(self.dom)
        self._check_leases(xml_str, [self.driveVolInfo])

    def test_drive_lease_without_volume_chain(self):
        """
        Lacking volumeChain attribute (like cdroms), don't raise.
        We treat leases like VM lease, because we cannot distinguish
        this case.
        """

        domxml_preprocess.update_leases_xml_from_disk_objs(
            self.vm, self.dom, self.disk_devs)

        xml_str = xmlutils.tostring(self.dom)
        self._check_leases(xml_str, [self.vmVolInfo])

    def test_drive_lease_chain_not_matches(self):
        """
        We have no choice but consider this a VM lease.
        """

        disk_devs = self._inject_volume_chain(
            self.disk_devs, self.driveVolInfo,
            domainID='unknwonDomainID',
            volumeID='unknownVolumeID')

        domxml_preprocess.update_leases_xml_from_disk_objs(
            self.vm, self.dom, disk_devs)

        xml_str = xmlutils.tostring(self.dom)
        self._check_leases(xml_str, [self.vmVolInfo])

    def _check_leases(self, xml_str, vol_infos):
        xml_dom = xmlutils.fromstring(xml_str)
        lease_elems = xml_dom.findall('./devices/lease')
        self.assertEqual(len(lease_elems), len(vol_infos))

        for lease_elem, vol_info in zip(lease_elems, vol_infos):
            target = vmxml.find_first(lease_elem, 'target')
            self.assertEqual(
                target.attrib['path'], str(vol_info['leasePath']))
            self.assertEqual(
                target.attrib['offset'], str(vol_info['leaseOffset']))

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


def extract_device_snippet(device_type, xml_str=None, dom=None):
    if dom is None:
        dom = xmlutils.fromstring(xml_str)
    devs = vmxml.Element('devices')
    for dev in dom.findall('./devices/%s' % device_type):
        vmxml.append_child(devs, etree_child=dev)
    return xmlutils.tostring(devs, pretty=True)


class FakeVM(object):
    def __init__(self, log):
        self.log = log
