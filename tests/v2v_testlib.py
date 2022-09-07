# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import
from __future__ import division

from collections import namedtuple
from io import BytesIO
import libvirt
import uuid

import vmfakecon as fake

VmSpec = namedtuple('VmSpec',
                    ['name', 'uuid', 'id', 'active', 'has_snapshots',
                     'has_disk_volume', 'has_disk_block'])

VM_SPECS = (
    VmSpec("RHEL_0", str(uuid.uuid4()), id=0, active=True,
           has_snapshots=False, has_disk_volume=True, has_disk_block=False),
    VmSpec("RHEL_1", str(uuid.uuid4()), id=1, active=True,
           has_snapshots=False, has_disk_volume=True, has_disk_block=False),
    VmSpec("RHEL_2", str(uuid.uuid4()), id=2, active=False,
           has_snapshots=False, has_disk_volume=True, has_disk_block=False),
    VmSpec("RHEL_3", str(uuid.uuid4()), id=3, active=False,
           has_snapshots=False, has_disk_volume=True, has_disk_block=False),
    VmSpec("RHEL_4", str(uuid.uuid4()), id=4, active=False,
           has_snapshots=True, has_disk_volume=False, has_disk_block=True),
    VmSpec("RHEL_5", str(uuid.uuid4()), id=5, active=False,
           has_snapshots=False, has_disk_volume=True, has_disk_block=True),
)


BLOCK_DEV_PATH = '/dev/mapper/vdev'

XML_DISK_VOLUME = """
        <disk type='file' device='disk'>
            <source file='[datastore1] RHEL/RHEL_{name}.vmdk' />
            <target dev='sda' bus='scsi'/>
            <address type='drive' controller='0' bus='0' target='0'
                     unit='0'/>
        </disk>
"""

XML_DISK_BLOCK = """
        <disk type='block' device='disk'>
            <source dev='{block}'/>
            <target dev='sdb' bus='scsi'/>
            <address type='drive' controller='0' bus='0' target='0'
                     unit='0'/>
        </disk>
"""


def _mac_from_uuid(vm_uuid):
    return "52:54:%s:%s:%s:%s" % (
        vm_uuid[:2], vm_uuid[2:4], vm_uuid[4:6], vm_uuid[6:8])


class FakeVolume(object):
    def __init__(self):
        self._bytes = BytesIO(b'x' * 1024)

    def info(self):
        # type, capacity, allocation
        return [0, 1024, 1024]

    def download(self, stream, offset, length, flags):
        stream.volume = self
        self._bytes.seek(0)

    def read(self, nbytes):
        return self._bytes.read(nbytes)

    def data(self):
        return self._bytes.getvalue()

    def seek(self, pos):
        self._bytes.seek(pos)

    def key(self):
        return "abcd12345"


class FakeStream(object):
    def __init__(self):
        self.volume = None

    def recv(self, nbytes):
        return self.volume.read(nbytes)

    def finish(self):
        self.volume = None


class MockVirDomain(object):

    def __init__(self, name="RHEL",
                 vm_uuid="564d7cb4-8e3d-06ec-ce82-7b2b13c6a611",
                 id=0,
                 active=False,
                 has_snapshots=False,
                 has_disk_volume=False,
                 has_disk_block=False
                 ):
        self._name = name
        self._uuid = vm_uuid
        self._mac_address = _mac_from_uuid(vm_uuid)
        self._id = id
        self._active = active
        self._has_snapshots = has_snapshots
        self._block_disk = FakeVolume()
        self._has_disk_block = has_disk_block
        self._has_disk_volume = has_disk_volume

    def name(self):
        return self._name

    def UUID(self):
        return self._uuid

    def ID(self):
        return self._id

    def state(self, flags=0):
        """
        VIR_DOMAIN_RUNNING = 1
        VIR_DOMAIN_SHUTOFF = 5
        """
        if self._active:
            return [1, 0]
        return [5, 0]

    def isActive(self):
        return self._active

    def XMLDesc(self, flags=0):
        params = {
            'name': self._name,
            'uuid': self._uuid,
            'block': BLOCK_DEV_PATH,
            'mac': self._mac_address
        }
        params['volume_disk'] = "" if not self._has_disk_volume else \
            XML_DISK_VOLUME.format(**params)

        params['block_disk'] = "" if not self._has_disk_block else \
            XML_DISK_BLOCK.format(**params)

        return """
<domain type='vmware' id='15'>
    <name>{name}</name>
    <uuid>{uuid}</uuid>
    <memory unit='KiB'>2097152</memory>
    <currentMemory unit='KiB'>2097152</currentMemory>
    <vcpu placement='static'>1</vcpu>
    <os>
        <type arch='x86_64'>hvm</type>
    </os>
    <clock offset='utc'/>
    <on_poweroff>destroy</on_poweroff>
    <on_reboot>restart</on_reboot>
    <on_crash>destroy</on_crash>
    <devices>
        {volume_disk}
        {block_disk}
        <disk type='file' device='cdrom'>
            <source file='[datastore1] RHEL/cdrom.iso' />
            <target dev='hdb' bus='ide'/>
            <readonly/>
        </disk>
        <disk type='file' device='floppy'>
            <driver name='qemu' type='raw' cache='none'/>
            <source file='/floppy.vfd'/>
            <target dev='fda' bus='fdc'/>
        </disk>

        <disk type='block' device='cdrom'>
            <source file='/dev/sr0' />
            <target dev='hdc' bus='ide'/>
            <readonly/>
        </disk>
        <controller type='scsi' index='0' model='vmpvscsi'/>
        <interface type='bridge'>
            <mac address='{mac}'/>
            <source bridge='VM Network'/>
            <model type='vmxnet3'/>
        </interface>
        <video>
            <model type='vmvga' vram='8192'/>
        </video>
    </devices>
</domain>""".format(**params)

    def hasCurrentSnapshot(self):
        return self._has_snapshots

    def setCurrentSnapshot(self, has_snapshot=False):
        self._has_snapshots = has_snapshot

    def blockInfo(self, source, flags=0):
        if not self._has_disk_block:
            raise fake.Error(libvirt.VIR_ERR_INTERNAL_ERROR,
                             "no such disk in this VM")
        # capacity, allocation, physical
        info = self._block_disk.info()
        return [info[1], info[2], info[1]]

    def blockPeek(self, disk, pos, size):
        if not self._has_disk_block:
            raise fake.Error(libvirt.VIR_ERR_INTERNAL_ERROR,
                             "no such disk in this VM")
        self._block_disk.seek(pos)
        return self._block_disk.read(size)


# FIXME: extend vmfakelib allowing to set predefined domain in Connection class
class MockVirConnect(object):

    def __init__(self, vms):
        self._vms = vms
        self._type = 'ESX'

    def close(self):
        pass

    def setType(self, type_name):
        self._type = type_name

    def getType(self):
        return self._type

    def listAllDomains(self):
        return [vm for vm in self._vms]

    def listDefinedDomains(self):
        # listDefinedDomains return only inactive domains
        return [vm.name() for vm in self._vms if not vm.isActive()]

    def listDomainsID(self):
        # listDomainsID return only active domains
        return [vm.ID() for vm in self._vms if vm.isActive()]

    def lookupByName(self, name):
        for vm in self._vms:
            if vm.name() == name:
                return vm
        raise fake.Error(libvirt.VIR_ERR_NO_DOMAIN,
                         'virDomainLookupByName() failed')

    def lookupByID(self, id):
        for vm in self._vms:
            if vm.ID() == id:
                return vm
        raise fake.Error(libvirt.VIR_ERR_NO_DOMAIN,
                         'virDomainLookupByID() failed')

    def storageVolLookupByPath(self, name):
        if not any([vm._has_disk_volume for vm in self._vms]):
            raise fake.Error(libvirt.VIR_ERR_INTERNAL_ERROR,
                             "no volume in storage")
        return FakeVolume()

    def newStream(self):
        return FakeStream()
