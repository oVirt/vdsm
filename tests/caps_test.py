# -*- coding: utf-8 -*-
#
# Copyright 2012-2020 Red Hat, Inc.
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

from __future__ import absolute_import
from __future__ import division

import os
import platform
import tempfile
from testlib import VdsmTestCase as TestCaseBase
from monkeypatch import MonkeyPatch

from vdsm.host import caps
from vdsm import cpuinfo
from vdsm import numa
from vdsm import machinetype
from vdsm import osinfo
from vdsm.common import cache
from vdsm.common import commands
from vdsm.common import cpuarch
from vdsm.common import libvirtconnection


def _getTestData(testFileName):
    testPath = os.path.realpath(__file__)
    dirName = os.path.dirname(testPath)
    path = os.path.join(dirName, testFileName)
    with open(path) as src:
        return src.read()


def _getCapsNumaDistanceTestData(testFileName):
    return (0, _getTestData(testFileName).splitlines(False), [])


def _getLibvirtConnStub():
    class ConnStub:
        def getCapabilities(self):
            return "<capabilities><host><cpu>" \
                   "<counter name='tsc' frequency='1234000000'/>" \
                   "</cpu></host></capabilities>"
    return ConnStub()


def _getLibvirtConnStubEmpty():
    class ConnStub:
        def getCapabilities(self):
            return "<capabilities><host><cpu>" \
                   "</cpu></host></capabilities>"
    return ConnStub()


def _getLibvirtConnStubForTscScaling(scaling):
    class ConnStub:
        def getCapabilities(self):
            return "<capabilities><host><cpu>" \
                   "<counter name='tsc' frequency='1234000000' scaling='" + \
                   scaling + \
                   "'/>" \
                   "</cpu></host></capabilities>"
    return ConnStub()


def _getLibvirtConnStubFromFile(path):
    class ConnStub:
        def getCapabilities(self):
            return _getTestData(path)
    return ConnStub()


class TestCaps(TestCaseBase):

    def tearDown(self):
        for name in dir(caps):
            obj = getattr(caps, name)
            if isinstance(obj, cache.memoized):
                obj.invalidate()

    def _readCaps(self, fileName):
        testPath = os.path.realpath(__file__)
        dirName = os.path.split(testPath)[0]
        path = os.path.join(dirName, fileName)
        with open(path) as f:
            return f.read()

    @MonkeyPatch(libvirtconnection, 'get',
                 lambda : _getLibvirtConnStubFromFile(
                     'caps_libvirt_ibm_S822L.out'))
    @MonkeyPatch(numa, 'memory_by_cell', lambda x: {
        'total': '1', 'free': '1'})
    @MonkeyPatch(platform, 'machine', lambda: cpuarch.PPC64)
    def testCpuTopologyPPC64(self):
        # PPC64 4 sockets, 5 cores, 1 threads per core
        numa.update()
        t = numa.cpu_topology()
        self.assertEqual(t.threads, 20)
        self.assertEqual(t.cores, 20)
        self.assertEqual(t.sockets, 4)

    @MonkeyPatch(libvirtconnection, 'get',
                 lambda : _getLibvirtConnStubFromFile(
                     'caps_libvirt_s390x.out'))
    @MonkeyPatch(numa, 'memory_by_cell', lambda x: {
        'total': '1', 'free': '1'})
    @MonkeyPatch(platform, 'machine', lambda: cpuarch.S390X)
    def testCpuTopologyS390X(self):
        # S390 1 socket, 4 cores, 1 threads per core
        numa.update()
        t = numa.cpu_topology()
        self.assertEqual(t.threads, 4)
        self.assertEqual(t.cores, 4)
        self.assertEqual(t.sockets, 1)

    @MonkeyPatch(libvirtconnection, 'get',
                 lambda : _getLibvirtConnStubFromFile(
                     'caps_libvirt_intel_E5649.out'))
    @MonkeyPatch(numa, 'memory_by_cell', lambda x: {
        'total': '1', 'free': '1'})
    @MonkeyPatch(platform, 'machine', lambda: cpuarch.X86_64)
    def testCpuTopologyX86_64_intel_e5649(self):
        # 2 x Intel E5649 (with Hyperthreading)
        numa.update()
        t = numa.cpu_topology()
        self.assertEqual(t.threads, 24)
        self.assertEqual(t.cores, 12)
        self.assertEqual(t.sockets, 2)

    @MonkeyPatch(libvirtconnection, 'get',
                 lambda : _getLibvirtConnStubFromFile(
                     'caps_libvirt_amd_6274.out'))
    @MonkeyPatch(numa, 'memory_by_cell', lambda x: {
        'total': '1', 'free': '1'})
    @MonkeyPatch(platform, 'machine', lambda: cpuarch.X86_64)
    def testCpuTopologyX86_64_amd_6272(self):
        # 2 x AMD 6272 (with Modules)
        numa.update()
        t = numa.cpu_topology()
        self.assertEqual(t.threads, 32)
        self.assertEqual(t.cores, 16)
        self.assertEqual(t.sockets, 2)

    @MonkeyPatch(libvirtconnection, 'get',
                 lambda : _getLibvirtConnStubFromFile(
                     'caps_libvirt_intel_E31220.out'))
    @MonkeyPatch(numa, 'memory_by_cell', lambda x: {
        'total': '1', 'free': '1'})
    @MonkeyPatch(platform, 'machine', lambda: cpuarch.X86_64)
    def testCpuTopologyX86_64_intel_e31220(self):
        # 1 x Intel E31220 (normal Multi-core)
        numa.update()
        t = numa.cpu_topology()
        self.assertEqual(t.threads, 4)
        self.assertEqual(t.cores, 4)
        self.assertEqual(t.sockets, 1)

    def testEmulatedMachines(self):
        capsData = self._readCaps("caps_libvirt_amd_6274.out")
        machines = set(machinetype.emulated_machines(cpuarch.X86_64,
                                                     capsData))
        expectedMachines = {'pc-1.0', 'pc', 'isapc', 'pc-0.12', 'pc-0.13',
                            'pc-0.10', 'pc-0.11', 'pc-0.14', 'pc-0.15'}
        self.assertEqual(machines, expectedMachines)

    def test_parseKeyVal(self):
        lines = ["x=&2", "y& = 2", " z = 2 ", " s=3=&'5", " w=", "4&",
                 u"v=1", u"temperature=”1°C”"]
        expectedRes = [{'x': '&2', 'y&': '2', 'z': '2', 's': "3=&'5", 'w': '',
                        u'v': u'1', u'temperature': u'”1°C”'},
                       {'x=': '2', 'y': '= 2', 's=3=': "'5", '4': ''}]
        sign = ["=", "&"]
        for res, s in zip(expectedRes, sign):
            self.assertEqual(res, caps._parseKeyVal(lines, s))

    def test_parse_node_version(self):
        inputs = (b'',
                  b'VERSION = 1\n',
                  b'RELEASE = 2\n',
                  b'VERSION = 1\nRELEASE = 2\n',
                  b'VERSIO = 1\nRELEASE = 2\n')
        expected_results = (('', ''),
                            ('1', ''),
                            ('', '2'),
                            ('1', '2'),
                            ('', '2'))
        for test_input, expected_result in zip(inputs, expected_results):
            with tempfile.NamedTemporaryFile() as f:
                f.write(test_input)
                f.flush()
                self.assertEqual(osinfo._parse_node_version(f.name),
                                 expected_result)

    @MonkeyPatch(numa, 'memory_by_cell', lambda x: {
        'total': '49141', 'free': '46783'})
    @MonkeyPatch(libvirtconnection, 'get',
                 lambda : _getLibvirtConnStubFromFile(
                     "caps_libvirt_amd_6274.out"))
    def testNumaTopology(self):
        # 2 x AMD 6272 (with Modules)
        numa.update()
        t = numa.topology()
        expectedNumaInfo = {
            '0': {'cpus': [0, 1, 2, 3, 4, 5, 6, 7], 'totalMemory': '49141',
                  'hugepages': {
                      4: {'totalPages': '2500'},
                      2048: {'totalPages': '100'}}},
            '1': {'cpus': [8, 9, 10, 11, 12, 13, 14, 15],
                  'totalMemory': '49141',
                  'hugepages': {
                      4: {'totalPages': '2'},
                      2048: {'totalPages': '1'}}},
            '2': {'cpus': [16, 17, 18, 19, 20, 21, 22, 23],
                  'totalMemory': '49141',
                  'hugepages': {
                      4: {'totalPages': '0'},
                      2048: {'totalPages': '0'}}},
            '3': {'cpus': [24, 25, 26, 27, 28, 29, 30, 31],
                  'totalMemory': '49141',
                  'hugepages': {
                      4: {'totalPages': '2500'},
                      2048: {'totalPages': '100'}}}}
        self.assertEqual(t, expectedNumaInfo)

    @MonkeyPatch(libvirtconnection, 'get',
                 lambda : _getLibvirtConnStubFromFile(
                     'caps_libvirt_ibm_S822L_le.out'))
    @MonkeyPatch(numa, 'memory_by_cell', lambda x: {
        'total': '1', 'free': '1'})
    def testNumaNodeDistance(self):
        numa.update()
        t = numa.distances()
        expectedDistanceInfo = {'0': [10, 20, 40, 40],
                                '1': [20, 10, 40, 40],
                                '16': [40, 40, 10, 20],
                                '17': [40, 40, 20, 10]}
        self.assertEqual(t, expectedDistanceInfo)

    @MonkeyPatch(commands, 'run', lambda x: ('0', ['0'], []))
    def testAutoNumaBalancingInfo(self):
        t = numa.autonuma_status()
        self.assertEqual(t, 0)

    def test_get_emulated_machines(self):
        capsData = self._readCaps("caps_libvirt_intel_i73770_nosnap.out")
        result = set(machinetype.emulated_machines('x86_64', capsData))
        expected = {'rhel6.3.0', 'rhel6.1.0', 'rhel6.2.0', 'pc', 'rhel5.4.0',
                    'rhel5.4.4', 'rhel6.4.0', 'rhel6.0.0', 'rhel6.5.0',
                    'rhel5.5.0'}
        self.assertEqual(expected, result)

    def test_get_emulated_machinesCanonical(self):
        capsData = self._readCaps("caps_libvirt_intel_E5606.out")
        result = set(machinetype.emulated_machines('x86_64', capsData))
        expected = {'pc-i440fx-rhel7.1.0',
                    'rhel6.3.0',
                    'pc-q35-rhel7.0.0',
                    'rhel6.1.0',
                    'rhel6.6.0',
                    'rhel6.2.0',
                    'pc',
                    'pc-q35-rhel7.1.0',
                    'q35',
                    'rhel6.4.0',
                    'rhel6.0.0',
                    'rhel6.5.0',
                    'pc-i440fx-rhel7.0.0'}
        self.assertEqual(expected, result)

    def test_get_emulated_machinesWithTwoQEMUInstalled(self):
        capsData = self._readCaps("caps_libvirt_multiqemu.out")
        result = set(machinetype.emulated_machines('x86_64', capsData))
        expected = {'pc-i440fx-rhel7.1.0',
                    'rhel6.3.0',
                    'pc-q35-rhel7.0.0',
                    'rhel6.1.0',
                    'rhel6.6.0',
                    'rhel6.2.0',
                    'pc',
                    'pc-q35-rhel7.1.0',
                    'q35',
                    'rhel6.4.0',
                    'rhel6.0.0',
                    'rhel6.5.0',
                    'pc-i440fx-rhel7.0.0'}
        self.assertEqual(expected, result)

    @MonkeyPatch(libvirtconnection, 'get',
                 lambda : _getLibvirtConnStubFromFile(
                     'caps_libvirt_intel_i73770_nosnap.out'))
    @MonkeyPatch(numa, 'memory_by_cell', lambda x: {
        'total': '1', 'free': '1'})
    def test_topology(self):
        numa.update()
        result = numa.topology()
        # only check cpus, memory does not come from file
        expected = [0, 1, 2, 3, 4, 5, 6, 7]
        self.assertEqual(expected, result['0']['cpus'])

    @MonkeyPatch(libvirtconnection, 'get',
                 lambda : _getLibvirtConnStubFromFile(
                     'caps_libvirt_intel_i73770_nosnap.out'))
    @MonkeyPatch(numa, 'memory_by_cell', lambda x: {
        'total': '1', 'free': '1'})
    def test_getCpuTopology(self):
        numa.update()
        t = numa.cpu_topology()
        self.assertEqual(t.threads, 8)
        self.assertEqual(t.cores, 4)
        self.assertEqual(t.sockets, 1)
        self.assertEqual(t.online_cpus, [0, 1, 2, 3, 4, 5, 6, 7])

    @MonkeyPatch(libvirtconnection, 'get', _getLibvirtConnStub)
    def test_getTscFrequency_libvirt(self):
        freq = caps._getTscFrequency()
        self.assertEqual(freq, "1234000000")

    @MonkeyPatch(libvirtconnection, 'get', _getLibvirtConnStubEmpty)
    def test_getTscFrequency_no_counter(self):
        freq = caps._getTscFrequency()
        self.assertEqual(freq, "")

    @MonkeyPatch(commands, 'run', lambda x: b'crypto.fips_enabled = 1\n')
    def test_getFipsEnabledOn(self):
        self.assertTrue(caps._getFipsEnabled())

    @MonkeyPatch(commands, 'run', lambda x: b'crypto.fips_enabled = 0\n')
    def test_getFipsEnabledOff(self):
        self.assertFalse(caps._getFipsEnabled())

    # A hacky way to throw an exception from a lambda
    @MonkeyPatch(commands, 'run',
                 lambda x: (_ for _ in ()).throw(Exception("A problem")))
    def test_getFipsEnabledOffWhenError(self):
        self.assertFalse(caps._getFipsEnabled())

    @MonkeyPatch(libvirtconnection, 'get',
                 lambda: _getLibvirtConnStubForTscScaling('yes'))
    def test_getTscScalingYes(self):
        scaling = caps._getTscScaling()
        self.assertTrue(scaling)

    @MonkeyPatch(libvirtconnection, 'get',
                 lambda: _getLibvirtConnStubForTscScaling('no'))
    def test_getTscScalingNo(self):
        scaling = caps._getTscScaling()
        self.assertFalse(scaling)

    @MonkeyPatch(cpuinfo, 'flags', lambda: ['flag_1', 'flag_2', 'flag_3'])
    @MonkeyPatch(machinetype, 'cpu_features',
                 lambda: ['flag_3', 'feature_1', 'feature_2'])
    @MonkeyPatch(machinetype, 'compatible_cpu_models', lambda: [])
    def test_getFlagsAndFeatures(self):
        flags = caps._getFlagsAndFeatures()
        expected = ['flag_1', 'flag_2', 'flag_3', 'feature_1', 'feature_2']
        self.assertEqual(5, len(flags))
        self.assertTrue(all([x in flags for x in expected]))

    @MonkeyPatch(cpuinfo, 'flags', lambda: ['flag_1', 'flag_2', 'flag_3'])
    @MonkeyPatch(machinetype, 'cpu_features', lambda: [])
    @MonkeyPatch(machinetype, 'compatible_cpu_models', lambda: [])
    def test_getFlagsAndFeaturesEmptyFeatures(self):
        flags = caps._getFlagsAndFeatures()
        expected = ['flag_1', 'flag_2', 'flag_3']
        self.assertEqual(3, len(flags))
        self.assertTrue(all([x in flags for x in expected]))
