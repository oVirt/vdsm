#
# Copyright 2014 Hewlett-Packard Development Company, L.P.
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
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA
# 02110-1301  USA
#
# Refer to the README and COPYING files for full details of the license
#

import xml.etree.cElementTree as ET
import os.path

from testlib import VdsmTestCase as TestCaseBase
from monkeypatch import MonkeyPatch
from monkeypatch import MonkeyPatchScope

from vdsm import numa

import vmfakelib as fake


_VM_RUN_FILE_CONTENT = """
    <domstatus state='running' reason='booted' pid='12262'>
      <monitor path='/var/lib/libvirt/qemu/testvm.monitor'
               json='1' type='unix'/>
        <vcpus>
          <vcpu pid='12266'/>
          <vcpu pid='12267'/>
          <vcpu pid='12268'/>
          <vcpu pid='12269'/>
        </vcpus>
    </domstatus>"""


class TestNumaUtils(TestCaseBase):

    @MonkeyPatch(ET, 'parse',
                 lambda x: ET.fromstring(_VM_RUN_FILE_CONTENT))
    @MonkeyPatch(os.path, 'getmtime',
                 lambda x: 0)
    def testVcpuPid(self):
        vcpuPids = numa.getVcpuPid('testvm')
        expectedVcpuPids = {0: '12266',
                            1: '12267',
                            2: '12268',
                            3: '12269'}
        self.assertEqual(vcpuPids, expectedVcpuPids)

    @MonkeyPatch(numa, 'supervdsm', fake.SuperVdsm())
    @MonkeyPatch(numa,
                 'topology',
                 lambda: {'0': {'cpus': [0, 1, 2, 3],
                                'totalMemory': '49141'},
                          '1': {'cpus': [4, 5, 6, 7],
                                'totalMemory': '49141'}})
    def testVmNumaNodeRuntimeInfo(self):
        VM_PARAMS = {'guestNumaNodes': [{'cpus': '0,1',
                                         'memory': '1024',
                                         'nodeIndex': 0},
                                        {'cpus': '2,3',
                                         'memory': '1024',
                                         'nodeIndex': 1}]}
        with fake.VM(VM_PARAMS) as testvm:
            expectedResult = {'0': [0, 1], '1': [0, 1]}
            self.assertTrue(testvm.hasGuestNumaNode)
            sample = [(0, 1, 19590000000, 1),
                      (1, 1, 10710000000, 1),
                      (2, 1, 19590000000, 0),
                      (3, 1, 19590000000, 2),
                      # CPU not assigned by Engine, should be ignored:
                      (4, 1, 10710000000, 0)]
            with MonkeyPatchScope([(numa, "_get_vcpu_positioning",
                                  lambda vm: sample)]):
                vm_numa_info = numa.getVmNumaNodeRuntimeInfo(testvm)
                self.assertEqual(expectedResult, vm_numa_info)


class NumaUtilsHelperTests(TestCaseBase):
    """
    Good practice dictates not to test non-public APIs.
    Here, we add this tests for the sake of practicality.

    TODO: overhaul the numa API to make these tests
    redundant.
    """

    def test_get_mapping_vcpu_to_pcpu(self):
        # stolen from real libvirt
        vcpu_output = (
            [(0, 1, 20050000000, 0), (1, 1, 11300000000, 1)],
            [(True, True, True, True), (True, True, True, True)]
        )
        mapping = {0: 0, 1: 1}

        with fake.VM() as testvm:
            testvm._dom = fake.Domain()
            testvm._dom.vcpus = lambda: vcpu_output

            self.assertEqual(
                numa._get_mapping_vcpu_to_pcpu(
                    numa._get_vcpu_positioning(testvm)),
                mapping)
