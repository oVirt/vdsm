#
# Copyright 2017 IBM Corp.
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

import libvirt
import os

from monkeypatch import MonkeyPatchScope
from testlib import VdsmTestCase as TestCaseBase
from testlib import expandPermutations, permutations

from vdsm import machinetype
from vdsm.common import cpuarch


class FakeConnection(object):

    def __init__(self, arch, file_name=None):
        test_path = os.path.realpath(__file__)
        dir_name = os.path.split(test_path)[0]
        if file_name is None:
            file_name = 'domcaps_libvirt_%s.out' % (arch,)
        self.domcapspath = os.path.join(dir_name, file_name)
        self.arch = arch

    def getDomainCapabilities(self, *args):
        with open(self.domcapspath) as f:
            return f.read()

    def compareCPU(self, xml, flags):
        if self.arch != cpuarch.X86_64:
            return libvirt.VIR_CPU_COMPARE_SUPERSET
        for model in ('Skylake', 'Broadwell', 'EPYC', 'Opteron',):
            if model in xml:
                return libvirt.VIR_CPU_COMPARE_INCOMPATIBLE
        if 'Haswell' in xml:
            return libvirt.VIR_CPU_COMPARE_IDENTICAL
        return libvirt.VIR_CPU_COMPARE_SUPERSET


class FailingConnection(object):

    def getDomainCapabilities(self, *args):
        raise libvirt.libvirtError('test')


_EXPECTED_CPU_MODELS_X86_64 = (
    'qemu64', 'qemu32', 'pentium3', 'pentium2', 'pentium', 'kvm64', 'kvm32',
    'coreduo', 'core2duo', 'Penryn', 'Opteron_G2', 'Opteron_G1', 'Nehalem',
    'Nehalem-IBRS', 'Conroe', '486',
)
_EXPECTED_CPU_MODELS_PPC64LE = ('POWER9', 'POWER8',)
_EXPECTED_CPU_MODELS_S390X = (
    'z10EC-base', 'z9EC-base', 'z196.2-base', 'z900-base', 'z990',
    'z900.2-base', 'z900.3', 'z114', 'z890-base', 'z13.2-base', 'zEC12.2',
    'z10BC', 'z900.2', 'z10BC.2', 'z196', 'z9EC', 'z990-base', 'z10EC.3',
    'z900', 'z9EC.3-base', 'z990.5-base', 'z10EC.2', 'z9BC.2', 'z10EC',
    'z990.3-base', 'z13s', 'z10EC.3-base', 'zEC12.2-base', 'z890.3-base',
    'z9EC.3', 'z990.5', 'z13', 'z13s-base', 'z14-base', 'z9EC.2', 'z990.4',
    'zEC12-base', 'z9EC.2-base', 'zBC12', 'z196.2', 'z990.3', 'z990.2-base',
    'z900.3-base', 'z890.3', 'z10EC.2-base', 'z990.2', 'z890.2', 'zBC12-base',
    'z800-base', 'zEC12', 'z9BC.2-base', 'z9BC', 'z10BC.2-base', 'z990.4-base',
    'qemu', 'z10BC-base', 'z9BC-base', 'z800', 'z890.2-base', 'z13.2',
    'z114-base', 'z196-base', 'z13-base', 'z890',
)

_EXPECTED_CPU_FEATURES_X86_64 = [
    'vme', 'ss', 'pclmuldq', 'pcid', 'x2apic', 'tsc-deadline', 'hypervisor',
    'arat', 'tsc_adjust', 'stibp', 'pdpe1gb', 'rdtscp', 'invtsc',
]
_EXPECTED_CPU_FEATURES_PPC_64 = []
_EXPECTED_CPU_FEATURES_S390X = [
    'aen', 'cmmnt', 'aefsi', 'mepoch', 'msa8', 'msa7', 'msa6', 'msa5', 'msa4',
    'msa3', 'msa2', 'msa1', 'sthyi', 'edat', 'ri', 'edat2', 'vx', 'ipter',
    'vxeh', 'vxpd', 'esop', 'iep', 'cte', 'gs', 'zpci', 'sea_esop2', 'te',
    'cmm',
]


@expandPermutations
class TestDomCaps(TestCaseBase):

    def testCpuTypeS390X(self):
        conn = FakeConnection(cpuarch.S390X)
        dom_models = machinetype.domain_cpu_models(conn, cpuarch.S390X,
                                                   'custom')
        exp_models = {'z14-base': 'yes', 'z14': 'no'}
        for model, usable in exp_models.items():
            self.assertEqual(dom_models[model], usable)

    @permutations([
        # arch, expected_models
        [cpuarch.X86_64, _EXPECTED_CPU_MODELS_X86_64],
        [cpuarch.PPC64LE, _EXPECTED_CPU_MODELS_PPC64LE],
        [cpuarch.S390X, _EXPECTED_CPU_MODELS_S390X],
    ])
    def test_cpu_models(self, arch, expected_models):
        machinetype.compatible_cpu_models.invalidate()
        with MonkeyPatchScope([
                (machinetype.cpuarch, 'real', lambda: arch),
                (machinetype.libvirtconnection, 'get',
                 lambda: FakeConnection(arch)),
        ]):
            result = machinetype.compatible_cpu_models()
        result = set(result)
        expected = set(['model_' + m for m in expected_models])
        self.assertEqual(result, expected)

    def test_libvirt_exception(self):
        machinetype.compatible_cpu_models.invalidate()
        with MonkeyPatchScope([
                (machinetype.libvirtconnection, 'get',
                 lambda: FailingConnection()),
        ]):
            result = machinetype.compatible_cpu_models()
            self.assertEqual(result, [])

    @permutations([
        # arch, expected_features
        [cpuarch.X86_64, _EXPECTED_CPU_FEATURES_X86_64 + ['spec_ctrl']],
        [cpuarch.PPC64LE, _EXPECTED_CPU_FEATURES_PPC_64],
        [cpuarch.S390X, _EXPECTED_CPU_FEATURES_S390X],
    ])
    def test_cpu_features(self, arch, expected_features):
        machinetype.cpu_features.invalidate()
        conn = FakeConnection(arch)
        with MonkeyPatchScope([
                (machinetype.cpuarch, 'real', lambda: arch),
                (machinetype.libvirtconnection, 'get', lambda: conn), ]):
            result = machinetype.cpu_features()
            self.assertEqual(result, expected_features)

    def test_cpu_features_no_ibrs(self):
        machinetype.cpu_features.invalidate()
        conn = FakeConnection(cpuarch.X86_64,
                              file_name='domcaps_libvirt_x86_64_noibrs.out')
        with MonkeyPatchScope([
                (machinetype.cpuarch, 'real', lambda: cpuarch.X86_64),
                (machinetype.libvirtconnection, 'get', lambda: conn), ]):
            result = machinetype.cpu_features()
            self.assertEqual(result, _EXPECTED_CPU_FEATURES_X86_64)

    def test_libvirt_exception_cpu_features(self):
        machinetype.cpu_features.invalidate()
        with MonkeyPatchScope([
                (machinetype.libvirtconnection, 'get',
                 lambda: FailingConnection()),
        ]):
            result = machinetype.cpu_features()
            self.assertEqual(result, [])
