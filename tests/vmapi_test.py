# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import
from __future__ import division

from vdsm.virt import vmexitreason

from vdsm import API
from vdsm.clientIF import clientIF
from vdsm.common import cache
from vdsm.common import define
from testlib import VdsmTestCase as TestCaseBase
from vdsm.api import vdsmapi
from vdsm.virt import vm

from testValidation import brokentest
from monkeypatch import MonkeyPatch, MonkeyPatchScope

from virt import vmfakelib as fake


class TestSchemaCompliancyBase(TestCaseBase):
    @cache.memoized
    def _getAPI(self):
        return vdsmapi.Schema.vdsm_api(strict_mode=True)

    def assertVmStatsSchemaCompliancy(self, schema, stats):
        api = self._getAPI()
        ref = api.get_type(schema)
        for prop in ref.get('properties'):
            name = prop.get('name')
            if 'defaultvalue' in prop:
                # optional, may be absent and it is fine
                if name in stats:
                    self.assertNotEqual(stats[name], None)
            else:
                # mandatory
                self.assertIn(name, stats)
        # TODO: type checking


_VM_PARAMS = {
    'displayPort': -1, 'displaySecurePort': -1, 'display': 'qxl',
    'displayIp': '127.0.0.1', 'vmType': 'kvm', 'devices': {},
    'memSize': 1024,
    # HACKs
    'pauseCode': 'NOERR'}


class TestVmStats(TestSchemaCompliancyBase):

    @MonkeyPatch(vm.Vm, 'send_status_event', lambda x: None)
    def testDownStats(self):
        with fake.VM() as testvm:
            testvm.setDownStatus(define.ERROR, vmexitreason.GENERIC_ERROR)
            self.assertVmStatsSchemaCompliancy('ExitedVmStats',
                                               testvm.getStats())

    @brokentest('Racy test, see http://gerrit.ovirt.org/37275')
    def testRunningStats(self):
        with fake.VM(_VM_PARAMS) as testvm:
            self.assertVmStatsSchemaCompliancy('RunningVmStats',
                                               testvm.getStats())


class TestApiAllVm(TestSchemaCompliancyBase):

    @brokentest('Racy test, see http://gerrit.ovirt.org/36894')
    def testAllVmStats(self):
        with fake.VM(_VM_PARAMS) as testvm:
            with MonkeyPatchScope([(clientIF, 'getInstance',
                                    lambda _: testvm.cif)]):
                api = API.Global()

                # here is where clientIF will be used.
                response = api.getAllVmStats()

                self.assertEqual(response['status']['code'], 0)

                for stat in response['statsList']:
                    self.assertVmStatsSchemaCompliancy(
                        'RunningVmStats', stat)
