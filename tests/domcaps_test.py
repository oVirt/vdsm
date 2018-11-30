#
# Copyright 2017 IBM Corp.
# Copyright 2012-2017 Red Hat, Inc.
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

    def __init__(self, arch):
        test_path = os.path.realpath(__file__)
        dir_name = os.path.split(test_path)[0]
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
