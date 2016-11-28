#
# Copyright 2014 Red Hat, Inc.
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


import vmfakelib as fake

from virt.vmdevices import hostdevice, network, hwclass

from testlib import VdsmTestCase as TestCaseBase, XMLTestCase
from testlib import permutations, expandPermutations
from testValidation import slowtest
from monkeypatch import MonkeyClass

from vdsm import hooks
from vdsm import hostdev
from vdsm import libvirtconnection

_PCI_DEVICES = ['pci_0000_00_1a_0', 'pci_0000_00_1f_2', 'pci_0000_00_02_0',
                'pci_0000_00_19_0', 'pci_0000_00_1b_0']
_USB_DEVICES = ['usb_usb1', 'usb_1_1', 'usb_1_1_4']
_SCSI_DEVICES = ['scsi_host0', 'scsi_target0_0_0', 'scsi_0_0_0_0',
                 'block_sda_ssd', 'scsi_generic_sg0',
                 'scsi_host1', 'scsi_target1_0_0', 'scsi_1_0_0_0',
                 'scsi_generic_sg1',
                 'scsi_host2', 'scsi_target2_0_0', 'scsi_2_0_0_0']
_SRIOV_PF = 'pci_0000_05_00_1'
_SRIOV_VF = 'pci_0000_05_10_7'
_ADDITIONAL_DEVICE = 'pci_0000_00_09_0'
_COMPUTER_DEVICE = 'computer'
_NET_DEVICE = 'net_em1_28_d2_44_55_66_88'

_DEVICE_XML = {
    'pci_0000_00_02_0':
    '''
    <hostdev managed="no" mode="subsystem" type="pci">
            <source>
                    <address bus="0" domain="0" function="0" slot="2"/>
            </source>
            %s
    </hostdev>
    ''',
    'pci_0000_00_19_0':
    '''
    <hostdev managed="no" mode="subsystem" type="pci">
            <source>
                    <address bus="0" domain="0" function="0" slot="25"/>
            </source>
            %s
    </hostdev>
    ''',
    'pci_0000_00_1a_0':
    '''
    <hostdev managed="no" mode="subsystem" type="pci">
            <source>
                    <address bus="0" domain="0" function="0" slot="26"/>
            </source>
            %s
    </hostdev>
    ''',
    'pci_0000_00_1b_0':
    '''
    <hostdev managed="no" mode="subsystem" type="pci">
            <source>
                    <address bus="0" domain="0" function="0" slot="27"/>
            </source>
            %s
    </hostdev>
    ''',
    'pci_0000_00_1f_2':
    '''
    <hostdev managed="no" mode="subsystem" type="pci">
            <source>
                    <address bus="0" domain="0" function="2" slot="31"/>
            </source>
            %s
    </hostdev>
    ''',
    'usb_1_1':
    '''
    <hostdev managed="no" mode="subsystem" type="usb">
            <source>
                    <address bus="1" device="2"/>
            </source>
            %s
    </hostdev>
    ''',
    'usb_1_1_4':
    '''
    <hostdev managed="no" mode="subsystem" type="usb">
            <source>
                    <address bus="1" device="10"/>
            </source>
            %s
    </hostdev>
    ''',
    'usb_usb1':
    '''
    <hostdev managed="no" mode="subsystem" type="usb">
            <source>
                    <address bus="1" device="1"/>
            </source>
            %s
    </hostdev>
    ''',
    'scsi_0_0_0_0':
    '''
    <hostdev managed="no" mode="subsystem" rawio="yes" type="scsi">
            <source>
                    <adapter name="scsi_host0"/>
                    <address bus="0" unit="0" target="0"/>
            </source>
            %s
    </hostdev>
    ''',
    _SRIOV_VF:
    '''
    <interface managed="no" type="hostdev">
            %s
            <mac address="ff:ff:ff:ff:ff:ff"/>
            <source>
                    <address bus="5" domain="0" function="7" slot="16"
                    type="pci"/>
            </source>
            <vlan>
                    <tag id="3"/>
            </vlan>
            <boot order="9"/>
            <driver name="vfio"/>
    </interface>
    '''}

DEVICES_PROCESSED = {u'pci_0000_00_1b_0': {'product': '6 Series/C200 Series '
                                           'Chipset Family High Definition '
                                           'Audio Controller',
                                           'vendor': 'Intel Corporation',
                                           'product_id': '0x1c20',
                                           'parent': 'computer',
                                           'vendor_id': '0x8086',
                                           'capability': 'pci',
                                           'driver': 'snd_hda_intel',
                                           'is_assignable': 'true',
                                           'address': {'slot': '27',
                                                       'bus': '0',
                                                       'domain': '0',
                                                       'function': '0'}},
                     u'scsi_0_0_0_0': {'capability': 'scsi',
                                       'driver': 'sd',
                                       'is_assignable': 'true',
                                       'parent': 'scsi_target0_0_0',
                                       'address': {'bus': '0', 'host': '0',
                                                   'lun': '0', 'target': '0'},
                                       'udev_path': '/dev/sg0',
                                       'product': 'SSD',
                                       'vendor': 'ATA'},
                     u'scsi_1_0_0_0': {'capability': 'scsi',
                                       'driver': 'sd',
                                       'parent': 'scsi_target1_0_0',
                                       'is_assignable': 'true',
                                       'address': {'bus': '0', 'host': '1',
                                                   'lun': '0', 'target': '0'},
                                       'udev_path': '/dev/sg1'},
                     u'scsi_2_0_0_0': {'capability': 'scsi',
                                       'driver': 'sd',
                                       'parent': 'scsi_target2_0_0',
                                       'is_assignable': 'true',
                                       'address': {'bus': '0', 'host': '2',
                                                   'lun': '0', 'target': '0'}},
                     u'pci_0000_00_1a_0': {'product': '6 Series/C200 Series '
                                           'Chipset Family USB Enhanced Host '
                                           'Controller #2',
                                           'vendor': 'Intel Corporation',
                                           'product_id': '0x1c2d',
                                           'parent': 'computer',
                                           'vendor_id': '0x8086',
                                           'capability': 'pci',
                                           'is_assignable': 'true',
                                           'address': {'slot': '26',
                                                       'bus': '0',
                                                       'domain': '0',
                                                       'function': '0'}},
                     u'pci_0000_00_1f_2': {'product': '6 Series/C200 Series '
                                           'Chipset Family 6 port SATA AHCI '
                                           'Controller',
                                           'vendor': 'Intel Corporation',
                                           'product_id': '0x1c03',
                                           'parent': 'computer',
                                           'vendor_id': '0x8086',
                                           'capability': 'pci',
                                           'driver': 'ahci',
                                           'is_assignable': 'true',
                                           'address': {'slot': '31',
                                                       'bus': '0',
                                                       'domain': '0',
                                                       'function': '2'}},
                     u'scsi_target0_0_0': {'capability': 'scsi_target',
                                           'parent': 'scsi_host0',
                                           'is_assignable': 'true'},
                     u'block_sda_ssd': {'capability': 'storage',
                                        'product': 'SSD',
                                        'parent': 'scsi_0_0_0_0',
                                        'vendor': 'ATA',
                                        'is_assignable': 'true'},
                     u'scsi_target1_0_0': {'capability': 'scsi_target',
                                           'is_assignable': 'true',
                                           'parent': 'scsi_host1'},
                     u'scsi_target2_0_0': {'capability': 'scsi_target',
                                           'is_assignable': 'true',
                                           'parent': 'scsi_host2'},
                     u'block_sda_ssd': {'capability': 'storage',
                                        'product': 'SSD',
                                        'parent': 'scsi_0_0_0_0',
                                        'is_assignable': 'true',
                                        'vendor': 'ATA'},
                     u'pci_0000_00_02_0': {'product': '2nd Generation Core '
                                           'Processor Family Integrated '
                                           'Graphics Controller',
                                           'vendor': 'Intel Corporation',
                                           'product_id': '0x0126',
                                           'parent': 'computer',
                                           'vendor_id': '0x8086',
                                           'capability': 'pci',
                                           'driver': 'i915',
                                           'is_assignable': 'true',
                                           'address': {'slot': '2',
                                                       'bus': '0',
                                                       'domain': '0',
                                                       'function': '0'}},
                     u'scsi_host0': {'capability': 'scsi_host',
                                     'parent': 'pci_0000_00_1f_2',
                                     'is_assignable': 'true',
                                     'parent': 'pci_0000_00_1f_2'},
                     u'scsi_host1': {'capability': 'scsi_host',
                                     'is_assignable': 'true',
                                     'parent': 'pci_0000_00_1f_2'},
                     u'scsi_host2': {'capability': 'scsi_host',
                                     'is_assignable': 'true',
                                     'parent': 'pci_0000_00_1f_2'},
                     u'pci_0000_00_19_0': {'product': '82579LM Gigabit '
                                           'Network Connection',
                                           'vendor': 'Intel Corporation',
                                           'product_id': '0x1502',
                                           'parent': 'computer',
                                           'vendor_id': '0x8086',
                                           'capability': 'pci',
                                           'driver': 'e1000e',
                                           'is_assignable': 'true',
                                           'address': {'slot': '25',
                                                       'bus': '0',
                                                       'domain': '0',
                                                       'function': '0'}},
                     u'scsi_generic_sg0': {'capability': 'scsi_generic',
                                           'udev_path': '/dev/sg0',
                                           'is_assignable': 'true',
                                           'parent': 'scsi_0_0_0_0'},
                     u'scsi_generic_sg1': {'capability': 'scsi_generic',
                                           'udev_path': '/dev/sg1',
                                           'is_assignable': 'true',
                                           'parent': 'scsi_1_0_0_0'},
                     u'usb_1_1_4': {'product': 'Broadcom Bluetooth Device',
                                    'vendor': 'Broadcom Corp',
                                    'product_id': '0x217f',
                                    'parent': 'usb_1_1',
                                    'vendor_id': '0x0a5c',
                                    'address': {'bus': '1', 'device': '10'},
                                    'capability': 'usb_device',
                                    'driver': 'usb',
                                    'is_assignable': 'true'},
                     u'usb_1_1': {'product_id': '0x0024', 'parent':
                                  'usb_usb1', 'vendor_id': '0x8087',
                                  'address': {'bus': '1', 'device': '2'},
                                  'capability': 'usb_device',
                                  'driver': 'usb',
                                  'is_assignable': 'true'},
                     u'usb_usb1': {'product': 'EHCI Host Controller',
                                   'vendor': 'Linux 3.10.0-123.6.3.el7.x86_64 '
                                   'ehci_hcd', 'product_id': '0x0002',
                                   'address': {'bus': '1', 'device': '1'},
                                   'parent': 'pci_0000_00_1a_0',
                                   'vendor_id': '0x1d6b',
                                   'capability': 'usb_device',
                                   'driver': 'usb',
                                   'is_assignable': 'true'}}

ADDITIONAL_DEVICE_PROCESSED = {'product': '7500/5520/5500/X58 I/O Hub PCI '
                               'Express Root Port 9',
                               'driver': 'pcieport',
                               'is_assignable': 'true',
                               'vendor': 'Intel Corporation',
                               'product_id': '0x3410',
                               'parent': 'computer',
                               'iommu_group': '4',
                               'vendor_id': '0x8086', 'capability': 'pci',
                               'address': {'slot': '9',
                                           'bus': '0',
                                           'domain': '0',
                                           'function': '0'}}

_COMPUTER_DEVICE_PROCESSED = {'capability': 'system', 'is_assignable': 'true'}

_NET_DEVICE_PROCESSED = {
    'parent': 'pci_0000_00_19_0',
    'capability': 'net',
    'interface': 'em1',
    'is_assignable': 'true',
}

_SRIOV_PF_PROCESSED = {'capability': 'pci',
                       'driver': 'igb',
                       'is_assignable': 'true',
                       'address': {'slot': '0',
                                   'bus': '5',
                                   'domain': '0',
                                   'function': '1'},
                       'iommu_group': '15',
                       'parent': 'pci_0000_00_09_0',
                       'product': '82576 Gigabit Network Connection',
                       'product_id': '0x10c9',
                       'totalvfs': 7,
                       'vendor': 'Intel Corporation',
                       'vendor_id': '0x8086'}

_SRIOV_VF_PROCESSED = {'capability': 'pci',
                       'driver': 'igbvf',
                       'is_assignable': 'true',
                       'address': {'slot': '16',
                                   'bus': '5',
                                   'domain': '0',
                                   'function': '7'},
                       'iommu_group': '25',
                       'parent': 'pci_0000_00_09_0',
                       'physfn': 'pci_0000_05_00_1',
                       'product': '82576 Virtual Function',
                       'product_id': '0x10ca',
                       'vendor': 'Intel Corporation',
                       'vendor_id': '0x8086'}

DEVICES_BY_CAPS = {'': {u'pci_0000_00_1b_0':
                        {'params': DEVICES_PROCESSED['pci_0000_00_1b_0']},
                        u'scsi_0_0_0_0':
                        {'params': DEVICES_PROCESSED['scsi_0_0_0_0']},
                        u'pci_0000_00_1a_0':
                        {'params': DEVICES_PROCESSED['pci_0000_00_1a_0']},
                        u'pci_0000_00_1f_2':
                        {'params': DEVICES_PROCESSED['pci_0000_00_1f_2']},
                        u'scsi_target0_0_0':
                        {'params': DEVICES_PROCESSED['scsi_target0_0_0']},
                        u'pci_0000_00_02_0':
                        {'params': DEVICES_PROCESSED['pci_0000_00_02_0']},
                        u'scsi_host0': {'params':
                                        DEVICES_PROCESSED['scsi_host0']},
                        u'usb_usb1': {'params': DEVICES_PROCESSED['usb_usb1']},
                        u'usb_1_1_4':
                        {'params': DEVICES_PROCESSED['usb_1_1_4']},
                        u'usb_1_1': {'params': DEVICES_PROCESSED['usb_1_1']},
                        u'pci_0000_00_19_0':
                        {'params': DEVICES_PROCESSED['pci_0000_00_19_0']}},
                   'pci': {u'pci_0000_00_1b_0':
                           {'params': DEVICES_PROCESSED['pci_0000_00_1b_0']},
                           u'pci_0000_00_1a_0':
                           {'params': DEVICES_PROCESSED['pci_0000_00_1a_0']},
                           u'pci_0000_00_1f_2':
                           {'params': DEVICES_PROCESSED['pci_0000_00_1f_2']},
                           u'pci_0000_00_02_0':
                           {'params': DEVICES_PROCESSED['pci_0000_00_02_0']},
                           u'pci_0000_00_19_0':
                           {'params': DEVICES_PROCESSED['pci_0000_00_19_0']}},
                   'usb_device': {u'usb_usb1':
                                  {'params': DEVICES_PROCESSED['usb_usb1']},
                                  u'usb_1_1_4':
                                  {'params': DEVICES_PROCESSED['usb_1_1_4']},
                                  u'usb_1_1':
                                  {'params': DEVICES_PROCESSED['usb_1_1']}}}


class Connection(fake.Connection):

    def __init__(self, *args):
        self._virNodeDevices = [
            self.nodeDeviceLookupByName(device) for device in
            _PCI_DEVICES + _USB_DEVICES + _SCSI_DEVICES
        ]

    def listAllDevices(self, flags=0):
        if not flags:
            return self._virNodeDevices
        else:
            return [device for device in self._virNodeDevices if
                    flags & hostdev._LIBVIRT_DEVICE_FLAGS[device.capability]]

    def listAllDevices2(self, flags=0):
        if not flags:
            return self.__hostdevtree()
        else:
            devices = self.__hostdevtree()
            return [device for device in devices if
                    flags & hostdev._LIBVIRT_DEVICE_FLAGS[device.capability]]


def _fake_totalvfs(device_name):
    if device_name == 'pci_0000_05_00_1':
        return 7

    raise IOError


@expandPermutations
@MonkeyClass(libvirtconnection, 'get', Connection)
@MonkeyClass(hostdev, '_sriov_totalvfs', _fake_totalvfs)
@MonkeyClass(hostdev, '_pci_header_type', lambda _: 0)
@MonkeyClass(hooks, 'after_hostdev_list_by_caps', lambda json: json)
class HostdevTests(TestCaseBase):

    def testProcessDeviceParams(self):
        deviceXML = hostdev._process_device_params(
            libvirtconnection.get().nodeDeviceLookupByName(
                _ADDITIONAL_DEVICE).XMLDesc()
        )

        self.assertEquals(ADDITIONAL_DEVICE_PROCESSED, deviceXML)

    def testProcessDeviceParamsInvalidEncoding(self):
        deviceXML = hostdev._process_device_params(
            libvirtconnection.get().nodeDeviceLookupByName(
                _COMPUTER_DEVICE).XMLDesc()
        )

        self.assertEquals(_COMPUTER_DEVICE_PROCESSED, deviceXML)

    def testProcessSRIOV_PFDeviceParams(self):
        deviceXML = hostdev._process_device_params(
            libvirtconnection.get().nodeDeviceLookupByName(
                _SRIOV_PF).XMLDesc()
        )

        self.assertEquals(_SRIOV_PF_PROCESSED, deviceXML)

    def testProcessSRIOV_VFDeviceParams(self):
        deviceXML = hostdev._process_device_params(
            libvirtconnection.get().nodeDeviceLookupByName(
                _SRIOV_VF).XMLDesc()
        )

        self.assertEquals(_SRIOV_VF_PROCESSED, deviceXML)

    def testProcessNetDeviceParams(self):
        deviceXML = hostdev._process_device_params(
            libvirtconnection.get().nodeDeviceLookupByName(
                _NET_DEVICE).XMLDesc()
        )

        self.assertEquals(_NET_DEVICE_PROCESSED, deviceXML)

    def testGetDevicesFromLibvirt(self):
        libvirt_devices = hostdev._get_devices_from_libvirt()

        self.assertEqual(DEVICES_PROCESSED, libvirt_devices)
        self.assertEqual(len(libvirt_devices),
                         len(_PCI_DEVICES) +
                         len(_USB_DEVICES) +
                         len(_SCSI_DEVICES))

    @permutations([[''], [('pci',)], [('usb_device',)],
                   [('pci', 'usb_device')]])
    def testListByCaps(self, caps):
        devices = hostdev.list_by_caps(caps)

        for cap in caps:
            self.assertTrue(set(DEVICES_BY_CAPS[cap].keys()).
                            issubset(devices.keys()))


@MonkeyClass(Connection, 'listAllDevices', Connection.listAllDevices2)
@MonkeyClass(libvirtconnection, 'get', Connection)
@MonkeyClass(hostdev, '_sriov_totalvfs', _fake_totalvfs)
@MonkeyClass(hostdev, '_pci_header_type', lambda _: 0)
@MonkeyClass(hooks, 'after_hostdev_list_by_caps', lambda json: json)
class HostdevPerformanceTests(TestCaseBase):

    @slowtest
    def test_3k_storage_devices(self):
        devices = hostdev.list_by_caps()
        self.assertEqual(len(devices),
                         len(libvirtconnection.get().listAllDevices2()))


@expandPermutations
@MonkeyClass(libvirtconnection, 'get', Connection)
@MonkeyClass(hostdev, '_sriov_totalvfs', _fake_totalvfs)
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

    @permutations([[device] for device in _PCI_DEVICES + _USB_DEVICES +
                   [_SCSI_DEVICES[2]]])
    def testCreateHostDevice(self, device_name):
        dev_spec = {'type': 'hostdev', 'device': device_name}
        device = hostdevice.HostDevice(self.conf, self.log, **dev_spec)
        self.assertXMLEqual(device.getXML().toxml(),
                            _DEVICE_XML[device_name] % ('',))

    @permutations([[device] for device in _PCI_DEVICES])
    def testCreatePCIHostDeviceWithAddress(self, device_name):
        dev_spec = {'type': 'hostdev', 'device': device_name, 'address':
                    self._PCI_ADDRESS}
        device = hostdevice.HostDevice(self.conf, self.log, **dev_spec)
        self.assertXMLEqual(
            device.getXML().toxml(),
            _DEVICE_XML[device_name] %
            (self._PCI_ADDRESS_XML))

    # TODO: next 2 tests should reside in their own module (interfaceTests.py)
    def testCreateSRIOVVF(self):
        dev_spec = {'type': hwclass.NIC, 'device': 'hostdev',
                    'hostdev': _SRIOV_VF, 'macAddr': 'ff:ff:ff:ff:ff:ff',
                    'specParams': {'vlanid': 3},
                    'bootOrder': '9'}
        device = network.Interface(self.conf, self.log, **dev_spec)
        self.assertXMLEqual(device.getXML().toxml(),
                            _DEVICE_XML[_SRIOV_VF] % ('',))

    def testCreateSRIOVVFWithAddress(self):
        dev_spec = {'type': hwclass.NIC, 'device': 'hostdev',
                    'hostdev': _SRIOV_VF, 'macAddr': 'ff:ff:ff:ff:ff:ff',
                    'specParams': {'vlanid': 3},
                    'bootOrder': '9', 'address':
                    {'slot': '0x02', 'bus': '0x01', 'domain': '0x0000',
                     'function': '0x0', 'type': 'pci'}}
        device = network.Interface(self.conf, self.log, **dev_spec)
        self.assertXMLEqual(
            device.getXML().toxml(),
            _DEVICE_XML[_SRIOV_VF] % (self._PCI_ADDRESS_XML))
