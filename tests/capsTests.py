#
# Copyright 2012 Red Hat, Inc.
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

import os
import platform
from testlib import VdsmTestCase as TestCaseBase
from monkeypatch import MonkeyPatch

import caps
from vdsm import utils


def _getTestData(testFileName):
    testPath = os.path.realpath(__file__)
    dirName = os.path.dirname(testPath)
    path = os.path.join(dirName, testFileName)
    with open(path) as src:
        return src.read()


def _getCapsNumaDistanceTestData(testFileName):
    return (0, _getTestData(testFileName).splitlines(False), [])


class TestCaps(TestCaseBase):
    def _readCaps(self, fileName):
        testPath = os.path.realpath(__file__)
        dirName = os.path.split(testPath)[0]
        path = os.path.join(dirName, fileName)
        with open(path) as f:
            return f.read()

    @MonkeyPatch(platform, 'machine', lambda: caps.Architecture.X86_64)
    def testCpuInfo(self):
        testPath = os.path.realpath(__file__)
        dirName = os.path.split(testPath)[0]
        path = os.path.join(dirName, "cpu_info.out")
        c = caps.CpuInfo(path)
        self.assertEqual(set(c.flags()), set("""fpu vme de pse tsc msr pae
                                                mce cx8 apic mtrr pge mca
                                                cmov pat pse36 clflush dts
                                                acpi mmx fxsr sse sse2 ss ht
                                                tm pbe syscall nx pdpe1gb
                                                rdtscp lm constant_tsc
                                                arch_perfmon pebs bts
                                                rep_good xtopology
                                                nonstop_tsc aperfmperf pni
                                                pclmulqdq dtes64 monitor
                                                ds_cpl vmx smx est tm2 ssse3
                                                cx16 xtpr pdcm dca sse4_1
                                                sse4_2 popcnt aes lahf_lm
                                                arat epb dts tpr_shadow vnmi
                                                flexpriority ept
                                                vpid""".split()))

        self.assertEqual(c.mhz(), '2533.402')
        self.assertEqual(c.model(),
                         'Intel(R) Xeon(R) CPU           E5649  @ 2.53GHz')

    @MonkeyPatch(platform, 'machine', lambda: caps.Architecture.PPC64)
    def testCpuTopologyPPC64(self):
        testPath = os.path.realpath(__file__)
        dirName = os.path.split(testPath)[0]
        # PPC64 1 socket, 4 cores, 4 threads per core
        path = os.path.join(dirName, "caps_lscpu_ppc64_1_4_4.out")
        t = caps.CpuTopology(open(path).read())
        self.assertEqual(t.threads(), 16)
        self.assertEqual(t.cores(), 4)
        self.assertEqual(t.sockets(), 4)
        # PPC64 2 sockets, 8 cores, 8 threads per core
        path = os.path.join(dirName, "caps_lscpu_ppc64_2_4_8.out")
        t = caps.CpuTopology(open(path).read())
        self.assertEqual(t.threads(), 64)
        self.assertEqual(t.cores(), 8)
        self.assertEqual(t.sockets(), 2)

    @MonkeyPatch(platform, 'machine', lambda: caps.Architecture.X86_64)
    def testCpuTopologyX86_64(self):
        testPath = os.path.realpath(__file__)
        dirName = os.path.split(testPath)[0]
        # 2 x Intel E5649 (with Hyperthreading)
        path = os.path.join(dirName, "caps_libvirt_intel_E5649.out")
        t = caps.CpuTopology(file(path).read())
        self.assertEqual(t.threads(), 24)
        self.assertEqual(t.cores(), 12)
        self.assertEqual(t.sockets(), 2)
        # 2 x AMD 6272 (with Modules)
        path = os.path.join(dirName, "caps_libvirt_amd_6274.out")
        t = caps.CpuTopology(file(path).read())
        self.assertEqual(t.threads(), 32)
        self.assertEqual(t.cores(), 16)
        self.assertEqual(t.sockets(), 2)
        # 1 x Intel E31220 (normal Multi-core)
        path = os.path.join(dirName, "caps_libvirt_intel_E31220.out")
        t = caps.CpuTopology(file(path).read())
        self.assertEqual(t.threads(), 4)
        self.assertEqual(t.cores(), 4)
        self.assertEqual(t.sockets(), 1)

    def testEmulatedMachines(self):
        capsData = self._readCaps("caps_libvirt_amd_6274.out")
        machines = caps._getEmulatedMachines(caps.Architecture.X86_64,
                                             capsData)
        expectedMachines = ['pc-0.15', 'pc', 'pc-1.0', 'pc-0.14',
                            'pc-0.13', 'pc-0.12', 'pc-0.11',
                            'pc-0.10', 'isapc']
        self.assertEqual(machines, expectedMachines)

    def test_parseKeyVal(self):
        lines = ["x=&2", "y& = 2", " z = 2 ", " s=3=&'5", " w=", "4&"]
        expectedRes = [{'x': '&2', 'y&': '2', 'z': '2', 's': "3=&'5", 'w': ''},
                       {'x=': '2', 'y': '= 2', 's=3=': "'5", '4': ''}]
        sign = ["=", "&"]
        for res, s in zip(expectedRes, sign):
            self.assertEqual(res, caps._parseKeyVal(lines, s))

    @MonkeyPatch(caps, 'getMemoryStatsByNumaCell', lambda x: {
        'total': '49141', 'free': '46783'})
    @MonkeyPatch(caps, '_getCapsXMLStr', lambda: _getTestData(
        "caps_libvirt_amd_6274.out"))
    def testNumaTopology(self):
        # 2 x AMD 6272 (with Modules)
        t = caps.getNumaTopology()
        expectedNumaInfo = {
            '0': {'cpus': [0, 1, 2, 3, 4, 5, 6, 7], 'totalMemory': '49141'},
            '1': {'cpus': [8, 9, 10, 11, 12, 13, 14, 15],
                  'totalMemory': '49141'},
            '2': {'cpus': [16, 17, 18, 19, 20, 21, 22, 23],
                  'totalMemory': '49141'},
            '3': {'cpus': [24, 25, 26, 27, 28, 29, 30, 31],
                  'totalMemory': '49141'}}
        self.assertEqual(t, expectedNumaInfo)

    @MonkeyPatch(utils, 'readMemInfo', lambda: {
        'MemTotal': 50321208, 'MemFree': 47906488})
    def testGetUMAMemStats(self):
        t = caps.getUMAHostMemoryStats()
        expectedInfo = {'total': '49141', 'free': '46783'}
        self.assertEqual(t, expectedInfo)

    @MonkeyPatch(utils, 'execCmd', lambda x: _getCapsNumaDistanceTestData(
        "caps_numactl_4_nodes.out"))
    def testNumaNodeDistance(self):
        t = caps.getNumaNodeDistance()
        expectedDistanceInfo = {
            '0': [10, 20, 20, 20],
            '1': [20, 10, 20, 20],
            '2': [20, 20, 10, 20],
            '3': [20, 20, 20, 10]}
        self.assertEqual(t, expectedDistanceInfo)

    @MonkeyPatch(utils, 'execCmd', lambda x: (0, ['0'], []))
    def testAutoNumaBalancingInfo(self):
        t = caps.getAutoNumaBalancingInfo()
        self.assertEqual(t, 0)

    def testLiveSnapshotNoElementX86_64(self):
        '''old libvirt, backward compatibility'''
        capsData = self._readCaps("caps_libvirt_amd_6274.out")
        support = caps._getLiveSnapshotSupport(caps.Architecture.X86_64,
                                               capsData)
        self.assertTrue(support is None)

    def testLiveSnapshotX86_64(self):
        capsData = self._readCaps("caps_libvirt_intel_i73770.out")
        support = caps._getLiveSnapshotSupport(caps.Architecture.X86_64,
                                               capsData)
        self.assertEqual(support, True)

    def testLiveSnapshotDisabledX86_64(self):
        capsData = self._readCaps("caps_libvirt_intel_i73770_nosnap.out")
        support = caps._getLiveSnapshotSupport(caps.Architecture.X86_64,
                                               capsData)
        self.assertEqual(support, False)
