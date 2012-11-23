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
from testrunner import VdsmTestCase as TestCaseBase

import caps


class TestCaps(TestCaseBase):

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

    def testCpuTopology(self):
        testPath = os.path.realpath(__file__)
        dirName = os.path.split(testPath)[0]
        path = os.path.join(dirName, "caps_libvirt.out")
        t = caps.CpuTopology(file(path).read())
        self.assertEqual(t.threads(), 24)
        self.assertEqual(t.cores(), 12)
        self.assertEqual(t.sockets(), 2)

    def test_parseKeyVal(self):
        lines = ["x=&2", "y& = 2", " z = 2 ", " s=3=&'5", " w=", "4&"]
        expectedRes = [{'x': '&2', 'y&': '2', 'z': '2', 's': "3=&'5", 'w': ''},
                        {'x=': '2', 'y': '= 2', 's=3=': "'5", '4': ''}]
        sign = ["=", "&"]
        for res, s in zip(expectedRes, sign):
            self.assertEqual(res, caps._parseKeyVal(lines, s))

    def test_getIfaceByIP(self):
        expectedRes = ["wlan0", "virbr0"]
        ip = ["10.201.129.37", "192.168.122.90"]
        for res, i in zip(expectedRes, ip):
            self.assertEqual(res, caps._getIfaceByIP(i, "route_info.out"))
