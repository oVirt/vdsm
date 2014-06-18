#
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
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA  02110-1301 USA
#
# Refer to the README and COPYING files for full details of the license
#

from contextlib import contextmanager
import os
import os.path

from virt import vmexitreason
from vdsm import define
from testrunner import VdsmTestCase as TestCaseBase
from vdsm import utils
from rpc import vdsmapi

from vmTests import FakeVM


class TestSchemaCompliancyBase(TestCaseBase):
    @utils.memoized
    def _getAPI(self):
        testPath = os.path.realpath(__file__)
        dirName = os.path.split(testPath)[0]
        apiPath = os.path.join(
            dirName, '..', 'vdsm', 'rpc', 'vdsmapi-schema.json')
        return vdsmapi.get_api(apiPath)

    def assertVmStatsSchemaCompliancy(self, schema, stats):
        api = self._getAPI()
        ref = api['types'][schema]['data']
        for apiItem, apiType in ref.items():
            if apiItem[0] == '*':
                # optional, may be absent and it is fine
                self.assertTrue(stats.get(apiItem[1:], True))
            else:
                # mandatory
                self.assertIn(apiItem, stats)
        # TODO: type checking


@contextmanager
def ensureVmStats(vm):
    vm._initVmStats()
    try:
        yield vm
    finally:
        vm._vmStats.stop()


class TestVmStats(TestSchemaCompliancyBase):
    def testDownStats(self):
        with FakeVM() as fake:
            fake.setDownStatus(define.ERROR, vmexitreason.GENERIC_ERROR)
            self.assertVmStatsSchemaCompliancy('ExitedVmStats',
                                               fake.getStats())

    def testRunningStats(self):
        vmParams = {
            'displayPort': -1, 'displaySecurePort': -1, 'display': 'qxl',
            'displayIp': '127.0.0.1', 'vmType': 'kvm', 'devices': {},
            'memSize': 1024,
            # HACKs
            'pauseCode': 'NOERR'}
        with FakeVM(vmParams) as fake:
            with ensureVmStats(fake):
                self.assertVmStatsSchemaCompliancy('RunningVmStats',
                                                   fake.getStats())
