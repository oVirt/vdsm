# Copyright 2014-2021 Red Hat, Inc.
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

from contextlib import contextmanager
import io
import subprocess
import tarfile
import time
import uuid
import zipfile

import libvirt
import os
import pytest

from testlib import namedTemporaryDir, permutations, expandPermutations
from v2v_testlib import VM_SPECS, MockVirDomain
from v2v_testlib import MockVirConnect, _mac_from_uuid, BLOCK_DEV_PATH
from vdsm import v2v
from vdsm.common import libvirtconnection
from vdsm.common import response
from vdsm.common.cmdutils import CommandPath, exec_cmd
from vdsm.common.commands import terminating
from vdsm.common.password import HiddenValue

from testlib import VdsmTestCase as TestCaseBase, recorded
from monkeypatch import MonkeyPatch, MonkeyPatchScope

import vmfakecon as fake


FAKE_VIRT_V2V = CommandPath('fake-virt-v2v',
                            os.path.abspath('fake-virt-v2v'))
FAKE_SSH_ADD = CommandPath('fake-ssh-add',
                           os.path.abspath('fake-ssh-add'))
FAKE_SSH_AGENT = CommandPath('fake-ssh-agent',
                             os.path.abspath('fake-ssh-agent'))


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


def read_ovf(ovf_path):
    return u"""<?xml version="1.0" encoding="UTF-8"?>
<Envelope xmlns="http://schemas.dmtf.org/ovf/envelope/1"
          xmlns:ovf="http://schemas.dmtf.org/ovf/envelope/1"
          xmlns:rasd="http://schemas.dmtf.org/wbem/wscim/1/cim-schema/2/CIM_Re\
sourceAllocationSettingData">
  <References>
    <File ovf:href="First-disk1.vmdk" ovf:id="file1" ovf:size="349405696"/>
    <File ovf:href="First-disk2.vmdk" ovf:id="file2" ovf:size="349405696"/>
  </References>
  <DiskSection>
    <Disk ovf:capacity="32" ovf:capacityAllocationUnits="byte * 2^30"
        ovf:fileRef="file1"/>
    <Disk ovf:capacity="32" ovf:capacityAllocationUnits="byte * 2^30"
        ovf:populatedSize="698811392" ovf:fileRef="file2"/>
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

        with io.open(ovfpath, 'w') as ovffile:
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

        self._vms_with_snapshot = [MockVirDomain(*spec)for spec in
                                   VM_SPECS]
        self._vms_with_snapshot[4].setCurrentSnapshot(True)

    def tearDown(self):
        v2v._jobs.clear()

    def testGetExternalVMs(self):
        def _connect(uri, username, passwd):
            return MockVirConnect(vms=self._vms_with_snapshot)

        with MonkeyPatchScope([(libvirtconnection, 'open_connection',
                                _connect)]):
            vms = v2v.get_external_vms('esx://mydomain', 'user',
                                       HiddenValue('password'),
                                       None)['vmList']

        # Make sure that VM nr. 4 is now in the returned list
        # (the one with snapshot, see setUp())
        assert len(vms) == len(VM_SPECS)
        assert self._vms_with_snapshot[4].ID not in \
            [vm['vmId'] for vm in vms]

        specs = list(VM_SPECS)
        for vm, spec in zip(vms, specs):
            self._assertVmMatchesSpec(vm, spec)
            self._assertVmDisksMatchSpec(vm, spec)

    def testGetExternalVMsList(self):
        def _connect(uri, username, passwd):
            return MockVirConnect(vms=self._vms_with_snapshot)

        vmIDs = [1, 3, 4]
        names = [vm.name for vm in VM_SPECS if vm.id in vmIDs]
        # Add a non-existent name to check that nothing bad happens.
        names.append('Some nonexistent name')

        with MonkeyPatchScope([(libvirtconnection, 'open_connection',
                                _connect)]):
            vms = v2v.get_external_vms('esx://mydomain', 'user',
                                       HiddenValue('password'),
                                       names)['vmList']

        assert len(vms) == len(vmIDs)
        assert self._vms_with_snapshot[4].ID not in \
            [vm['vmId'] for vm in vms]

        for vm, vmID in zip(vms, vmIDs):
            spec = VM_SPECS[vmID]
            self._assertVmMatchesSpec(vm, spec)
            self._assertVmDisksMatchSpec(vm, spec)

    def testGetExternalVMNames(self):
        def _connect(uri, username, passwd):
            return MockVirConnect(vms=self._vms)

        with MonkeyPatchScope([(libvirtconnection, 'open_connection',
                                _connect)]):
            vms = v2v.get_external_vm_names(
                'esx://mydomain', 'user',
                HiddenValue('password'))['vmNames']

        assert sorted(vms) == \
            sorted(spec.name for spec in VM_SPECS)

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
                                       HiddenValue('password'),
                                       None)['vmList']

        assert len(vms) == len(specs)

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
                                       HiddenValue('password'),
                                       None)['vmList']
            assert len(vms) == len(self._vms)

    def testLegacyGetExternalVMsFailure(self):
        def _connect(uri, username, passwd):
            mock = MockVirConnect(vms=self._vms)
            mock.listAllDomains = legacylistAllDomainsWrongRaise
            return mock

        with MonkeyPatchScope([(libvirtconnection, 'open_connection',
                                _connect)]):
            with pytest.raises(libvirt.libvirtError):
                v2v.get_external_vms('esx://mydomain', 'user',
                                     HiddenValue('password'),
                                     None)

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
                                       HiddenValue('password'),
                                       None)['vmList']
            assert sorted(vm['vmName'] for vm in vms) == \
                sorted(spec.name for spec in VM_SPECS
                       if spec.active == active)

    def testOutputParser(self):
        output = (b'[   0.0] Opening the source -i libvirt ://roo...\n'
                  b'[   1.0] Creating an overlay to protect the f...\n'
                  b'[  88.0] Copying disk 1/2 to /tmp/v2v/0000000...\n'
                  b'    (0/100%)\r'
                  b'some messages\r'
                  b'    (25/100%)\r'
                  b'more messages\n'
                  b'    (50/100%)\r'
                  b'much much more messages\r\n'
                  b'    (100/100%)\r'
                  b'[ 180.0] Copying disk 2/2 to /tmp/v2v/100000-...\n'
                  b'    (0/100%)\r'
                  b'    (50/100%)\r'
                  b'    (100/100%)\r'
                  b'[ 256.0] Creating output metadata'
                  b'[ 256.0] Finishing off')

        parser = v2v.OutputParser()
        events = list(parser.parse(io.BytesIO(output)))
        assert events == [
            (v2v.ImportProgress(1, 2, 'Copying disk 1/2')),
            (v2v.DiskProgress(0)),
            (v2v.DiskProgress(25)),
            (v2v.DiskProgress(50)),
            (v2v.DiskProgress(100)),
            (v2v.ImportProgress(2, 2, 'Copying disk 2/2')),
            (v2v.DiskProgress(0)),
            (v2v.DiskProgress(50)),
            (v2v.DiskProgress(100))]

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
                                       HiddenValue('password'),
                                       None)['vmList']
        assert len(vms) == 0

    @permutations([
        # exc
        [v2v.V2VError],
        [v2v.ClientError],
    ])
    def testGetConvertedVMErrorFlow(self, exc):
        def _raise_error(*args, **kwargs):
            raise exc()

        # we monkeypatch the very first utility function called
        with MonkeyPatchScope([(v2v, '_get_job', _raise_error)]):
            # we use uuid to fill the API contract, but it is unused
            res = v2v.get_converted_vm(str(uuid.uuid4()))
        assert response.is_error(res)

    def _assertVmDisksMatchSpec(self, vm, spec):
        disk = vm['disks'][0]
        if spec.has_disk_volume:
            assert disk['dev'] == 'sda'
            assert disk['alias'] == \
                '[datastore1] RHEL/RHEL_%s.vmdk' % spec.name
        else:
            assert disk['dev'] == 'sdb'
            assert disk['alias'] == BLOCK_DEV_PATH
        assert 'capacity' in disk
        assert 'allocation' in disk

    def _assertVmMatchesSpec(self, vm, spec):
        assert vm['vmId'] == spec.uuid
        assert vm['memSize'] == 2048
        assert vm['smp'] == 1
        assert len(vm['disks']) == \
            int(spec.has_disk_volume) + int(spec.has_disk_block)
        assert len(vm['networks']) == 1
        assert vm['has_snapshots'] == spec.has_snapshots

        network = vm['networks'][0]
        assert network['type'] == 'bridge'
        assert network['macAddr'] == _mac_from_uuid(spec.uuid)
        assert network['bridge'] == 'VM Network'

    def testSuccessfulVMWareImport(self):
        self._commonConvertExternalVM(self.vpx_url)

    @MonkeyPatch(v2v, '_SSH_ADD', FAKE_SSH_ADD)
    @MonkeyPatch(v2v, '_SSH_AGENT', FAKE_SSH_AGENT)
    def testSuccessfulXenImport(self):
        self._commonConvertExternalVM(self.xen_url)

    def testBlockDevice(self):
        def _connect(uri, username, passwd):
            return MockVirConnect(vms=self._vms)

        with MonkeyPatchScope([(libvirtconnection, 'open_connection',
                                _connect)]):
            vms = v2v.get_external_vms(self.xen_url, 'user',
                                       HiddenValue('password'),
                                       None)['vmList']

        assert len(vms) == len(VM_SPECS)
        assert BLOCK_DEV_PATH == vms[4]['disks'][0]['alias']
        assert BLOCK_DEV_PATH == vms[5]['disks'][1]['alias']

    def testXenBlockDevice(self):
        def _connect(uri, username, passwd):
            conn = MockVirConnect(vms=self._vms)
            conn.setType('Xen')
            return conn

        with MonkeyPatchScope([(libvirtconnection, 'open_connection',
                                _connect)]):
            vms = v2v.get_external_vms(self.xen_url, 'user',
                                       HiddenValue('password'),
                                       None)['vmList']

        # Import of VMs with block devices is not supported for Xen source
        # so the VMs RHEL_4 and RHEL_5 should not be in the list.
        assert len(vms) == len(VM_SPECS) - 2
        vm_names = [vm['vmName'] for vm in vms]
        assert 'RHEL_4' not in vm_names
        assert 'RHEL_5' not in vm_names

    @MonkeyPatch(v2v, '_VIRT_V2V', FAKE_VIRT_V2V)
    @MonkeyPatch(v2v, '_LOG_DIR', None)
    def testSuccessfulImportOVA(self):
        with temporary_ovf_dir() as ovapath, \
                namedTemporaryDir() as v2v._LOG_DIR:
            v2v.convert_ova(ovapath, self.vminfo, self.job_id, FakeIRS())
            job = v2v._jobs[self.job_id]
            job.wait()

            assert job.status == v2v.STATUS.DONE

    def testV2VOutput(self):
        cmd = [FAKE_VIRT_V2V.cmd,
               '-v',
               '-x',
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

        rc, output, error = exec_cmd(cmd)
        assert rc == 0

        with io.open('fake-virt-v2v.out', 'rb') as f:
            assert output == f.read()

        with io.open('fake-virt-v2v.err', 'rb') as f:
            assert error == f.read()

    @MonkeyPatch(v2v, '_VIRT_V2V', FAKE_VIRT_V2V)
    @MonkeyPatch(v2v, '_V2V_DIR', None)
    @MonkeyPatch(v2v, '_LOG_DIR', None)
    def _commonConvertExternalVM(self, url):
        with namedTemporaryDir() as v2v._V2V_DIR, \
                namedTemporaryDir() as v2v._LOG_DIR:
            v2v.convert_external_vm(url,
                                    'root',
                                    HiddenValue('mypassword'),
                                    self.vminfo,
                                    self.job_id,
                                    FakeIRS())
            job = v2v._jobs[self.job_id]
            job.wait()

            assert job.status == v2v.STATUS.DONE

    def testSimpleExecCmd(self):
        p = v2v._simple_exec_cmd(['cat'],
                                 stdin=subprocess.PIPE,
                                 stdout=subprocess.PIPE)
        msg = "test\ntest"
        p.stdin.write(msg.encode())
        p.stdin.close()
        p.wait()
        out = p.stdout.read()
        assert out == msg.encode()

        p = v2v._simple_exec_cmd(['/bin/sh', '-c', 'echo -en "%s" >&2' % msg],
                                 stdout=subprocess.PIPE,
                                 stderr=subprocess.STDOUT)
        p.wait()
        out = p.stdout.read()
        assert out == msg.encode()

    @MonkeyPatch(v2v, '_VIRT_V2V', FAKE_VIRT_V2V)
    def testV2VCapabilities(self):
        cmd = v2v.V2VCommand({}, None, None)
        assert 'virt-v2v' in cmd._v2v_caps
        assert 'input:libvirt' in cmd._v2v_caps
        assert 'output:vdsm' in cmd._v2v_caps

    @MonkeyPatch(v2v, '_VIRT_V2V', FAKE_VIRT_V2V)
    def testQcow2Compat(self):
        # Make sure we raise on invalid compat version
        with pytest.raises(ValueError):
            cmd = v2v.V2VCommand({'qcow2_compat': 'foobar'}, None, None)

        # Make sure vdsm-compat capability is supported
        cmd = v2v.V2VCommand({}, None, None)
        assert 'vdsm-compat-option' in cmd._v2v_caps

        # Look for the command line argument
        cmd = v2v.V2VCommand({'qcow2_compat': '1.1'}, None, None)
        assert '--vdsm-compat' in cmd._base_command
        i = cmd._base_command.index('--vdsm-compat')
        assert '1.1' == cmd._base_command[i + 1]

    def test_v2v_error(self):

        class FakeProc(object):
            def __init__(self):
                self.returncode = 1
                self.stdout = io.FileIO('/dev/null')

            def kill(self):
                return

            def wait(self):
                return

        class FakeV2VCommand(object):
            @contextmanager
            def execute(self):
                yield FakeProc()

        job_id = '00000000-0000-0000-0000-000000000000'
        job = v2v.ImportVm(job_id, FakeV2VCommand())
        v2v._add_job(job_id, job)
        job.start()
        job.wait()
        status = v2v.get_jobs_status()
        assert status[job_id] == {
            'status': v2v.STATUS.FAILED,
            'description': "Job '%s' process failed exit-code: 1" % job_id,
            'progress': 0,
        }


SHORT_SLEEP = 0.3


@expandPermutations
class PipelineProcTests(TestCaseBase):

    PROC_WAIT_TIMEOUT = 30

    def testRun(self):
        msg = 'foo\nbar'
        p1 = v2v._simple_exec_cmd(['echo', '-n', msg],
                                  stdout=subprocess.PIPE)
        with terminating(p1):
            p2 = v2v._simple_exec_cmd(['cat'],
                                      stdin=p1.stdout,
                                      stdout=subprocess.PIPE)
            with terminating(p2):
                p = v2v.PipelineProc(p1, p2)
                assert p.pids == [p1.pid, p2.pid]

                ret = p.wait(self.PROC_WAIT_TIMEOUT)
                assert ret is True

                out = p.stdout.read()
                assert out == msg.encode()

    @permutations([
        # (cmd1, cmd2, returncode)
        ['false', 'true', 1],
        ['true', 'false', 1],
        ['true', 'true', 0],
    ])
    def testReturncode(self, cmd1, cmd2, returncode):
        p1 = v2v._simple_exec_cmd([cmd1],
                                  stdout=subprocess.PIPE)
        with terminating(p1):
            p2 = v2v._simple_exec_cmd([cmd2],
                                      stdin=p1.stdout,
                                      stdout=subprocess.PIPE)
            with terminating(p2):
                p = v2v.PipelineProc(p1, p2)
                p.wait(self.PROC_WAIT_TIMEOUT)
                assert p.returncode == returncode

    @permutations([
        # (cmd1, cmd2)
        [['sleep', str(3 * SHORT_SLEEP)], ['sleep', str(SHORT_SLEEP)]],
        [['sleep', str(SHORT_SLEEP)], ['sleep', str(3 * SHORT_SLEEP)]],
        [['sleep', str(3 * SHORT_SLEEP)], ['sleep', str(3 * SHORT_SLEEP)]],
    ])
    def testWait(self, cmd1, cmd2):
        p1 = v2v._simple_exec_cmd(cmd1,
                                  stdout=subprocess.PIPE)
        with terminating(p1):
            p2 = v2v._simple_exec_cmd(cmd2,
                                      stdin=p1.stdout,
                                      stdout=subprocess.PIPE)
            with terminating(p2):
                p = v2v.PipelineProc(p1, p2)
                ret = p.wait(2 * SHORT_SLEEP)
                p.kill()
                assert ret is False

    def test_wait_on_two_processes_that_finished(self):
        cmd = ['sleep', str(SHORT_SLEEP)]
        p1 = v2v._simple_exec_cmd(cmd, stdout=subprocess.PIPE)
        with terminating(p1):
            p2 = v2v._simple_exec_cmd(
                cmd, stdin=p1.stdout, stdout=subprocess.PIPE)
            with terminating(p2):
                # Wait for the processes to finish.
                time.sleep(2 * SHORT_SLEEP)
                p = v2v.PipelineProc(p1, p2)
                ret = p.wait(2 * SHORT_SLEEP)
                p.kill()
                assert ret is True

    def test_wait_on_two_processes_that_finish_before_timeout(self):
        cmd1 = ['sleep', str(SHORT_SLEEP)]
        cmd2 = ['sleep', str(1.5 * SHORT_SLEEP)]
        p1 = v2v._simple_exec_cmd(cmd1, stdout=subprocess.PIPE)
        with terminating(p1):
            p2 = v2v._simple_exec_cmd(
                cmd2, stdin=p1.stdout, stdout=subprocess.PIPE)
            with terminating(p2):
                p = v2v.PipelineProc(p1, p2)
                # Processes finish at different times but before the timeout.
                ret = p.wait(3 * SHORT_SLEEP)
                p.kill()
                assert ret is True


class MockVirConnectTests(TestCaseBase):
    def setUp(self):
        self._vms = [MockVirDomain(*spec) for spec in VM_SPECS]
        self._mock = MockVirConnect(vms=self._vms)

    def test_list_all_domains(self):
        vms = self._mock.listAllDomains()
        assert len(vms) == len(self._vms)

    def test_list_defined_domains(self):
        vms = self._mock.listDefinedDomains()
        assert sorted(vms) == \
            sorted(spec.name for spec in VM_SPECS if not spec.active)

    def test_list_domains_id(self):
        vms = self._mock.listDomainsID()
        assert len(vms) == 2

    def test_lookup_by_name(self):
        vm = self._mock.lookupByName('RHEL_0')
        assert 'RHEL_0' == vm.name()

    def test_lookup_by_name_failed(self):
        with pytest.raises(libvirt.libvirtError):
            self._mock.lookupByName('fakename')

    def test_lookup_by_id(self):
        vm = self._mock.lookupByID(0)
        assert 0 == vm.ID()

    def test_lookup_by_id_failed(self):
        with pytest.raises(libvirt.libvirtError):
            self._mock.lookupByID(99)


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

            with io.open(ovfpath, 'w') as ovffile:
                ovffile.write(ovf)
            yield base, ovfpath, ovapath

    def check(self, vm):
        assert vm['vmName'] == 'First'
        assert vm['memSize'] == 2048
        assert vm['smp'] == 1

        disk = vm['disks'][0]
        assert disk['allocation'] == '349405696'
        assert disk['capacity'] == '34359738368'
        assert disk['type'] == 'disk'
        assert disk['alias'] == 'First-disk1.vmdk'

        network = vm['networks'][0]
        assert network['bridge'] == 'VM Network'
        assert network['model'] == 'E1000'
        assert network['type'] == 'bridge'
        assert network['dev'] == 'Ethernet 1'


class UtilsTests(TestCaseBase):
    def test_units_parser(self):
        assert v2v._parse_allocation_units("byte") == 1
        assert v2v._parse_allocation_units("byte * 10") == 10
        assert v2v._parse_allocation_units("byte * +10") == 10
        assert v2v._parse_allocation_units("byte * 2 ^ 1") == 2
        assert v2v._parse_allocation_units("byte * 2 ^ 10") == 1024
        assert v2v._parse_allocation_units("byte * 2 ^ +10") == 1024
        assert v2v._parse_allocation_units("byte * 10 * 2^3") == 80
        assert v2v._parse_allocation_units("byte*10*2^3") == 80

        # We don't support other units!
        with pytest.raises(v2v.V2VError):
            v2v._parse_allocation_units("volt")
