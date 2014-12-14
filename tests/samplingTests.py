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
import tempfile
import random
import shutil

from vdsm import ipwrapper
import virt.sampling as sampling

from testValidation import ValidateRunningAsRoot
from testrunner import VdsmTestCase as TestCaseBase
from monkeypatch import MonkeyPatchScope
from functional import dummy


class SamplingTests(TestCaseBase):
    proc_stat_template = """
cpu  4350684 14521 1120299 20687999 677480 197238 48056 0 1383 0
cpu0 1082143 1040 335283 19253788 628168 104752 21570 0 351 0
cpu1 1010362 2065 294113 474697 18915 41743 9793 0 308 0
cpu2 1296289 6812 283613 472725 18664 30549 9776 0 213 0
cpu3 961889 4603 207289 486787 11732 20192 6916 0 511 0
ctxt 690239751
%(btime_line)s
processes 450432
procs_running 2
procs_blocked 0
"""
    fixture_good = proc_stat_template % {'btime_line': 'btime 1395249141'}
    fixture_missing = proc_stat_template % {'btime_line': 'btime'}
    fixture_malformed = proc_stat_template % {'btime_line':
                                              'btime 22not_a_number3'}
    fixture_extra = proc_stat_template % {'btime_line': 'btime 1395249141 foo'}

    def _createFixtureFile(self, name, content):
        path = os.path.join(self._tmpDir, name)
        with open(path, 'w') as f:
            f.write(content)
        return path

    def setUp(self):
        self._tmpDir = tempfile.mkdtemp()
        self._good_path = self._createFixtureFile('good',
                                                  self.fixture_good)
        self._missing_path = self._createFixtureFile('missing',
                                                     self.fixture_missing)
        self._malformed_path = self._createFixtureFile('malformed',
                                                       self.fixture_malformed)
        self._extra_path = self._createFixtureFile('extra',
                                                   self.fixture_extra)

    def tearDown(self):
        shutil.rmtree(self._tmpDir)

    def testBootTimeOk(self):
        with MonkeyPatchScope([(sampling, '_PROC_STAT_PATH',
                                self._good_path)]):
            self.assertEquals(sampling.getBootTime(),
                              1395249141)

    def testBootTimeEmpty(self):
        with MonkeyPatchScope([(sampling, '_PROC_STAT_PATH',
                                '/dev/null')]):
            with self.assertRaises(ValueError):
                sampling.getBootTime()

    def testBootTimeMissing(self):
        with MonkeyPatchScope([(sampling, '_PROC_STAT_PATH',
                                self._missing_path)]):
            with self.assertRaises(ValueError):
                sampling.getBootTime()

    def testBootTimeMalformed(self):
        with MonkeyPatchScope([(sampling, '_PROC_STAT_PATH',
                                self._malformed_path)]):
            with self.assertRaises(ValueError):
                sampling.getBootTime()

    def testBootTimeNonExistantFile(self):
        with MonkeyPatchScope([(sampling, '_PROC_STAT_PATH',
                                '/i/do/not/exist/1234567890')]):
            with self.assertRaises(IOError):
                sampling.getBootTime()

    def testBootTimeExtra(self):
        with MonkeyPatchScope([(sampling, '_PROC_STAT_PATH',
                                self._extra_path)]):
            self.assertEquals(sampling.getBootTime(), 1395249141)


@contextmanager
def dummy_if():
    dummy_name = dummy.create()
    try:
        yield dummy_name
    finally:
        dummy.remove(dummy_name)


@contextmanager
def vlan(name, link, vlan_id):
    ipwrapper.linkAdd(name, 'vlan', link=link, args=['id', str(vlan_id)])
    try:
        yield
    finally:
        try:
            ipwrapper.linkDel(name)
        except ipwrapper.IPRoute2Error:
            # faultyGetLinks is expected to have already removed the vlan
            # device.
            pass


class InterfaceSampleTests(TestCaseBase):
    def setUp(self):
        self.NEW_VLAN = 'vlan_%s' % (random.randint(0, 1000))

    def testDiff(self):
        lo = ipwrapper.getLink('lo')
        s0 = sampling.InterfaceSample(lo)
        s1 = sampling.InterfaceSample(lo)
        s1.operstate = 'x'
        self.assertEquals('operstate:x', s1.connlog_diff(s0))

    @ValidateRunningAsRoot
    def testHostSampleReportsNewInterface(self):
        hs_before = sampling.HostSample(os.getpid())
        interfaces_before = set(hs_before.interfaces.iterkeys())

        with dummy_if() as dummy_name:
            hs_after = sampling.HostSample(os.getpid())
            interfaces_after = set(hs_after.interfaces.iterkeys())
            interfaces_diff = interfaces_after - interfaces_before
            self.assertEqual(interfaces_diff, set([dummy_name]))

    @ValidateRunningAsRoot
    def testHostSampleHandlesDisappearingVlanInterfaces(self):
        original_getLinks = ipwrapper.getLinks

        def faultyGetLinks():
            all_links = list(original_getLinks())
            ipwrapper.linkDel(self.NEW_VLAN)
            return iter(all_links)

        with MonkeyPatchScope(
                [(ipwrapper, 'getLinks', faultyGetLinks)]):
            with dummy_if() as dummy_name:
                with vlan(self.NEW_VLAN, dummy_name, 999):
                    hs = sampling.HostSample(os.getpid())
                    self.assertNotIn(self.NEW_VLAN, hs.interfaces)