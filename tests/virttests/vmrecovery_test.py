#
# Copyright 2016-2017 Red Hat, Inc.
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
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA  02110-1301 USA
#
# Refer to the README and COPYING files for full details of the license
#
from __future__ import absolute_import

import contextlib
import os
import threading

from vdsm.common import libvirtconnection
from vdsm.common import response
from vdsm.common.compat import pickle
from vdsm.virt import recovery
from vdsm.virt import vmstatus
from vdsm import constants
from vdsm import containersconnection
from vdsm import cpuarch


from monkeypatch import MonkeyPatchScope
from testlib import VdsmTestCase as TestCaseBase
from testlib import namedTemporaryDir
from testlib import permutations, expandPermutations
from vmTestsData import CONF_TO_DOMXML_X86_64
from vmTestsData import CONF_TO_DOMXML_PPC64
from vmTestsData import CONF_TO_DOMXML_NO_VDSM
import vmfakelib as fake


def _createVm_fails(*args, **kwargs):
    return response.error('noVM')


def _createVm_raises(*args, **kwargs):
    raise RuntimeError("fake error")


@expandPermutations
class RecoveryFileTests(TestCaseBase):

    def test_save(self):

        with self.setup_env() as (testvm, tmpdir):
            rec = recovery.File(testvm.id)
            rec.save(testvm)

            with open(os.path.join(tmpdir, rec.name), 'rb') as f:
                self.assertTrue(pickle.load(f))

    def test_save_after_cleanup(self):

        with self.setup_env() as (testvm, tmpdir):
            rec = recovery.File(testvm.id)
            rec.save(testvm)

            rec.cleanup()
            self.assertEqual(os.listdir(tmpdir), [])

            rec.save(testvm)  # must silently fail
            self.assertEqual(os.listdir(tmpdir), [])

    def test_load(self):

        with self.setup_env() as (testvm, tmpdir):
            stored = recovery.File(testvm.id)
            stored.save(testvm)

            loaded = recovery.File(testvm.id)
            fakecif = fake.ClientIF()
            res = loaded.load(fakecif)
            self.assertTrue(res)

            self.assertVmStatus(testvm, fakecif.vmRequests[testvm.id][0])

    @permutations([[_createVm_raises], [_createVm_fails]])
    def test_load_with_createVm_error(self, createVm):

        with self.setup_env() as (testvm, tmpdir):
            stored = recovery.File(testvm.id)
            stored.save(testvm)

            loaded = recovery.File(testvm.id)
            fakecif = fake.ClientIF()

            fakecif.createVm = createVm
            res = loaded.load(fakecif)

            self.assertFalse(res)
            self.assertEqual(fakecif.vmContainer, {})
            self.assertEqual(fakecif.vmRequests, {})

    def test_cleanup(self):

        with self.setup_env() as (testvm, tmpdir):
            stored = recovery.File(testvm.id)
            stored.save(testvm)

            self.assertEqual(len(os.listdir(tmpdir)), 1)
            stored.cleanup()
            self.assertEqual(len(os.listdir(tmpdir)), 0)

    def test_name(self):

        with self.setup_env() as (testvm, tmpdir):
            state = recovery.File(testvm.id)
            self.assertIn(testvm.id, state.name)
            self.assertTrue(state.name.endswith(recovery.File.EXTENSION))

    def test_vmid(self):

        with self.setup_env() as (testvm, tmpdir):
            state = recovery.File(testvm.id)
            self.assertEqual(testvm.id, state.vmid)

    def assertVmStatus(self, testvm, params):
        status = testvm.status()
        # reloaded status must be a superset of Vm' status()
        # return value.
        for key in status:
            if key == 'statusTime':
                # monotically increasing, it is fine if changes.
                continue
            expected, recovered = status[key], params[key]
            msg = 'item %s status=%s params=%s' % (key, expected, recovered)
            self.assertEqual(recovered, expected, msg)

    @contextlib.contextmanager
    def setup_env(self):
        with fake.VM() as testvm, namedTemporaryDir() as tmpdir:
            with MonkeyPatchScope([(constants, 'P_VDSM_RUN', tmpdir)]):
                yield testvm, tmpdir


class FakeConnection(object):

    CONFS = {
        cpuarch.X86_64: CONF_TO_DOMXML_X86_64,
        cpuarch.PPC64: CONF_TO_DOMXML_PPC64,
        'novdsm': CONF_TO_DOMXML_NO_VDSM,
    }

    def __init__(self, arch, channel_name=None):
        self.arch = arch
        self.channel_name = channel_name

    def listAllDomains(self):
        for conf, rawXml in self.CONFS[self.arch]:
            if self.channel_name is not None:
                conf = conf.copy()
                conf['agentChannelName'] = self.channel_name
            domXml = rawXml % conf
            yield fake.Domain(domXml, vmId=conf['vmId'])


@expandPermutations
class RecoveryFunctionsTests(TestCaseBase):

    def _getAllDomainIds(self, arch):
        return [(conf['vmId'], arch == 'novdsm',)
                for conf, _ in FakeConnection.CONFS[arch]]

    # TODO: rewrite once recovery.py refactoring is completed
    @permutations([[cpuarch.X86_64], [cpuarch.PPC64], ['novdsm']])
    def testGetVDSMDomains(self, arch):
        with MonkeyPatchScope([(libvirtconnection, 'get',
                                lambda: FakeConnection(arch)),
                               (cpuarch, 'effective', lambda: arch)]):
            self.assertEqual([(v.UUIDString(), external,)
                              for v, xml, external
                              in recovery._list_domains()],
                             self._getAllDomainIds(arch))

    @permutations([[cpuarch.X86_64], [cpuarch.PPC64]])
    def testGetVDSMDomainsWithChannel(self, arch):
        with MonkeyPatchScope([(libvirtconnection, 'get',
                                lambda: FakeConnection(arch, 'chan')),
                               (cpuarch, 'effective', lambda: arch)]):
            self.assertEqual([(v.UUIDString(), external,)
                              for v, xml, external
                              in recovery._list_domains()],
                             self._getAllDomainIds(arch))

    @permutations([[cpuarch.X86_64], [cpuarch.PPC64]])
    def testGetVDSMDomainsWithoutGuestfs(self, arch):
        connect = lambda: FakeConnection(arch, 'org.libguestfs.channel.0')
        with MonkeyPatchScope([(libvirtconnection, 'get', connect),
                               (cpuarch, 'effective', lambda: arch)]):
            self.assertEqual(recovery._list_domains(), [])


class RecoveryAllVmsTests(TestCaseBase):
    # more tests handling all the edge cases will come
    def test_without_any_vms(self):

        with namedTemporaryDir() as tmpdir:
            with MonkeyPatchScope([
                (constants, 'P_VDSM_RUN', tmpdir),
                (recovery, '_list_domains', lambda: []),
                (containersconnection, 'recovery', lambda: []),
            ]):
                fakecif = fake.ClientIF()
                recovery.all_domains(fakecif)
                self.assertEqual(fakecif.vmContainer, {})


class VmRecoveryTests(TestCaseBase):

    def test_exception(self):

        done = threading.Event()

        def fail():
            raise RuntimeError('fake error')

        with fake.VM(runCpu=True, recover=True) as testvm:

            def _send_status_event(**kwargs):
                vm_status = testvm.lastStatus
                if vm_status == vmstatus.UP:
                    done.set()

            def _updateDomainDescriptor(*args):
                pass

            testvm.send_status_event = _send_status_event
            testvm._updateDomainDescriptor = _updateDomainDescriptor
            testvm._run = fail
            testvm.run()

            self.assertTrue(done.wait(1))
