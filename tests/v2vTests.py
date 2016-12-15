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
from contextlib import contextmanager
from StringIO import StringIO
import tarfile
import zipfile
import uuid

import libvirt
import os

from testlib import namedTemporaryDir, permutations, expandPermutations
from vdsm import v2v
from vdsm import libvirtconnection
from vdsm.password import ProtectedPassword
from vdsm.commands import execCmd
from vdsm.utils import CommandPath


from testlib import VdsmTestCase as TestCaseBase, recorded
from monkeypatch import MonkeyPatch, MonkeyPatchScope

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
           has_snapshots=True),
)

FAKE_VIRT_V2V = CommandPath('fake-virt-v2v',
                            os.path.abspath('fake-virt-v2v'))
FAKE_SSH_ADD = CommandPath('fake-ssh-add',
                           os.path.abspath('fake-ssh-add'))
FAKE_SSH_AGENT = CommandPath('fake-ssh-agent',
                             os.path.abspath('fake-ssh-agent'))


def _mac_from_uuid(vm_uuid):
    return "52:54:%s:%s:%s:%s" % (
        vm_uuid[:2], vm_uuid[2:4], vm_uuid[4:6], vm_uuid[6:8])


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
            uuid=self._uuid,
            mac=self._mac_address)

    def hasCurrentSnapshot(self):
        return self._has_snapshots


# FIXME: extend vmfakelib allowing to set predefined domain in Connection class
class MockVirConnect(object):

    def __init__(self, vms):
        self._vms = vms

    def close(self):
        pass

    def getType(self):
        return "ESX"

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
        return MockVirConnect.Volume()

    class Volume(object):
        def info(self):
            return [0, 0, 0]


def legacylistAllDomains():
    raise fake.Error(libvirt.VIR_ERR_NO_SUPPORT,
                     'Method not supported')


def legacylistAllDomainsWrongRaise():
    raise fake.Error(libvirt.VIR_ERR_NO_DOMAIN,
                     'Domain not exists')


def lookupByNameFailure(name):
    raise fake.Error(libvirt.VIR_ERR_NO_DOMAIN,
                     'Domain not exists')


def lookupByIDFailure(id):
    raise fake.Error(libvirt.VIR_ERR_NO_DOMAIN,
                     'Domain not exists')


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
    return MockVirConnect()


def read_ovf(ovf_path):
    return """<?xml version="1.0" encoding="UTF-8"?>
<Envelope xmlns="http://schemas.dmtf.org/ovf/envelope/1"
          xmlns:ovf="http://schemas.dmtf.org/ovf/envelope/1"
          xmlns:rasd="http://schemas.dmtf.org/wbem/wscim/1/cim-schema/2/CIM_Re\
sourceAllocationSettingData">
  <References>
    <File ovf:href="First-disk1.vmdk" ovf:id="file1" ovf:size="349405696"/>
    <File ovf:href="First-disk2.vmdk" ovf:id="file2" ovf:size="349405696"/>
  </References>
  <DiskSection>
    <Disk ovf:capacity="32" ovf:fileRef="file1"/>
    <Disk ovf:capacity="32" ovf:populatedSize="698811392" ovf:fileRef="file2"/>
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


@contextmanager
def temporary_ovf_dir():
    with namedTemporaryDir() as base:
        ovfpath = os.path.join(base, 'testvm.ovf')
        ovapath = os.path.join(base, 'testvm.ova')
        ovf = read_ovf('test')

        with open(ovfpath, 'w') as ovffile:
            ovffile.write(ovf)
        yield ovapath


@expandPermutations
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
        self.vpx_url = 'vpx://adminr%40vsphere@0.0.0.0/ovirt/' \
                       '0.0.0.0?no_verify=1'
        self.xen_url = 'xen+ssh://user@host.com'

        self.vminfo = {'vmName': self.vm_name,
                       'poolID': self.pool_id,
                       'domainID': self.domain_id,
                       'disks': [{'imageID': self.image_id_a,
                                  'volumeID': self.volume_id_a},
                                 {'imageID': self.image_id_b,
                                  'volumeID': self.volume_id_b}]}

        self._vms = [MockVirDomain(*spec) for spec in VM_SPECS]

    def tearDown(self):
        v2v._jobs.clear()

    def testGetExternalVMs(self):
        def _connect(uri, username, passwd):
            return MockVirConnect(vms=self._vms)

        with MonkeyPatchScope([(libvirtconnection, 'open_connection',
                                _connect)]):
            vms = v2v.get_external_vms('esx://mydomain', 'user',
                                       ProtectedPassword('password'))['vmList']

        self.assertEqual(len(vms), len(VM_SPECS))

        for vm, spec in zip(vms, VM_SPECS):
            self._assertVmMatchesSpec(vm, spec)
            self._assertVmDisksMatchSpec(vm, spec)

    def testGetExternalVMsWithXMLDescFailure(self):
        specs = list(VM_SPECS)

        def internal_error(flags=0):
            raise fake.Error(libvirt.VIR_ERR_INTERNAL_ERROR)

        fake_vms = [MockVirDomain(*spec) for spec in specs]
        # Cause vm 1 to fail, so it would not appear in results
        fake_vms[1].XMLDesc = internal_error
        del specs[1]

        def _connect(uri, username, passwd):
            return MockVirConnect(vms=fake_vms)

        with MonkeyPatchScope([(libvirtconnection, 'open_connection',
                                _connect)]):
            vms = v2v.get_external_vms('esx://mydomain', 'user',
                                       ProtectedPassword('password'))['vmList']

        self.assertEqual(len(vms), len(specs))

        for vm, spec in zip(vms, specs):
            self._assertVmMatchesSpec(vm, spec)
            self._assertVmDisksMatchSpec(vm, spec)

    def testLegacyGetExternalVMs(self):
        def _connect(uri, username, passwd):
            mock = MockVirConnect(vms=self._vms)
            mock.listAllDomains = legacylistAllDomains
            return mock

        with MonkeyPatchScope([(libvirtconnection, 'open_connection',
                                _connect)]):
            vms = v2v.get_external_vms('esx://mydomain', 'user',
                                       ProtectedPassword('password')
                                       )['vmList']
            self.assertEqual(len(vms), len(self._vms))

    def testLegacyGetExternalVMsFailure(self):
        def _connect(uri, username, passwd):
            mock = MockVirConnect(vms=self._vms)
            mock.listAllDomains = legacylistAllDomainsWrongRaise
            return mock

        with MonkeyPatchScope([(libvirtconnection, 'open_connection',
                                _connect)]):
            self.assertRaises(libvirt.libvirtError,
                              v2v.get_external_vms,
                              'esx://mydomain', 'user',
                              ProtectedPassword('password'))

    @permutations([
        # (methodname, fakemethod, active)
        ['lookupByName', lookupByNameFailure, True],
        ['lookupByID', lookupByIDFailure, False]
    ])
    def testLookupFailure(self, methodname, fakemethod, active):
        def _connect(uri, username, passwd):
            mock = MockVirConnect(vms=self._vms)
            mock.listAllDomains = legacylistAllDomains
            setattr(mock, methodname, fakemethod)
            return mock

        with MonkeyPatchScope([(libvirtconnection, 'open_connection',
                                _connect)]):
            vms = v2v.get_external_vms('esx://mydomain', 'user',
                                       ProtectedPassword('password')
                                       )['vmList']
            self.assertEqual(
                sorted(vm['vmName'] for vm in vms),
                sorted(spec.name for spec in VM_SPECS
                       if spec.active == active)
                )

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

    def testGetExternalVMsWithoutDisksInfo(self):
        def internal_error(name):
            raise fake.Error(libvirt.VIR_ERR_INTERNAL_ERROR)

        # we need a sequence of just one vm
        mock = MockVirConnect(vms=self._vms[:1])
        mock.storageVolLookupByPath = internal_error

        def _connect(uri, username, passwd):
            return mock

        with MonkeyPatchScope([(libvirtconnection, 'open_connection',
                                _connect)]):
            vms = v2v.get_external_vms('esx://mydomain', 'user',
                                       ProtectedPassword('password'))['vmList']
        self.assertEquals(len(vms), 1)
        self._assertVmMatchesSpec(vms[0], VM_SPECS[0])
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
        self.assertEquals(vm['vmId'], spec.uuid)
        self.assertEquals(vm['memSize'], 2048)
        self.assertEquals(vm['smp'], 1)
        self.assertEquals(len(vm['disks']), 1)
        self.assertEquals(len(vm['networks']), 1)
        self.assertEquals(vm['has_snapshots'], spec.has_snapshots)

        network = vm['networks'][0]
        self.assertEquals(network['type'], 'bridge')
        self.assertEquals(network['macAddr'], _mac_from_uuid(spec.uuid))
        self.assertEquals(network['bridge'], 'VM Network')

    def testSuccessfulVMWareImport(self):
        self._commonConvertExternalVM(self.vpx_url)

    @MonkeyPatch(v2v, '_SSH_ADD', FAKE_SSH_ADD)
    @MonkeyPatch(v2v, '_SSH_AGENT', FAKE_SSH_AGENT)
    def testSuccessfulXenImport(self):
        self._commonConvertExternalVM(self.xen_url)

    @MonkeyPatch(v2v, '_VIRT_V2V', FAKE_VIRT_V2V)
    def testSuccessfulImportOVA(self):
        with temporary_ovf_dir() as ovapath:
            v2v.convert_ova(ovapath, self.vminfo, self.job_id, FakeIRS())
            job = v2v._jobs[self.job_id]
            job.wait()

            self.assertEqual(job.status, v2v.STATUS.DONE)

    def testV2VOutput(self):
        cmd = [FAKE_VIRT_V2V.cmd,
               '-ic', self.vpx_url,
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

    @MonkeyPatch(v2v, '_VIRT_V2V', FAKE_VIRT_V2V)
    @MonkeyPatch(v2v, '_V2V_DIR', None)
    def _commonConvertExternalVM(self, url):
        with namedTemporaryDir() as v2v._V2V_DIR:
            v2v.convert_external_vm(url,
                                    'root',
                                    ProtectedPassword('mypassword'),
                                    self.vminfo,
                                    self.job_id,
                                    FakeIRS())
            job = v2v._jobs[self.job_id]
            job.wait()

            self.assertEqual(job.status, v2v.STATUS.DONE)


class MockVirConnectTests(TestCaseBase):
    def setUp(self):
        self._vms = [MockVirDomain(*spec) for spec in VM_SPECS]
        self._mock = MockVirConnect(vms=self._vms)

    def test_list_all_domains(self):
        vms = self._mock.listAllDomains()
        self.assertEqual(len(vms), len(self._vms))

    def test_list_defined_domains(self):
        vms = self._mock.listDefinedDomains()
        self.assertEqual(
            sorted(vms),
            sorted(spec.name for spec in VM_SPECS if not spec.active))

    def test_list_domains_id(self):
        vms = self._mock.listDomainsID()
        self.assertEqual(len(vms), 2)

    def test_lookup_by_name(self):
        vm = self._mock.lookupByName('RHEL_0')
        self.assertEquals('RHEL_0', vm.name())

    def test_lookup_by_name_failed(self):
        self.assertRaises(libvirt.libvirtError, self._mock.lookupByName,
                          'fakename')

    def test_lookup_by_id(self):
        vm = self._mock.lookupByID(0)
        self.assertEquals(0, vm.ID())

    def test_lookup_by_id_failed(self):
        self.assertRaises(libvirt.libvirtError, self._mock.lookupByID, 99)


class TestGetOVAInfo(TestCaseBase):
    def test_directory(self):
        with self.temporary_ovf_dir() as (base, ovfpath, ovapath):
            vm = v2v.get_ova_info(base)
            self.check(vm['vmList'])

    def test_tar(self):
        with self.temporary_ovf_dir() as (base, ovfpath, ovapath):
            with tarfile.open(ovapath, 'w') as tar:
                tar.add(ovfpath, arcname='testvm.ovf')
            vm = v2v.get_ova_info(ovapath)
            self.check(vm['vmList'])

    def test_zip(self):
        with self.temporary_ovf_dir() as (base, ovfpath, ovapath):
            with zipfile.ZipFile(ovapath, 'w') as zip:
                zip.write(ovfpath)
            vm = v2v.get_ova_info(ovapath)
            self.check(vm['vmList'])

    @contextmanager
    def temporary_ovf_dir(self):
        with namedTemporaryDir() as base:
            ovfpath = os.path.join(base, 'testvm.ovf')
            ovapath = os.path.join(base, 'testvm.ova')
            ovf = read_ovf('test')

            with open(ovfpath, 'w') as ovffile:
                ovffile.write(ovf)
            yield base, ovfpath, ovapath

    def check(self, vm):
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
