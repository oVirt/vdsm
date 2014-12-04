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

import hostdev
import vmfakelib as fake

from testlib import VdsmTestCase as TestCaseBase
from testlib import permutations, expandPermutations
from monkeypatch import MonkeyClass

from vdsm import libvirtconnection

PCI_DEVICE_XML = [
    """
    <device>
        <name>pci_0000_00_1a_0</name>
        <path>/sys/devices/pci0000:00/0000:00:1a.0</path>
        <parent>computer</parent>
        <driver>
        <name>ehci-pci</name>
        </driver>
        <capability type='pci'>
        <domain>0</domain>
        <bus>0</bus>
        <slot>26</slot>
        <function>0</function>
        <product id='0x1c2d'>6 Series/C200 Series Chipset Family USB \
Enhanced Host Controller #2</product>
        <vendor id='0x8086'>Intel Corporation</vendor>
        </capability>
    </device>
    """,
    """
    <device>
        <name>pci_0000_00_1f_2</name>
        <path>/sys/devices/pci0000:00/0000:00:1f.2</path>
        <parent>computer</parent>
        <driver>
        <name>ahci</name>
        </driver>
        <capability type='pci'>
        <domain>0</domain>
        <bus>0</bus>
        <slot>31</slot>
        <function>2</function>
        <product id='0x1c03'>6 Series/C200 Series Chipset Family 6 port \
SATA AHCI Controller</product>
        <vendor id='0x8086'>Intel Corporation</vendor>
        </capability>
    </device>
    """,
    """
    <device>
        <name>pci_0000_00_02_0</name>
        <path>/sys/devices/pci0000:00/0000:00:02.0</path>
        <parent>computer</parent>
        <driver>
        <name>i915</name>
        </driver>
        <capability type='pci'>
        <domain>0</domain>
        <bus>0</bus>
        <slot>2</slot>
        <function>0</function>
        <product id='0x0126'>2nd Generation Core Processor Family \
Integrated Graphics Controller</product>
        <vendor id='0x8086'>Intel Corporation</vendor>
        </capability>
    </device>
    """,
    # in reality, the device above would be unavailable for passthrough,
    # but in case of tests that does not really matter as we can't
    # call real release()
    """
    <device>
        <name>pci_0000_00_19_0</name>
        <path>/sys/devices/pci0000:00/0000:00:19.0</path>
        <parent>computer</parent>
        <driver>
        <name>e1000e</name>
        </driver>
        <capability type='pci'>
        <domain>0</domain>
        <bus>0</bus>
        <slot>25</slot>
        <function>0</function>
        <product id='0x1502'>82579LM Gigabit Network Connection</product>
        <vendor id='0x8086'>Intel Corporation</vendor>
        </capability>
    </device>
    """,
    """
    <device>
        <name>pci_0000_00_1b_0</name>
        <path>/sys/devices/pci0000:00/0000:00:1b.0</path>
        <parent>computer</parent>
        <driver>
        <name>snd_hda_intel</name>
        </driver>
        <capability type='pci'>
        <domain>0</domain>
        <bus>0</bus>
        <slot>27</slot>
        <function>0</function>
        <product id='0x1c20'>6 Series/C200 Series Chipset Family High \
Definition Audio Controller</product>
        <vendor id='0x8086'>Intel Corporation</vendor>
    </capability>
    </device>
    """]

USB_DEVICE_XML = [
    """
    <device>
        <name>usb_usb1</name>
        <path>/sys/devices/pci0000:00/0000:00:1a.0/usb1</path>
        <parent>pci_0000_00_1a_0</parent>
        <driver>
        <name>usb</name>
        </driver>
        <capability type='usb_device'>
        <bus>1</bus>
        <device>1</device>
        <product id='0x0002'>EHCI Host Controller</product>
        <vendor id='0x1d6b'>Linux 3.10.0-123.6.3.el7.x86_64 \
ehci_hcd</vendor>
        </capability>
    </device>
    """,
    """
    <device>
        <name>usb_1_1</name>
        <path>/sys/devices/pci0000:00/0000:00:1a.0/usb1/1-1</path>
        <parent>usb_usb1</parent>
        <driver>
        <name>usb</name>
        </driver>
        <capability type='usb_device'>
        <bus>1</bus>
        <device>2</device>
        <product id='0x0024' />
        <vendor id='0x8087' />
        </capability>
    </device>
    """,
    """
    <device>
        <name>usb_1_1_4</name>
        <path>/sys/devices/pci0000:00/0000:00:1a.0/usb1/1-1/1-1.4</path>
        <parent>usb_1_1</parent>
        <driver>
        <name>usb</name>
        </driver>
        <capability type='usb_device'>
        <bus>1</bus>
        <device>10</device>
        <product id='0x217f'>Broadcom Bluetooth Device</product>
        <vendor id='0x0a5c'>Broadcom Corp</vendor>
        </capability>
    </device>
    """]

SCSI_DEVICE_XML = [
    """
    <device>
        <name>scsi_host0</name>
        <path>/sys/devices/pci0000:00/0000:00:1f.2/ata1/host0</path>
        <parent>pci_0000_00_1f_2</parent>
        <capability type='scsi_host'>
        <host>0</host>
        </capability>
    </device>
    """,
    """
    <device>
        <name>scsi_target0_0_0</name>
        <path>/sys/devices/pci0000:00/0000:00:1f.2/ata1/host0/\
target0:0:0</path>
        <parent>scsi_host0</parent>
        <capability type='scsi_target'>
        <target>target0:0:0</target>
        </capability>
    </device>
    """,
    """
    <device>
        <name>scsi_0_0_0_0</name>
        <path>/sys/devices/pci0000:00/0000:00:1f.2/ata1/host0/\
target0:0:0/0:0:0:0</path>
        <parent>scsi_target0_0_0</parent>
        <driver>
        <name>sd</name>
        </driver>
        <capability type='scsi'>
        <host>0</host>
        <bus>0</bus>
        <target>0</target>
        <lun>0</lun>
        <type>disk</type>
        </capability>
    </device>
    """]

ADDITIONAL_DEVICE = """
    <device>
        <name>pci_0000_00_09_0</name>
        <path>/sys/devices/pci0000:00/0000:00:09.0</path>
        <parent>computer</parent>
        <driver>
        <name>pcieport</name>
        </driver>
        <capability type='pci'>
        <domain>0</domain>
        <bus>0</bus>
        <slot>9</slot>
        <function>0</function>
        <product id='0x3410'>7500/5520/5500/X58 I/O Hub PCI Express Root \
Port 9</product>
        <vendor id='0x8086'>Intel Corporation</vendor>
        <iommuGroup number='4'>
            <address domain='0x0000' bus='0x00' slot='0x09' function='0x0'/>
        </iommuGroup>
        </capability>
    </device>
    """


DEVICES_PARSED = {u'pci_0000_00_1b_0': {'product': '6 Series/C200 Series '
                                        'Chipset Family High Definition '
                                        'Audio Controller',
                                        'vendor': 'Intel Corporation',
                                        'product_id': '0x1c20',
                                        'parent': 'computer',
                                        'vendor_id': '0x8086',
                                        'capability': 'pci'},
                  u'scsi_0_0_0_0': {'capability': 'scsi',
                                    'parent': 'scsi_target0_0_0'},
                  u'pci_0000_00_1a_0': {'product': '6 Series/C200 Series '
                                        'Chipset Family USB Enhanced Host '
                                        'Controller #2',
                                        'vendor': 'Intel Corporation',
                                        'product_id': '0x1c2d',
                                        'parent': 'computer',
                                        'vendor_id': '0x8086',
                                        'capability': 'pci'},
                  u'pci_0000_00_1f_2': {'product': '6 Series/C200 Series '
                                        'Chipset Family 6 port SATA AHCI '
                                        'Controller',
                                        'vendor': 'Intel Corporation',
                                        'product_id': '0x1c03',
                                        'parent': 'computer',
                                        'vendor_id': '0x8086',
                                        'capability': 'pci'},
                  u'scsi_target0_0_0': {'capability': 'scsi_target',
                                        'parent': 'scsi_host0'},
                  u'pci_0000_00_02_0': {'product': '2nd Generation Core '
                                        'Processor Family Integrated '
                                        'Graphics Controller',
                                        'vendor': 'Intel Corporation',
                                        'product_id': '0x0126',
                                        'parent': 'computer',
                                        'vendor_id': '0x8086',
                                        'capability': 'pci'},
                  u'scsi_host0': {'capability': 'scsi_host',
                                  'parent': 'pci_0000_00_1f_2'},
                  u'pci_0000_00_19_0': {'product': '82579LM Gigabit '
                                        'Network Connection',
                                        'vendor': 'Intel Corporation',
                                        'product_id': '0x1502',
                                        'parent': 'computer',
                                        'vendor_id': '0x8086',
                                        'totalvfs': 7,
                                        'capability': 'pci'},
                  u'usb_1_1_4': {'product': 'Broadcom Bluetooth Device',
                                 'vendor': 'Broadcom Corp',
                                 'product_id': '0x217f',
                                 'parent': 'usb_1_1',
                                 'vendor_id': '0x0a5c',
                                 'capability': 'usb_device'},
                  u'usb_1_1': {'product_id': '0x0024', 'parent':
                               'usb_usb1', 'vendor_id': '0x8087',
                               'capability': 'usb_device'},
                  u'usb_usb1': {'product': 'EHCI Host Controller',
                                'vendor': 'Linux 3.10.0-123.6.3.el7.x86_64 '
                                'ehci_hcd', 'product_id': '0x0002',
                                'parent': 'pci_0000_00_1a_0',
                                'vendor_id': '0x1d6b',
                                'capability': 'usb_device'}}

ADDITIONAL_DEVICE_PARSED = {'product': '7500/5520/5500/X58 I/O Hub PCI '
                            'Express Root Port 9',
                            'vendor': 'Intel Corporation',
                            'product_id': '0x3410',
                            'parent': 'computer',
                            'iommu_group': '4',
                            'vendor_id': '0x8086', 'capability': 'pci'}

DEVICE_TO_VM_MAPPING = {'usb_1_1_4': 'vmId1', 'pci_0000_00_19_0': 'vmId2'}

DEVICES_BY_CAPS = {'': {u'pci_0000_00_1b_0':
                        {'params': DEVICES_PARSED['pci_0000_00_1b_0']},
                        u'scsi_0_0_0_0':
                        {'params': DEVICES_PARSED['scsi_0_0_0_0']},
                        u'pci_0000_00_1a_0':
                        {'params': DEVICES_PARSED['pci_0000_00_1a_0']},
                        u'pci_0000_00_1f_2':
                        {'params': DEVICES_PARSED['pci_0000_00_1f_2']},
                        u'scsi_target0_0_0':
                        {'params': DEVICES_PARSED['scsi_target0_0_0']},
                        u'pci_0000_00_02_0':
                        {'params': DEVICES_PARSED['pci_0000_00_02_0']},
                        u'scsi_host0': {'params':
                                        DEVICES_PARSED['scsi_host0']},
                        u'usb_usb1': {'params': DEVICES_PARSED['usb_usb1']},
                        u'usb_1_1_4': {'vmId': 'vmId1', 'params':
                                       DEVICES_PARSED['usb_1_1_4']},
                        u'usb_1_1': {'params': DEVICES_PARSED['usb_1_1']},
                        u'pci_0000_00_19_0':
                        {'vmId': 'vmId2',
                         'params': DEVICES_PARSED['pci_0000_00_19_0']}},
                   'pci': {u'pci_0000_00_1b_0':
                           {'params': DEVICES_PARSED['pci_0000_00_1b_0']},
                           u'pci_0000_00_1a_0':
                           {'params': DEVICES_PARSED['pci_0000_00_1a_0']},
                           u'pci_0000_00_1f_2':
                           {'params': DEVICES_PARSED['pci_0000_00_1f_2']},
                           u'pci_0000_00_02_0':
                           {'params': DEVICES_PARSED['pci_0000_00_02_0']},
                           u'pci_0000_00_19_0':
                           {'vmId': 'vmId2',
                            'params': DEVICES_PARSED['pci_0000_00_19_0']}},
                   'usb_device': {u'usb_usb1':
                                  {'params': DEVICES_PARSED['usb_usb1']},
                                  u'usb_1_1_4':
                                  {'vmId': 'vmId1',
                                   'params': DEVICES_PARSED['usb_1_1_4']},
                                  u'usb_1_1':
                                  {'params': DEVICES_PARSED['usb_1_1']}}}


class Connection(fake.Connection):
    vmContainer = {'vmId1': fake.ConfStub({'devices': [{'device': 'hostdev',
                                                        'name':
                                                        'usb_1_1_4'}]}),
                   'vmId2': fake.ConfStub({'devices': [{'device': 'hostdev',
                                                        'name':
                                                        'pci_0000_00_19_0'}
                                                       ]})}

    def __init__(self, *args):
        self._virNodeDevices = []

        for device in PCI_DEVICE_XML + USB_DEVICE_XML + SCSI_DEVICE_XML:
            self._virNodeDevices.append(fake.VirNodeDeviceStub(device))

    def listAllDevices(self, flags=0):
        return self._virNodeDevices


def _fake_totalvfs(device_name):
    if device_name == 'pci_0000_00_19_0':
        return 7

    raise IOError


@expandPermutations
@MonkeyClass(libvirtconnection, 'get', Connection)
@MonkeyClass(hostdev, '_sriov_totalvfs', _fake_totalvfs)
class HostdevTests(TestCaseBase):

    def testParseDeviceParams(self):
        deviceXML = hostdev._parse_device_params(ADDITIONAL_DEVICE)

        self.assertEquals(ADDITIONAL_DEVICE_PARSED, deviceXML)

    def testGetDevicesFromLibvirt(self):
        libvirt_devices = hostdev._get_devices_from_libvirt()

        self.assertEqual(DEVICES_PARSED, libvirt_devices)
        self.assertEqual(len(libvirt_devices),
                         len(PCI_DEVICE_XML) +
                         len(USB_DEVICE_XML) +
                         len(SCSI_DEVICE_XML))

    def testGetDevicesFromVms(self):
        device_to_vm = hostdev._get_devices_from_vms(
            libvirtconnection.get().vmContainer)

        self.assertEqual(DEVICE_TO_VM_MAPPING, device_to_vm)

    @permutations([[''], [('pci',)], [('usb_device',)],
                   [('pci', 'usb_device')]])
    def testListByCaps(self, caps):
        devices = hostdev.list_by_caps(
            libvirtconnection.get().vmContainer, caps)

        for cap in caps:
            self.assertTrue(set(DEVICES_BY_CAPS[cap].keys()).
                            issubset(devices.keys()))
