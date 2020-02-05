#
# Copyright 2014-2020 Red Hat, Inc.
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

import six

from vdsm.common import exception
from vdsm.common import xmlutils
from vdsm.virt.vmdevices import network, hwclass

from testlib import VdsmTestCase as TestCaseBase, XMLTestCase
from testlib import permutations, expandPermutations
from monkeypatch import MonkeyClass, MonkeyPatchScope
from testValidation import skipif

from vdsm.common import hooks
from vdsm.common import hostdev
from vdsm.common import libvirtconnection

import hostdevlib


@expandPermutations
@MonkeyClass(libvirtconnection, 'get', hostdevlib.Connection)
@MonkeyClass(hostdev, '_sriov_totalvfs', hostdevlib.fake_totalvfs)
@MonkeyClass(hostdev, '_pci_header_type', lambda _: 0)
@MonkeyClass(hooks, 'after_hostdev_list_by_caps', lambda json: json)
@MonkeyClass(hostdev, '_get_udev_block_mapping',
             lambda: hostdevlib.UDEV_BLOCK_MAP)
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

    @skipif(six.PY3, "Not relevant in Python 3 libvirt")
    # libvirt in Python 3 returns strings, so we don't deal with
    # invalid coding anymore.
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
                            issubset(set(devices.keys())))

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

    # TODO: next 2 tests should reside in their own module (interfaceTests.py)
    def testCreateSRIOVVF(self):
        dev_spec = {'type': hwclass.NIC, 'device': 'hostdev',
                    'hostdev': hostdevlib.SRIOV_VF,
                    'macAddr': 'ff:ff:ff:ff:ff:ff',
                    'specParams': {'vlanid': 3},
                    'bootOrder': '9'}
        device = network.Interface(self.log, **dev_spec)
        self.assertXMLEqual(
            xmlutils.tostring(device.getXML()),
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
            xmlutils.tostring(device.getXML()),
            hostdevlib.DEVICE_XML[hostdevlib.SRIOV_VF] % (
                self._PCI_ADDRESS_XML
            )
        )


@expandPermutations
@MonkeyClass(hostdev, '_each_supported_mdev_type', hostdevlib.fake_mdev_types)
@MonkeyClass(hostdev, '_mdev_type_details', hostdevlib.fake_mdev_details)
@MonkeyClass(hostdev, '_mdev_device_vendor', hostdevlib.fake_mdev_vendor)
@MonkeyClass(hostdev, '_mdev_type_devices', hostdevlib.fake_mdev_instances)
@MonkeyClass(hostdev, 'supervdsm', hostdevlib.FakeSuperVdsm())
class TestMdev(TestCaseBase):
    def setUp(self):
        def make_device(name):
            mdev_types = [
                hostdevlib.FakeMdevType('incompatible-1', 2),
                hostdevlib.FakeMdevType('8q', 1),
                hostdevlib.FakeMdevType('4q', 2),
                hostdevlib.FakeMdevType('incompatible-2', 2),
            ]
            return hostdevlib.FakeMdevDevice(name=name, vendor='0x10de',
                                             mdev_types=mdev_types)

        self.devices = [make_device(name) for name in ('card-1', 'card-2',)]

    @permutations([
        # (mdev_type, mdev_uuid)*, mdev_placement, instances
        [[('4q', '4q-1')],
         hostdev.MdevPlacement.COMPACT, [['4q-1'], []]],
        [[('8q', '8q-1')],
         hostdev.MdevPlacement.SEPARATE, [['8q-1'], []]],
        [[('4q', '4q-1'), ('4q', '4q-2')],
         hostdev.MdevPlacement.COMPACT, [['4q-1', '4q-2'], []]],
        [[('4q', '4q-1'), ('8q', '8q-1')],
         hostdev.MdevPlacement.COMPACT, [['4q-1'], ['8q-1']]],
        [[('4q', '4q-1'), ('4q', '4q-2')],
         hostdev.MdevPlacement.SEPARATE, [['4q-1'], ['4q-2']]],
        [[('4q', '4q-1'), ('8q', '8q-1'), ('4q', '4q-2')],
         hostdev.MdevPlacement.COMPACT, [['4q-1', '4q-2'], ['8q-1']]],
        [[('8q', '8q-1'), ('4q', '4q-1'), ('4q', '4q-2')],
         hostdev.MdevPlacement.COMPACT, [['8q-1'], ['4q-1', '4q-2']]],
        [[('4q', '4q-1'), ('4q', '4q-2'), ('8q', '8q-1')],
         hostdev.MdevPlacement.COMPACT, [['4q-1', '4q-2'], ['8q-1']]],
        [[('4q', '4q-1'), ('8q', '8q-1'), ('4q', '4q-2')],
         hostdev.MdevPlacement.SEPARATE, [['4q-1', '4q-2'], ['8q-1']]],
    ])
    def test_vgpu_placement(self, mdev_specs, mdev_placement, instances):
        with MonkeyPatchScope([
                (hostdev, '_each_mdev_device', lambda: self.devices)
        ]):
            for mdev_type, mdev_uuid in mdev_specs:
                hostdev.spawn_mdev(mdev_type, mdev_uuid, mdev_placement,
                                   self.log)
        for inst, dev in zip(instances, self.devices):
            dev_inst = []
            for mdev_type in dev.mdev_types:
                dev_inst.extend(mdev_type.instances)
            self.assertEqual(inst, dev_inst)

    @permutations([
        [hostdev.MdevPlacement.COMPACT],
        [hostdev.MdevPlacement.SEPARATE],
    ])
    def test_unsupported_vgpu_placement(self, placement):
        with MonkeyPatchScope([
                (hostdev, '_each_mdev_device', lambda: self.devices)
        ]):
            self.assertRaises(
                exception.ResourceUnavailable,
                hostdev.spawn_mdev, 'unsupported', '1234', placement, self.log
            )
