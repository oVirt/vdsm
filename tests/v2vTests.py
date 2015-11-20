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

from collections import namedtuple
from StringIO import StringIO
import uuid

import libvirt
import os

import v2v
from vdsm import libvirtconnection
from vdsm.password import ProtectedPassword
from vdsm.utils import CommandPath, execCmd


from nose.plugins.skip import SkipTest
from testlib import VdsmTestCase as TestCaseBase, recorded
from monkeypatch import MonkeyPatch, MonkeyPatchScope

import vmfakelib as fake


VmSpec = namedtuple('VmSpec', ['name', 'vmid'])

FAKE_VIRT_V2V = CommandPath('fake-virt-v2v',
                            os.path.abspath('fake-virt-v2v'))


def _mac_from_uuid(vmid):
    return "52:54:%s:%s:%s:%s" % (
        vmid[:2], vmid[2:4], vmid[4:6], vmid[6:8])


class VmMock(object):

    def __init__(self, name="RHEL",
                 vmid="564d7cb4-8e3d-06ec-ce82-7b2b13c6a611"):
        self._name = name
        self._vmid = vmid
        self._mac_address = _mac_from_uuid(vmid)

    def name(self):
        return self._name

    def state(self, flags=0):
        return [5, 0]

    def XMLDesc(self, flags=0):
        return """
<domain type='vmware' id='15'>
    <name>{name}</name>
    <uuid>{vmid}</uuid>
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
        <disk type='file' device='disk'>
            <source file='[datastore1] RHEL/RHEL_{name}.vmdk'/>
            <target dev='sda' bus='scsi'/>
            <address type='drive' controller='0' bus='0' target='0' unit='0'/>
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
            vmid=self._vmid,
            mac=self._mac_address)


# FIXME: extend vmfakelib allowing to set predefined domain in Connection class
class LibvirtMock(object):

    def __init__(self, vms):
        self._vms = vms

    def close(self):
        pass

    def listAllDomains(self):
        return self._vms

    def storageVolLookupByPath(self, name):
        return LibvirtMock.Volume()

    class Volume(object):
        def info(self):
            return [0, 0, 0]


class FakeIRS(object):
    @recorded
    def prepareImage(self, domainId, poolId, imageId, volumeId):
        return {'status': {'code': 0},
                'path': os.path.join('/rhev/data-center', poolId, domainId,
                                     'images', imageId, volumeId)}

    @recorded
    def teardownImage(self, domainId, poolId, imageId):
        return 0


def hypervisorConnect(uri, username, passwd):
    return LibvirtMock()


def read_ovf(ovf_path):
    return """<?xml version="1.0" encoding="UTF-8"?>
<Envelope xmlns="http://schemas.dmtf.org/ovf/envelope/1"
          xmlns:ovf="http://schemas.dmtf.org/ovf/envelope/1"
          xmlns:rasd="http://schemas.dmtf.org/wbem/wscim/1/cim-schema/2/CIM_Re\
sourceAllocationSettingData">
  <References>
    <File ovf:href="First-disk1.vmdk" ovf:id="file1" ovf:size="349405696"/>
  </References>
  <DiskSection>
    <Disk ovf:capacity="32" ovf:fileRef="file1"/>
  </DiskSection>
  <VirtualSystem ovf:id="First">
    <Name>First</Name>
    <VirtualHardwareSection>
      <Item>
        <rasd:ResourceType>4</rasd:ResourceType>
        <rasd:VirtualQuantity>2048</rasd:VirtualQuantity>
      </Item>
      <Item>
        <rasd:ResourceType>3</rasd:ResourceType>
        <rasd:VirtualQuantity>1</rasd:VirtualQuantity>
      </Item>
      <Item>
        <rasd:Connection>VM Network</rasd:Connection>
        <rasd:ElementName>Ethernet 1</rasd:ElementName>
        <rasd:ResourceSubType>E1000</rasd:ResourceSubType>
        <rasd:ResourceType>10</rasd:ResourceType>
      </Item>
    </VirtualHardwareSection>
  </VirtualSystem>
</Envelope>"""


class v2vTests(TestCaseBase):
    def setUp(self):
        '''
        We are testing the output of fake-virt-v2v with vminfo input
        against pre-saved output;
        Do not change this parameters without modifying
        fake-virt-v2v.output.
        '''
        self.vm_name = 'TEST'
        self.job_id = '00000000-0000-0000-0000-000000000005'
        self.pool_id = '00000000-0000-0000-0000-000000000006'
        self.domain_id = '00000000-0000-0000-0000-000000000007'
        self.image_id_a = '00000000-0000-0000-0000-000000000001'
        self.volume_id_a = '00000000-0000-0000-0000-000000000002'
        self.image_id_b = '00000000-0000-0000-0000-000000000003'
        self.volume_id_b = '00000000-0000-0000-0000-000000000004'
        self.url = 'vpx://adminr%40vsphere@0.0.0.0/ovirt/0.0.0.0?no_verify=1'

    _VM_SPECS = (
        VmSpec("RHEL_0", str(uuid.uuid4())),
        VmSpec("RHEL_1", str(uuid.uuid4())),
        VmSpec("RHEL_2", str(uuid.uuid4()))
    )

    _VMS = [VmMock(*spec) for spec in _VM_SPECS]

    def testGetExternalVMs(self):
        if not v2v.supported():
            raise SkipTest('v2v is not supported current os version')

        def _connect(uri, username, passwd):
            return LibvirtMock(vms=self._VMS)

        with MonkeyPatchScope([(libvirtconnection, 'open_connection',
                                _connect)]):
            vms = v2v.get_external_vms('esx://mydomain', 'user',
                                       ProtectedPassword('password'))['vmList']

        self.assertEqual(len(vms), len(self._VM_SPECS))

        for vm, spec in zip(vms, self._VM_SPECS):
            self._assertVmMatchesSpec(vm, spec)
            self._assertVmDisksMatchSpec(vm, spec)

    def testGetExternalVMsWithXMLDescFailure(self):
        specs = list(self._VM_SPECS)

        def internal_error(flags=0):
            raise fake.Error(libvirt.VIR_ERR_INTERNAL_ERROR)

        fake_vms = [VmMock(*spec) for spec in specs]
        # Cause vm 1 to fail, so it would not appear in results
        fake_vms[1].XMLDesc = internal_error
        del specs[1]

        def _connect(uri, username, passwd):
            return LibvirtMock(vms=fake_vms)

        with MonkeyPatchScope([(libvirtconnection, 'open_connection',
                                _connect)]):
            vms = v2v.get_external_vms('esx://mydomain', 'user',
                                       ProtectedPassword('password'))['vmList']

        self.assertEqual(len(vms), len(specs))

        for vm, spec in zip(vms, specs):
            self._assertVmMatchesSpec(vm, spec)
            self._assertVmDisksMatchSpec(vm, spec)

    def testOutputParser(self):
        output = ''.join(['[   0.0] Opening the source -i libvirt ://roo...\n',
                          '[   1.0] Creating an overlay to protect the f...\n',
                          '[  88.0] Copying disk 1/2 to /tmp/v2v/0000000...\n',
                          '    (0/100%)\r',
                          '    (50/100%)\r',
                          '    (100/100%)\r',
                          '[ 180.0] Copying disk 2/2 to /tmp/v2v/100000-...\n',
                          '    (0/100%)\r',
                          '    (50/100%)\r',
                          '    (100/100%)\r',
                          '[ 256.0] Creating output metadata',
                          '[ 256.0] Finishing off'])

        parser = v2v.OutputParser()
        events = list(parser.parse(StringIO(output)))
        self.assertEqual(events, [
            (v2v.ImportProgress(1, 2, 'Copying disk 1/2')),
            (v2v.DiskProgress(0)),
            (v2v.DiskProgress(50)),
            (v2v.DiskProgress(100)),
            (v2v.ImportProgress(2, 2, 'Copying disk 2/2')),
            (v2v.DiskProgress(0)),
            (v2v.DiskProgress(50)),
            (v2v.DiskProgress(100))])

    @MonkeyPatch(v2v, '_read_ovf_from_ova', read_ovf)
    def testGetOvaInfo(self):
        ret = v2v.get_ova_info("dummy")
        vm = ret['vmList']
        self.assertEquals(vm['vmName'], 'First')
        self.assertEquals(vm['memSize'], 2048)
        self.assertEquals(vm['smp'], 1)

        disk = vm['disks'][0]
        self.assertEquals(disk['allocation'], '349405696')
        self.assertEquals(disk['capacity'], '34359738368')
        self.assertEquals(disk['type'], 'disk')
        self.assertEquals(disk['alias'], 'First-disk1.vmdk')

        network = vm['networks'][0]
        self.assertEquals(network['bridge'], 'VM Network')
        self.assertEquals(network['model'], 'E1000')
        self.assertEquals(network['type'], 'bridge')
        self.assertEquals(network['dev'], 'Ethernet 1')

    def testGetExternalVMsWithoutDisksInfo(self):
        if not v2v.supported():
            raise SkipTest('v2v is not supported current os version')

        def internal_error(name):
            raise fake.Error(libvirt.VIR_ERR_INTERNAL_ERROR)

        # we need a sequence of just one vm
        mock = LibvirtMock(vms=self._VMS[:1])
        mock.storageVolLookupByPath = internal_error

        def _connect(uri, username, passwd):
            return mock

        with MonkeyPatchScope([(libvirtconnection, 'open_connection',
                                _connect)]):
            vms = v2v.get_external_vms('esx://mydomain', 'user',
                                       ProtectedPassword('password'))['vmList']
        self.assertEquals(len(vms), 1)
        self._assertVmMatchesSpec(vms[0], self._VM_SPECS[0])
        for disk in vms[0]['disks']:
            self.assertNotIn('capacity', disk)
            self.assertNotIn('allocation', disk)

    def _assertVmDisksMatchSpec(self, vm, spec):
        disk = vm['disks'][0]
        self.assertEquals(disk['dev'], 'sda')
        self.assertEquals(disk['alias'],
                          '[datastore1] RHEL/RHEL_%s.vmdk' % spec.name)
        self.assertIn('capacity', disk)
        self.assertIn('allocation', disk)

    def _assertVmMatchesSpec(self, vm, spec):
        self.assertEquals(vm['vmId'], spec.vmid)
        self.assertEquals(vm['memSize'], 2048)
        self.assertEquals(vm['smp'], 1)
        self.assertEquals(len(vm['disks']), 1)
        self.assertEquals(len(vm['networks']), 1)

        network = vm['networks'][0]
        self.assertEquals(network['type'], 'bridge')
        self.assertEquals(network['macAddr'], _mac_from_uuid(spec.vmid))
        self.assertEquals(network['bridge'], 'VM Network')

    @MonkeyPatch(v2v, '_VIRT_V2V', FAKE_VIRT_V2V)
    def testSuccessfulImport(self):
        vminfo = {'vmName': self.vm_name,
                  'poolID': self.pool_id,
                  'domainID': self.domain_id,
                  'disks': [{'imageID': self.image_id_a,
                             'volumeID': self.volume_id_a},
                            {'imageID': self.image_id_b,
                             'volumeID': self.volume_id_b}]}
        ivm = v2v.ImportVm.from_libvirt(self.url, 'root', 'mypassword',
                                        vminfo, self.job_id, FakeIRS())
        ivm._run_command = ivm._run
        ivm.start()
        ivm.wait()

        self.assertEqual(ivm.status, v2v.STATUS.DONE)

    def testV2VOutput(self):
        cmd = [FAKE_VIRT_V2V.cmd,
               '-ic', self.url,
               '-o', 'vdsm',
               '-of', 'raw',
               '-oa', 'sparse',
               '--vdsm-image-uuid', self.image_id_a,
               '--vdsm-vol-uuid', self.volume_id_a,
               '--vdsm-image-uuid', self.image_id_b,
               '--vdsm-vol-uuid', self.volume_id_b,
               '--password-file', '/tmp/mypass',
               '--vdsm-vm-uuid', self.job_id,
               '--vdsm-ovf-output', '/usr/local/var/run/vdsm/v2v',
               '--machine-readable',
               '-os', '/rhev/data-center/%s/%s' % (self.pool_id,
                                                   self.domain_id),
               self.vm_name]

        rc, output, error = execCmd(cmd, raw=True)
        self.assertEqual(rc, 0)

        with open('fake-virt-v2v.out', 'r') as f:
            self.assertEqual(output, f.read())
