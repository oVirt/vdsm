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
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA  02110-1301 USA
#
# Refer to the README and COPYING files for full details of the license
#
from __future__ import absolute_import

from vdsm.virt import vmexitreason

from virt import vm
from vdsm.common import define
from testlib import VdsmTestCase as TestCaseBase
from vdsm import utils
from vdsm.api import vdsmapi

import API
from clientIF import clientIF

from testValidation import brokentest
from monkeypatch import MonkeyPatch, MonkeyPatchScope
import vmfakelib as fake


class TestSchemaCompliancyBase(TestCaseBase):
    @utils.memoized
    def _getAPI(self):
        paths = [vdsmapi.find_schema()]
        return vdsmapi.Schema(paths, True)

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

    @brokentest
    def testRunningStats(self):
        with fake.VM(_VM_PARAMS) as testvm:
            self.assertVmStatsSchemaCompliancy('RunningVmStats',
                                               testvm.getStats())


class TestApiAllVm(TestSchemaCompliancyBase):

    @brokentest
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
