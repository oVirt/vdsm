#
# Copyright 2014-2017 Red Hat, Inc.
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

from vdsm.virt import libvirtxml
from vdsm.virt import vmxml
from vdsm.virt.vmdevices import hostdevice, network, hwclass

from testlib import VdsmTestCase as TestCaseBase, XMLTestCase
from testlib import permutations, expandPermutations
from testlib import find_xml_element
from monkeypatch import MonkeyClass

from vdsm.common import cpuarch
from vdsm.common import hooks
from vdsm.common import hostdev
from vdsm.common import libvirtconnection

import hostdevlib


@expandPermutations
@MonkeyClass(libvirtconnection, 'get', hostdevlib.Connection)
@MonkeyClass(hostdev, '_sriov_totalvfs', hostdevlib.fake_totalvfs)
@MonkeyClass(hostdev, '_pci_header_type', lambda _: 0)
@MonkeyClass(hooks, 'after_hostdev_list_by_caps', lambda json: json)
class HostdevTests(TestCaseBase):

    def testProcessDeviceParams(self):
        deviceXML = hostdev._process_device_params(
            libvirtconnection.get().nodeDeviceLookupByName(
                hostdevlib.ADDITIONAL_DEVICE).XMLDesc()
        )

        self.assertEqual(
            hostdevlib.ADDITIONAL_DEVICE_PROCESSED,
            deviceXML
        )

    def testProcessDeviceParamsInvalidEncoding(self):
        deviceXML = hostdev._process_device_params(
            libvirtconnection.get().nodeDeviceLookupByName(
                hostdevlib.COMPUTER_DEVICE).XMLDesc()
        )

        self.assertEqual(
            hostdevlib.COMPUTER_DEVICE_PROCESSED,
            deviceXML
        )

    def testProcessSRIOV_PFDeviceParams(self):
        deviceXML = hostdev._process_device_params(
            libvirtconnection.get().nodeDeviceLookupByName(
                hostdevlib.SRIOV_PF).XMLDesc()
        )

        self.assertEqual(
            hostdevlib.SRIOV_PF_PROCESSED,
            deviceXML
        )

    def testProcessSRIOV_VFDeviceParams(self):
        deviceXML = hostdev._process_device_params(
            libvirtconnection.get().nodeDeviceLookupByName(
                hostdevlib.SRIOV_VF).XMLDesc()
        )

        self.assertEqual(hostdevlib.SRIOV_VF_PROCESSED, deviceXML)

    def testProcessNetDeviceParams(self):
        deviceXML = hostdev._process_device_params(
            libvirtconnection.get().nodeDeviceLookupByName(
                hostdevlib.NET_DEVICE).XMLDesc()
        )

        self.assertEqual(hostdevlib.NET_DEVICE_PROCESSED, deviceXML)

    def testProcessMdevDeviceParams(self):
        deviceXML = hostdev._process_device_params(
            libvirtconnection.get().nodeDeviceLookupByName(
                hostdevlib.MDEV_DEVICE).XMLDesc()
        )

        self.assertEqual(hostdevlib.MDEV_DEVICE_PROCESSED, deviceXML)

    def testGetDevicesFromLibvirt(self):
        libvirt_devices, _ = hostdev._get_devices_from_libvirt()

        self.assertEqual(hostdevlib.DEVICES_PROCESSED, libvirt_devices)
        self.assertEqual(len(libvirt_devices),
                         len(hostdevlib.PCI_DEVICES) +
                         len(hostdevlib.USB_DEVICES) +
                         len(hostdevlib.SCSI_DEVICES))

    @permutations([[''], [('pci',)], [('usb_device',)],
                   [('pci', 'usb_device')]])
    def testListByCaps(self, caps):
        devices = hostdev.list_by_caps(caps)

        for cap in caps:
            self.assertTrue(set(hostdevlib.DEVICES_BY_CAPS[cap].keys()).
                            issubset(devices.keys()))

    @permutations([
        # addr_type, addr, name
        ('usb', {'bus': '1', 'device': '2'}, 'usb_1_1'),
        ('usb', {'bus': '1', 'device': '10'}, 'usb_1_1_4'),
        ('pci', {'slot': '26', 'bus': '0', 'domain': '0', 'function': '0'},
         'pci_0000_00_1a_0'),
        ('scsi', {'bus': '0', 'host': '1', 'lun': '0', 'target': '0'},
         'scsi_1_0_0_0'),
    ])
    def test_device_name_from_address(self, addr_type, addr, name):
        # we need to make sure we scan all the devices (hence caps=None)
        hostdev.list_by_caps()
        self.assertEqual(
            hostdev.device_name_from_address(addr_type, addr),
            name
        )


@MonkeyClass(libvirtconnection, 'get', hostdevlib.Connection.get)
@MonkeyClass(hostdev, '_sriov_totalvfs', hostdevlib.fake_totalvfs)
@MonkeyClass(hostdev, '_pci_header_type', lambda _: 0)
@MonkeyClass(hooks, 'after_hostdev_list_by_caps', lambda json: json)
class HostdevPerformanceTests(TestCaseBase):

    def test_3k_storage_devices(self):
        with hostdevlib.Connection.use_hostdev_tree():
            self.assertEqual(
                len(hostdev.list_by_caps()),
                len(libvirtconnection.get().listAllDevices())
            )


@expandPermutations
@MonkeyClass(libvirtconnection, 'get', hostdevlib.Connection)
@MonkeyClass(hostdev, '_sriov_totalvfs', hostdevlib.fake_totalvfs)
@MonkeyClass(hostdev, '_pci_header_type', lambda _: 0)
class HostdevCreationTests(XMLTestCase):

    _PCI_ADDRESS = {'slot': '0x02', 'bus': '0x01', 'domain': '0x0000',
                    'function': '0x0', 'type': 'pci'}

    _PCI_ADDRESS_XML = '<address bus="0x01" domain="0x0000" function="0x0" \
        slot="0x02" type="pci"/>'

    def setUp(self):
        self.conf = {
            'vmName': 'testVm',
            'vmId': '9ffe28b6-6134-4b1e-8804-1185f49c436f',
            'smp': '8', 'maxVCpus': '160',
            'memSize': '1024', 'memGuaranteedSize': '512'}

    @permutations([
        [device_name]
        for device_name in hostdevlib.PCI_DEVICES +
        hostdevlib.USB_DEVICES + [hostdevlib.SCSI_DEVICES[2]]
    ])
    def testCreateHostDevice(self, device_name):
        dev_spec = {'type': 'hostdev', 'device': device_name}
        device = hostdevice.HostDevice(self.log, **dev_spec)
        self.assertXMLEqual(vmxml.format_xml(device.getXML()),
                            hostdevlib.DEVICE_XML[device_name] % ('',))

    @permutations([
        [device_name]
        for device_name in hostdevlib.PCI_DEVICES
    ])
    def testCreatePCIHostDeviceWithAddress(self, device_name):
        dev_spec = {'type': 'hostdev', 'device': device_name, 'address':
                    self._PCI_ADDRESS}
        device = hostdevice.HostDevice(self.log, **dev_spec)
        self.assertXMLEqual(
            vmxml.format_xml(device.getXML()),
            hostdevlib.DEVICE_XML[device_name] %
            (self._PCI_ADDRESS_XML))

    # TODO: next 2 tests should reside in their own module (interfaceTests.py)
    def testCreateSRIOVVF(self):
        dev_spec = {'type': hwclass.NIC, 'device': 'hostdev',
                    'hostdev': hostdevlib.SRIOV_VF,
                    'macAddr': 'ff:ff:ff:ff:ff:ff',
                    'specParams': {'vlanid': 3},
                    'bootOrder': '9'}
        device = network.Interface(self.log, **dev_spec)
        self.assertXMLEqual(
            vmxml.format_xml(device.getXML()),
            hostdevlib.DEVICE_XML[hostdevlib.SRIOV_VF] % ('',))

    def testCreateSRIOVVFWithAddress(self):
        dev_spec = {'type': hwclass.NIC, 'device': 'hostdev',
                    'hostdev': hostdevlib.SRIOV_VF,
                    'macAddr': 'ff:ff:ff:ff:ff:ff',
                    'specParams': {'vlanid': 3},
                    'bootOrder': '9', 'address':
                    {'slot': '0x02', 'bus': '0x01', 'domain': '0x0000',
                     'function': '0x0', 'type': 'pci'}}
        device = network.Interface(self.log, **dev_spec)
        self.assertXMLEqual(
            vmxml.format_xml(device.getXML()),
            hostdevlib.DEVICE_XML[hostdevlib.SRIOV_VF] % (
                self._PCI_ADDRESS_XML
            )
        )

    @permutations([[
        ['pci_0000_00_02_0'], 0],
        [[hostdevlib.SRIOV_PF, hostdevlib.SRIOV_VF], 1]
    ])
    def testNumaTuneXMLSingleNode(self, devices, numa_node):
        numatuneXML = """
          <numatune>
              <memory mode="preferred" nodeset="{}" />
          </numatune> """.format(numa_node)

        domxml = libvirtxml.Domain(self.conf, self.log, cpuarch.X86_64)
        devices = [hostdevice.HostDevice(
            self.log, **{'type': 'hostdev', 'device': device}) for
            device in devices]
        domxml.appendHostdevNumaTune(devices)
        xml = vmxml.format_xml(domxml.dom)
        self.assertXMLEqual(find_xml_element(xml, './numatune'), numatuneXML)

    def testNumaTuneXMLMultiNode(self):
        domxml = libvirtxml.Domain(self.conf, self.log, cpuarch.X86_64)
        devices = [
            hostdevice.HostDevice(
                self.log, **{'type': 'hostdev', 'device': device}
            ) for device in [
                hostdevlib.SRIOV_PF, hostdevlib.SRIOV_VF, 'pci_0000_00_02_0'
            ]
        ]
        domxml.appendHostdevNumaTune(devices)
        xml = vmxml.format_xml(domxml.dom)
        self.assertRaises(AssertionError,
                          lambda: find_xml_element(xml, './numatune'))
