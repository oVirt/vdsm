#
# Copyright 2014-2018 Red Hat, Inc.
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
from __future__ import division

import os
import tempfile
import shutil

from vdsm.host import stats as hoststats
from vdsm import numa

from testlib import VdsmTestCase as TestCaseBase
from monkeypatch import MonkeyPatchScope

from virt import vmfakelib as fake


class BootTimeTests(TestCaseBase):
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
        with MonkeyPatchScope([(hoststats, '_PROC_STAT_PATH',
                                self._good_path)]):
            self.assertEqual(hoststats.get_boot_time(),
                             1395249141)

    def testBootTimeEmpty(self):
        with MonkeyPatchScope([(hoststats, '_PROC_STAT_PATH',
                                '/dev/null')]):
            with self.assertRaises(ValueError):
                hoststats.get_boot_time()

    def testBootTimeMissing(self):
        with MonkeyPatchScope([(hoststats, '_PROC_STAT_PATH',
                                self._missing_path)]):
            with self.assertRaises(ValueError):
                hoststats.get_boot_time()

    def testBootTimeMalformed(self):
        with MonkeyPatchScope([(hoststats, '_PROC_STAT_PATH',
                                self._malformed_path)]):
            with self.assertRaises(ValueError):
                hoststats.get_boot_time()

    def testBootTimeNonExistantFile(self):
        with MonkeyPatchScope([(hoststats, '_PROC_STAT_PATH',
                                '/i/do/not/exist/1234567890')]):
            with self.assertRaises(IOError):
                hoststats.get_boot_time()

    def testBootTimeExtra(self):
        with MonkeyPatchScope([(hoststats, '_PROC_STAT_PATH',
                                self._extra_path)]):
            self.assertEqual(hoststats.get_boot_time(), 1395249141)


class HostStatsThreadTests(TestCaseBase):

    _core_zero_stats = {
        'cpuIdle': '100.00',
        'cpuSys': '0.00',
        'cpuUser': '0.00',
        'nodeIndex': 0
    }

    _core_one_stats = {
        'cpuIdle': '100.00',
        'cpuSys': '0.00',
        'cpuUser': '0.00',
        'nodeIndex': 1
    }

    def _fakeNumaTopology(self):
        return {
            0: {'cpus': [0]},
            1: {'cpus': [1]}
        }

    def testCpuCoreStats(self):
        cpu_sample = {'user': 1.0, 'sys': 2.0}

        # Both CPUs are online and first and last samples are present
        first_sample = fake.HostSample(1.0, {0: cpu_sample, 1: cpu_sample})
        last_sample = fake.HostSample(2.0, {0: cpu_sample, 1: cpu_sample})

        with MonkeyPatchScope([(numa, 'topology',
                                self._fakeNumaTopology)]):
            result = hoststats._get_cpu_core_stats(first_sample, last_sample)
            self.assertEqual(len(result), 2)
            self.assertEqual(result['0'], self._core_zero_stats)
            self.assertEqual(result['1'], self._core_one_stats)

    def testSkipStatsOnMissingFirstSample(self):
        cpu_sample = {'user': 1.0, 'sys': 2.0}

        # CPU one suddenly went offline and no new sample from it is available
        first_sample = fake.HostSample(1.0, {0: cpu_sample})
        last_sample = fake.HostSample(2.0, {0: cpu_sample, 1: cpu_sample})

        with MonkeyPatchScope([(numa, 'topology',
                                self._fakeNumaTopology)]):
            result = hoststats._get_cpu_core_stats(first_sample, last_sample)
            self.assertEqual(len(result), 1)
            self.assertEqual(result['0'], self._core_zero_stats)

    def testSkipStatsOnMissingLastSample(self):
        cpu_sample = {'user': 1.0, 'sys': 2.0}

        first_sample = fake.HostSample(1.0, {0: cpu_sample, 1: cpu_sample})
        # CPU one suddenly came online and the second sample is still missing
        last_sample = fake.HostSample(2.0, {0: cpu_sample})

        with MonkeyPatchScope([(numa, 'topology',
                                self._fakeNumaTopology)]):
            result = hoststats._get_cpu_core_stats(first_sample, last_sample)
            self.assertEqual(len(result), 1)
            self.assertEqual(result['0'], self._core_zero_stats)

    def testOutputWithNoSamples(self):
        expected = {
            'cpuIdle': 100.0,
            'cpuSys': 0.0,
            'cpuSysVdsmd': 0.0,
            'cpuUser': 0.0,
            'cpuUserVdsmd': 0.0,
            'memUsed': 0.0,
            'elapsedTime': 0,
            'anonHugePages': 0.0,
            'cpuLoad': 0.0,
        }
        hoststats.start(lambda: 0)
        self.assertEqual(hoststats.produce(None, None), expected)

    def testSampleIntervalTooSmall(self):
        expected = {
            'cpuIdle': 100.0,
            'cpuSys': 0.0,
            'cpuSysVdsmd': 0.0,
            'cpuUser': 0.0,
            'cpuUserVdsmd': 0.0,
            'memUsed': 0.0,
            'elapsedTime': 0,
            'anonHugePages': 0.0,
            'cpuLoad': 0.0,
        }

        first_sample = fake.HostSample(1.0, {})
        last_sample = fake.HostSample(1.0, {})

        hoststats.start(lambda: 0)
        self.assertEqual(
            hoststats.produce(first_sample, last_sample),
            expected
        )


class HostStatsNetworkTests(TestCaseBase):

    def test_report_format(self):
        stats = hoststats.get_interfaces_stats()
        netstats = stats['network']

        self.assertIn('lo', netstats)

        iface_stats = netstats['lo']
        self.assertEqual(iface_stats['name'], 'lo')
        self.assertEqual(iface_stats['state'], 'up')
        self.assertEqual(iface_stats['speed'], '1000')
        self.assertIsInstance(iface_stats['tx'], str)
        self.assertIsInstance(iface_stats['rx'], str)
        self.assertIsInstance(iface_stats['rxDropped'], str)
        self.assertIsInstance(iface_stats['txDropped'], str)
        self.assertIsInstance(iface_stats['rxErrors'], str)
        self.assertIsInstance(iface_stats['txErrors'], str)
        self.assertIsInstance(iface_stats['sampleTime'], float)
