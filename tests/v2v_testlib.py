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
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA
#
# Refer to the README and COPYING files for full details of the license
#

from collections import namedtuple
from io import BytesIO
import libvirt
import uuid

import vmfakelib as fake

VmSpec = namedtuple('VmSpec',
                    ['name', 'uuid', 'id', 'active', 'has_snapshots'])

VM_SPECS = (
    VmSpec("RHEL_0", str(uuid.uuid4()), id=0, active=True,
           has_snapshots=False),
    VmSpec("RHEL_1", str(uuid.uuid4()), id=1, active=True,
           has_snapshots=False),
    VmSpec("RHEL_2", str(uuid.uuid4()), id=2, active=False,
           has_snapshots=False),
    VmSpec("RHEL_3", str(uuid.uuid4()), id=3, active=False,
           has_snapshots=False),
    VmSpec("RHEL_4", str(uuid.uuid4()), id=4, active=False,
           has_snapshots=False),
)


BLOCK_DEV_PATH = '/dev/mapper/vdev'


def _mac_from_uuid(vm_uuid):
    return "52:54:%s:%s:%s:%s" % (
        vm_uuid[:2], vm_uuid[2:4], vm_uuid[4:6], vm_uuid[6:8])


class FakeVolume(object):
    def __init__(self):
        self._bytes = BytesIO('x' * 1024)

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
                 has_snapshots=False):
        self._name = name
        self._uuid = vm_uuid
        self._mac_address = _mac_from_uuid(vm_uuid)
        self._id = id
        self._active = active
        self._has_snapshots = has_snapshots
        self._disk_type = 'file'
        self._block_disk = FakeVolume()

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

    def setDiskType(self, disk_type):
        self._disk_type = disk_type

    def XMLDesc(self, flags=0):
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
        <disk type='{disk_type}' device='disk'>
            <source file='[datastore1] RHEL/RHEL_{name}.vmdk' dev='{block}'/>
            <target dev='sda' bus='scsi'/>
            <address type='drive' controller='0' bus='0' target='0' unit='0'/>
        </disk>
        <disk type='{disk_type}' device='cdrom'>
            <source file='[datastore1] RHEL/cdrom.iso' />
            <target dev='hdb' bus='ide'/>
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
</domain>""".format(
            name=self._name,
            block=BLOCK_DEV_PATH,
            uuid=self._uuid,
            disk_type=self._disk_type,
            mac=self._mac_address)

    def hasCurrentSnapshot(self):
        return self._has_snapshots

    def setCurrentSnapshot(self, has_snapshot=False):
        self._has_snapshots = has_snapshot

    def blockInfo(self, source):
        # capacity, allocation, physical
        info = self._block_disk.info()
        return [info[1], info[2], info[1]]

    def blockPeek(self, disk, pos, size):
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
        return FakeVolume()

    def newStream(self):
        return FakeStream()
