# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import
from __future__ import division

import os
import platform

from testlib import VdsmTestCase as TestCaseBase
from monkeypatch import MonkeyPatch

from vdsm import cpuinfo
from vdsm.common import cpuarch


def _outfile(name):
    test_path = os.path.realpath(__file__)
    dir_name = os.path.split(test_path)[0]
    return os.path.join(dir_name, 'cpuinfo', name)


class TestCpuInfo(TestCaseBase):

    def setUp(self):
        cpuinfo._cpuinfo.invalidate()

    @MonkeyPatch(cpuinfo, '_PATH', _outfile('cpuinfo_E5649_x86_64.out'))
    @MonkeyPatch(platform, 'machine', lambda: cpuarch.X86_64)
    def test_cpuinfo_E5649_x86_64(self):
        self.assertEqual(
            set(cpuinfo.flags()),
            set(('pebs', 'ssse3', 'pge', 'vmx', 'clflush', 'syscall', 'vme',
                 'dtes64', 'tsc', 'est', 'xtopology', 'xtpr', 'cmov', 'nx',
                 'constant_tsc', 'pat', 'bts', 'tpr_shadow', 'smx', 'lm',
                 'msr', 'fpu', 'fxsr', 'tm', 'pae', 'arch_perfmon', 'acpi',
                 'popcnt', 'mmx', 'arat', 'flexpriority', 'cx8', 'nonstop_tsc',
                 'mce', 'de', 'sse4_1', 'pclmulqdq', 'mca', 'pse', 'pni',
                 'rep_good', 'pdcm', 'ht', 'pdpe1gb', 'apic', 'sse', 'sse4_2',
                 'dca', 'aperfmperf', 'monitor', 'lahf_lm', 'rdtscp', 'aes',
                 'vnmi', 'sse2', 'ss', 'ept', 'ds_cpl', 'vpid', 'pbe', 'cx16',
                 'pse36', 'mtrr', 'dts', 'tm2', 'epb')))

        self.assertEqual(cpuinfo.frequency(), '2533.402')
        self.assertEqual(cpuinfo.model(),
                         'Intel(R) Xeon(R) CPU           E5649  @ 2.53GHz')

    @MonkeyPatch(cpuinfo, '_PATH', _outfile('cpuinfo_POWER8E_ppc64le.out'))
    @MonkeyPatch(platform, 'machine', lambda: cpuarch.PPC64LE)
    def test_cpuinfo_POWER8E_ppc64le(self):
        self.assertEqual(cpuinfo.flags(), ['powernv'])
        self.assertEqual(cpuinfo.frequency(), '3690.000000')
        self.assertEqual(cpuinfo.model(),
                         'POWER8E (raw), altivec supported')
        self.assertEqual(cpuinfo.platform(), 'PowerNV')
        self.assertEqual(cpuinfo.machine(), 'PowerNV 8247-22L')

    @MonkeyPatch(cpuinfo, '_PATH', _outfile('cpuinfo_aarch64.out'))
    @MonkeyPatch(platform, 'machine', lambda: cpuarch.AARCH64)
    def test_cpuinfo_aarch64(self):
        self.assertEqual(cpuinfo.flags(), ['fp', 'asimd', 'evtstrm'])
        self.assertEqual(cpuinfo.frequency(), '100.00')
        self.assertEqual(cpuinfo.model(),
                         '0x000')

    @MonkeyPatch(cpuinfo, '_PATH', _outfile('cpuinfo_z14_s390x.out'))
    @MonkeyPatch(platform, 'machine', lambda: cpuarch.S390X)
    def test_cpuinfo_s390x_z14(self):
        self.assertEqual(cpuinfo.flags(),
                         ['esan3', 'zarch', 'stfle', 'msa', 'ldisp', 'eimm',
                          'dfp', 'edat', 'etf3eh', 'highgprs', 'te', 'vx',
                          'vxd', 'vxe', 'sie'])
        self.assertEqual(cpuinfo.frequency(), '5208')
        self.assertEqual(cpuinfo.model(),
                         '3906')

    @MonkeyPatch(cpuinfo, '_PATH', _outfile('cpuinfo_z196_s390x.out'))
    @MonkeyPatch(platform, 'machine', lambda: cpuarch.S390X)
    def test_cpuinfo_s390x_z196(self):
        self.assertEqual(cpuinfo.flags(),
                         ['esan3', 'zarch', 'stfle', 'msa', 'ldisp', 'eimm',
                          'dfp', 'etf3eh', 'highgprs'])
        self.assertEqual(cpuinfo.frequency(), 'unavailable')
        self.assertEqual(cpuinfo.model(),
                         '2817')

    @MonkeyPatch(cpuinfo, '_PATH', _outfile('cpuinfo_E5649_x86_64.out'))
    @MonkeyPatch(platform, 'machine', lambda: 'noarch')
    def test_cpuinfo_unsupported_arch(self):
        self.assertRaises(cpuarch.UnsupportedArchitecture,
                          cpuinfo._cpuinfo)
