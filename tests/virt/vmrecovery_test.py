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
from __future__ import division

import threading

from vdsm.common import cpuarch
from vdsm.common import libvirtconnection
from vdsm.common import response
from vdsm.virt import recovery
from vdsm.virt import vmstatus
from vdsm import constants
from vdsm import containersconnection


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
