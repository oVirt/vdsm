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

from collections import namedtuple
from contextlib import contextmanager

from vdsm.common import hostdev

import vmfakecon as fake


PCI_DEVICES = ['pci_0000_00_1a_0', 'pci_0000_00_1f_2', 'pci_0000_00_02_0',
               'pci_0000_00_19_0', 'pci_0000_00_1b_0']
USB_DEVICES = ['usb_usb1', 'usb_1_1', 'usb_1_1_4']
SCSI_DEVICES = ['scsi_host0', 'scsi_target0_0_0', 'scsi_0_0_0_0',
                'block_sda_ssd', 'scsi_generic_sg0',
                'scsi_host1', 'scsi_target1_0_0', 'scsi_1_0_0_0',
                'scsi_generic_sg1',
                'scsi_host2', 'scsi_target2_0_0', 'scsi_2_0_0_0']
INVALID_DEVICES = ['pci_that_doesnt_exist']
SRIOV_PF = 'pci_0000_05_00_1'
SRIOV_VF = 'pci_0000_05_10_7'
ADDITIONAL_DEVICE = 'pci_0000_00_09_0'
COMPUTER_DEVICE = 'computer'
NET_DEVICE = 'net_em1_28_d2_44_55_66_88'
MDEV_DEVICE = 'pci_0000_06_00_0'

DEVICE_XML = {
    'pci_0000_00_02_0':
    '''
    <hostdev managed="no" mode="subsystem" type="pci">
      <source>
        <address bus="0x00" domain="0x0000" function="0x0" slot="0x02"/>
      </source>
      %s
    </hostdev>
    ''',
    'pci_0000_00_19_0':
    '''
    <hostdev managed="no" mode="subsystem" type="pci">
      <source>
        <address bus="0x00" domain="0x0000" function="0x0" slot="0x19"/>
      </source>
      %s
    </hostdev>
    ''',
    'pci_0000_00_1a_0':
    '''
    <hostdev managed="no" mode="subsystem" type="pci">
      <source>
        <address bus="0x00" domain="0x0000" function="0x0" slot="0x1a"/>
      </source>
      %s
    </hostdev>
    ''',
    'pci_0000_00_1b_0':
    '''
    <hostdev managed="no" mode="subsystem" type="pci">
      <source>
        <address bus="0x00" domain="0x0000" function="0x0" slot="0x1b"/>
      </source>
      %s
    </hostdev>
    ''',
    'pci_0000_00_1f_2':
    '''
    <hostdev managed="no" mode="subsystem" type="pci">
      <source>
        <address bus="0x00" domain="0x0000" function="0x2" slot="0x1f"/>
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
    SRIOV_VF:
    '''
    <interface managed="no" type="hostdev">
            %s
            <mac address="ff:ff:ff:ff:ff:ff"/>
            <source>
                    <address bus="0x05" domain="0x0000"
                      function="0x7" slot="0x10" type="pci"/>
            </source>
            <vlan>
                    <tag id="3"/>
            </vlan>
            <link state="up"/>
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
                                       'block_path': '/dev/sdc',
                                       'product': 'SSD',
                                       'vendor': 'ATA'},
                     u'scsi_1_0_0_0': {'capability': 'scsi',
                                       'driver': 'sd',
                                       'parent': 'scsi_target1_0_0',
                                       'is_assignable': 'true',
                                       'address': {'bus': '0', 'host': '1',
                                                   'lun': '0', 'target': '0'},
                                       'udev_path': '/dev/sg1',
                                       'block_path': '/dev/sdd'},
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
                                           'numa_node': '0',
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

UDEV_BLOCK_MAP = {'/dev/sg0': '/dev/sdc',
                  '/dev/sg1': '/dev/sdd'}

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

MDEV_DEVICE_PROCESSED = {
    'product': 'GM204GL [Tesla M60]',
    'vendor': 'NVIDIA Corporation',
    'product_id': '0x13f2',
    'parent': 'pci_0000_05_08_0',
    'iommu_group': '33',
    'numa_node': '0',
    'vendor_id': '0x10de',
    'driver': 'nvidia',
    'capability': 'pci',
    'is_assignable': 'true',
    'mdev': {
        'nvidia-11': {'available_instances': '16', 'name': 'GRID M60-0B'},
        'nvidia-12': {'available_instances': '16', 'name': 'GRID M60-0Q'},
        'nvidia-13': {'available_instances': '8', 'name': 'GRID M60-1A'},
        'nvidia-14': {'available_instances': '8', 'name': 'GRID M60-1B'},
        'nvidia-15': {'available_instances': '8', 'name': 'GRID M60-1Q'},
        'nvidia-16': {'available_instances': '4', 'name': 'GRID M60-2A'},
        'nvidia-18': {'available_instances': '4', 'name': 'GRID M60-2Q'},
        'nvidia-19': {'available_instances': '2', 'name': 'GRID M60-4A'},
        'nvidia-20': {'available_instances': '2', 'name': 'GRID M60-4Q'},
        'nvidia-21': {'available_instances': '1', 'name': 'GRID M60-8A'},
        'nvidia-22': {'available_instances': '1', 'name': 'GRID M60-8Q'}
    },
    'address': {
        'slot': '0',
        'bus': '6',
        'domain': '0',
        'function': '0'
    }
}


COMPUTER_DEVICE_PROCESSED = {'capability': 'system', 'is_assignable': 'true'}

NET_DEVICE_PROCESSED = {
    'parent': 'pci_0000_00_19_0',
    'capability': 'net',
    'interface': 'em1',
    'is_assignable': 'true',
}

SRIOV_PF_PROCESSED = {'capability': 'pci',
                      'driver': 'igb',
                      'is_assignable': 'true',
                      'address': {'slot': '0',
                                  'bus': '5',
                                  'domain': '0',
                                  'function': '1'},
                      'iommu_group': '15',
                      'parent': 'pci_0000_00_09_0',
                      'numa_node': '1',
                      'product': '82576 Gigabit Network Connection',
                      'product_id': '0x10c9',
                      'totalvfs': 7,
                      'vendor': 'Intel Corporation',
                      'vendor_id': '0x8086'}

SRIOV_VF_PROCESSED = {'capability': 'pci',
                      'driver': 'igbvf',
                      'is_assignable': 'true',
                      'address': {'slot': '16',
                                  'bus': '5',
                                  'domain': '0',
                                  'function': '7'},
                      'iommu_group': '25',
                      'parent': 'pci_0000_00_09_0',
                      'numa_node': '1',
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

    USE_HOSTDEV_TREE = False

    inst = None

    @classmethod
    def get(cls, *args):
        if not cls.inst:
            cls.inst = cls(*args)

        return cls.inst

    def __init__(self, *args):
        self._virNodeDevices = [
            self.nodeDeviceLookupByName(device) for device in
            PCI_DEVICES + USB_DEVICES + SCSI_DEVICES + INVALID_DEVICES
        ]

    def listAllDevices(self, flags=0):
        node_devs = self._virNodeDevices
        if self.USE_HOSTDEV_TREE:
            node_devs = self.__hostdevtree()

        if not flags:
            return node_devs
        else:
            return [device for device in node_devs if
                    flags & hostdev._LIBVIRT_DEVICE_FLAGS[device.capability]]

    @classmethod
    @contextmanager
    def use_hostdev_tree(cls):
        old_value = cls.USE_HOSTDEV_TREE
        cls.USE_HOSTDEV_TREE = True
        try:
            yield
        finally:
            cls.USE_HOSTDEV_TREE = old_value


def fake_totalvfs(device_name):
    if device_name == 'pci_0000_05_00_1':
        return 7

    raise IOError


class FakeMdevType(object):

    def __init__(self, name, available_instances):
        self.name = name
        self.available_instances = available_instances
        self.instances = []

    def mdev_create(self, mdev_uuid):
        if self.available_instances <= 0:
            raise IOError("No available instance")
        self.instances.append(mdev_uuid)
        self.available_instances -= 1


FakeMdevDevice = namedtuple('FakeMdevDevice', ['name', 'vendor', 'mdev_types'])


def fake_mdev_vendor(device):
    return device.vendor


def fake_mdev_types(device):
    for t in device.mdev_types:
        yield t.name, t


def fake_mdev_instances(mdev_type, path):
    return path.instances


def fake_mdev_details(mdev_type, path):
    return hostdev._MdevDetail(
        available_instances=path.available_instances,
        name='', description='', device_api=''
    )


class FakeSuperVdsm:

    def getProxy(self):
        return self

    def mdev_create(self, device, mdev_type, mdev_uuid):
        for device_type in device.mdev_types:
            if device_type.name == mdev_type:
                break
        device_type.mdev_create(mdev_uuid)
