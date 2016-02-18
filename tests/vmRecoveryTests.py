#
# Copyright 2016 Red Hat, Inc.
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

import os

from virt import recovery
from vdsm.compat import pickle
from vdsm import constants
from vdsm import cpuarch


from monkeypatch import MonkeyPatchScope
from testlib import VdsmTestCase as TestCaseBase
from testlib import namedTemporaryDir
from testlib import permutations, expandPermutations
from vmTestsData import CONF_TO_DOMXML_X86_64
from vmTestsData import CONF_TO_DOMXML_PPC64
from vmTestsData import CONF_TO_DOMXML_NO_VDSM
import vmfakelib as fake


class RecoveryFileTests(TestCaseBase):

    def test_save(self):

        with fake.VM() as testvm, namedTemporaryDir() as tmpdir:
            with MonkeyPatchScope([(constants, 'P_VDSM_RUN', tmpdir + '/')]):
                rec = recovery.File(testvm.id)
                rec.save(testvm)

                with open(os.path.join(tmpdir, rec.name), 'rb') as f:
                    self.assertTrue(pickle.load(f))

    def test_save_after_cleanup(self):

        with fake.VM() as testvm, namedTemporaryDir() as tmpdir:
            with MonkeyPatchScope([(constants, 'P_VDSM_RUN', tmpdir + '/')]):
                rec = recovery.File(testvm.id)
                rec.save(testvm)

                rec.cleanup()
                self.assertEqual(os.listdir(tmpdir), [])

                rec.save(testvm)  # must silently fail
                self.assertEqual(os.listdir(tmpdir), [])

    def test_load(self):

        with fake.VM() as testvm, namedTemporaryDir() as tmpdir:
            with MonkeyPatchScope([(constants, 'P_VDSM_RUN', tmpdir + '/')]):
                stored = recovery.File(testvm.id)
                stored.save(testvm)

                loaded = recovery.File(testvm.id)
                fakecif = fake.ClientIF()
                loaded.load(fakecif)

                self.assertVmStatus(testvm,
                                    fakecif.vmRequests[testvm.id][0])

    def test_cleanup(self):

        with fake.VM() as testvm, namedTemporaryDir() as tmpdir:
            with MonkeyPatchScope([(constants, 'P_VDSM_RUN', tmpdir + '/')]):
                stored = recovery.File(testvm.id)
                stored.save(testvm)

                self.assertEqual(len(os.listdir(tmpdir)), 1)
                stored.cleanup()
                self.assertEqual(len(os.listdir(tmpdir)), 0)

    def test_name(self):
        with fake.VM() as testvm, namedTemporaryDir() as tmpDir:
            with MonkeyPatchScope([(constants, 'P_VDSM_RUN', tmpDir + '/')]):
                state = recovery.File(testvm.id)
                self.assertIn(testvm.id, state.name)
                self.assertTrue(state.name.endswith(recovery.File.EXTENSION))

    def test_vmid(self):
        with fake.VM() as testvm, namedTemporaryDir() as tmpDir:
            with MonkeyPatchScope([(constants, 'P_VDSM_RUN', tmpDir + '/')]):
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


@expandPermutations
class RecoveryFunctionsTests(TestCaseBase):

    _CONFS = {
        cpuarch.X86_64: CONF_TO_DOMXML_X86_64,
        cpuarch.PPC64: CONF_TO_DOMXML_PPC64,
        'novdsm': CONF_TO_DOMXML_NO_VDSM,
    }

    def _buildAllDomains(self, arch):
        for conf, _ in self._CONFS[arch]:
            with fake.VM(conf, arch=arch) as v:
                domXml = v._buildDomainXML()
                yield fake.Domain(domXml, vmId=v.id), domXml

    def _getAllDomains(self, arch):
        for conf, rawXml in self._CONFS[arch]:
            domXml = rawXml % conf
            yield fake.Domain(domXml, vmId=conf['vmId']), domXml

    def _getAllDomainIds(self, arch):
        return [conf['vmId'] for conf, _ in self._CONFS[arch]]

    # TODO: rewrite once recovery.py refactoring is completed
    @permutations([[cpuarch.X86_64], [cpuarch.PPC64]])
    def testGetVDSMDomains(self, arch):
        with MonkeyPatchScope([(recovery, '_list_domains',
                                lambda: self._buildAllDomains(arch)),
                               (cpuarch, 'effective', lambda: arch)]):
            self.assertEqual([v.UUIDString()
                             for v in recovery._get_vdsm_domains()],
                             self._getAllDomainIds(arch))

    # TODO: rewrite once recovery.py refactoring is completed
    # VDSM (of course) builds correct config, so we need static examples
    # of incorrect/not-compliant data
    def testSkipNotVDSMDomains(self):
        with MonkeyPatchScope([(recovery, '_list_domains',
                                lambda: self._getAllDomains('novdsm'))]):
            self.assertFalse(recovery._get_vdsm_domains())
