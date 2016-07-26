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
import itertools
import os
import tempfile
import random
import shutil
import threading
import time

from vdsm import ipwrapper
from vdsm.password import ProtectedPassword
import virt.sampling as sampling

import caps

from testValidation import brokentest, ValidateRunningAsRoot
from testlib import permutations, expandPermutations
from testlib import VdsmTestCase as TestCaseBase
from monkeypatch import MonkeyPatchScope
from functional import dummy
import vmfakelib as fake


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


def read_password():
    return ProtectedPassword('password')


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
        interfaces_before = set(
            sampling._get_interfaces_and_samples().iterkeys())

        with dummy.device() as dummy_name:
            interfaces_after = set(
                sampling._get_interfaces_and_samples().iterkeys())
            interfaces_diff = interfaces_after - interfaces_before
            self.assertEqual(interfaces_diff, {dummy_name})

    @ValidateRunningAsRoot
    def testHostSampleHandlesDisappearingVlanInterfaces(self):
        original_getLinks = ipwrapper.getLinks

        def faultyGetLinks():
            all_links = list(original_getLinks())
            ipwrapper.linkDel(self.NEW_VLAN)
            return iter(all_links)

        with MonkeyPatchScope([(ipwrapper, 'getLinks', faultyGetLinks)]):
            with dummy.device() as dummy_name, vlan(
                    self.NEW_VLAN, dummy_name, 999):
                interfaces_and_samples = sampling._get_interfaces_and_samples()
                self.assertNotIn(self.NEW_VLAN, interfaces_and_samples)


@expandPermutations
class SampleWindowTests(TestCaseBase):
    _VALUES = (19, 42, 23)  # throwaway values, no meaning

    def setUp(self):
        self._counter = itertools.count(0)
        self.win = sampling.SampleWindow(
            size=2, timefn=lambda: next(self._counter))

    @permutations([[-1], [0]])
    def test_window_size_bad_values(self, size):
        self.assertRaises(
            ValueError,
            sampling.SampleWindow, size)

    def test_last(self):
        win = sampling.SampleWindow(size=2)
        win.append(self._VALUES[0])
        win.append(self._VALUES[1])
        self.assertEqual(self._VALUES[1], win.last())

    def test_second_last(self):
        win = sampling.SampleWindow(size=2)
        win.append(self._VALUES[0])
        win.append(self._VALUES[1])
        self.assertEqual(self._VALUES[0], win.last(nth=2))

    def test_last_error(self):
        win = sampling.SampleWindow(size=2)
        win.append(self._VALUES[0])
        win.append(self._VALUES[1])
        self.assertEqual(None, win.last(nth=3))

    def test_stats_empty(self):
        self.assertEqual(self.win.stats(), (None, None, None))

    def test_stats_one_value(self):
        self.win.append(self._VALUES[0])
        self.assertEqual(self.win.stats(), (None, None, None))

    def test_stats_two_values(self):
        for val in self._VALUES:
            self.win.append(val)
        self.assertEqual(self.win.stats(),
                         (self._VALUES[-2], self._VALUES[-1], 1))


class HostStatsThreadTests(TestCaseBase):
    FAILED_SAMPLE = 3  # random 'small' value
    STOP_SAMPLE = 6  # ditto

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

    def setUp(self):
        self._hs = None
        self._sampleCount = 0
        self._samplingDone = threading.Event()

    @brokentest
    def testContinueWithErrors(self):
        """
        bz1113948: do not give up on errors != TimeoutError
        """
        def WrapHostSample(pid):
            self._sampleCount += 1
            if self._sampleCount == self.FAILED_SAMPLE:
                raise ValueError
            if self._sampleCount == self.STOP_SAMPLE:
                self._hs.stop()
                self._samplingDone.set()
            return sampling.HostSample(1)

        with MonkeyPatchScope([(sampling, 'HostSample', WrapHostSample),
                               (sampling.HostStatsThread,
                                   'SAMPLE_INTERVAL_SEC', 0.1)]):
            self._hs = sampling.HostStatsThread(self.log)
            self._hs.start()
            self._samplingDone.wait(3.0)
            self.assertTrue(self._samplingDone.is_set())
            self.assertTrue(self._sampleCount >= self.STOP_SAMPLE)

    def testOutputWithNoSamples(self):
        expected = {
            'cpuIdle': 100.0,
            'cpuSys': 0.0,
            'cpuSysVdsmd': 0.0,
            'cpuUser': 0.0,
            'cpuUserVdsmd': 0.0,
            'rxRate': 0.0,
            'txRate': 0.0,
            'elapsedTime': 0,
        }
        with MonkeyPatchScope([(time, 'time', lambda: 0)]):
            self._hs = sampling.HostStatsThread(self.log)
            self.assertEquals(self._hs.get(), expected)

    def testSamplesWraparound(self):
        NUM = sampling.HostStatsThread.AVERAGING_WINDOW + 1

        class FakeEvent(object):
            def __init__(self, *args):
                self.counter = 0

            def isSet(self):
                return self.counter >= NUM

            def set(self):
                pass

            def wait(self, unused):
                self.counter += 1

        class FakeHostSample(object):

            counter = 0

            def __repr__(self):
                return "FakeHostSample(id=%i)" % self.id

            def __init__(self, *args):
                self.id = FakeHostSample.counter
                FakeHostSample.counter += 1

            def to_connlog(self):
                pass

            def connlog_diff(self, *args):
                pass

        with MonkeyPatchScope([(sampling, 'HostSample', FakeHostSample)]):
            self._hs = sampling.HostStatsThread(self.log)
            self._hs._sampleInterval = 0
            # we cannot monkey patch, it will interfer on threading internals
            self._hs._stopEvent = FakeEvent()
            self._hs.start()
            self._hs.join()
            first, last, _ = self._hs._samples.stats()
            self.assertEqual(first.id,
                             FakeHostSample.counter -
                             sampling.HostStatsThread.AVERAGING_WINDOW)
            self.assertEqual(last.id,
                             FakeHostSample.counter - 1)

    def testCpuCoreStats(self):
        self._hs = sampling.HostStatsThread(self.log)
        cpu_sample = {'user': 1.0, 'sys': 2.0}

        # Both CPUs are online and first and last samples are present
        self._hs._samples.append(
            fake.HostSample(1.0, {0: cpu_sample, 1: cpu_sample}))
        self._hs._samples.append(
            fake.HostSample(2.0, {0: cpu_sample, 1: cpu_sample}))

        with MonkeyPatchScope([(caps, 'getNumaTopology',
                                self._fakeNumaTopology)]):
            result = self._hs._getCpuCoresStats()
            self.assertEqual(len(result), 2)
            self.assertEqual(result['0'], self._core_zero_stats)
            self.assertEqual(result['1'], self._core_one_stats)

    def testSkipStatsOnMissingFirstSample(self):
        self._hs = sampling.HostStatsThread(self.log)
        cpu_sample = {'user': 1.0, 'sys': 2.0}

        # CPU one suddenly went offline and no new sample from it is available
        self._hs._samples.append(
            fake.HostSample(1.0, {0: cpu_sample}))
        self._hs._samples.append(
            fake.HostSample(2.0, {0: cpu_sample, 1: cpu_sample}))

        with MonkeyPatchScope([(caps, 'getNumaTopology',
                                self._fakeNumaTopology)]):
            result = self._hs._getCpuCoresStats()
            self.assertEqual(len(result), 1)
            self.assertEqual(result['0'], self._core_zero_stats)

    def testSkipStatsOnMissingLastSample(self):
        self._hs = sampling.HostStatsThread(self.log)
        cpu_sample = {'user': 1.0, 'sys': 2.0}

        # CPU one suddenly came online and the second sample is still missing
        self._hs._samples.append(
            fake.HostSample(1.0, {0: cpu_sample, 1: cpu_sample}))
        self._hs._samples.append(
            fake.HostSample(2.0, {0: cpu_sample}))

        with MonkeyPatchScope([(caps, 'getNumaTopology',
                                self._fakeNumaTopology)]):
            result = self._hs._getCpuCoresStats()
            self.assertEqual(len(result), 1)
            self.assertEqual(result['0'], self._core_zero_stats)


class NumaNodeMemorySampleTests(TestCaseBase):

    def _monkeyPatchedMemorySample(self, freeMemory, totalMemory):
        node_id, cpu_id = 0, 0

        def fakeMemoryStats():
            return {
                'free': freeMemory,
                'total': totalMemory
            }

        def fakeNumaTopology():
            return {
                node_id: {
                    'cpus': [cpu_id]
                }
            }

        return MonkeyPatchScope([(caps, 'getNumaTopology',
                                  fakeNumaTopology),
                                 (caps, 'getUMAHostMemoryStats',
                                  fakeMemoryStats)])

    def testMemoryStatsWithZeroMemoryAsString(self):
        expected = {0: {'memPercent': 100, 'memFree': '0'}}

        with self._monkeyPatchedMemorySample(freeMemory='0', totalMemory='0'):
            memorySample = sampling.NumaNodeMemorySample()
            self.assertEqual(memorySample.nodesMemSample, expected)

    def testMemoryStatsWithZeroMemoryAsInt(self):
        expected = {0: {'memPercent': 100, 'memFree': '0'}}

        with self._monkeyPatchedMemorySample(freeMemory='0', totalMemory=0):
            memorySample = sampling.NumaNodeMemorySample()
            self.assertEqual(memorySample.nodesMemSample, expected)

    def testMemoryStats(self):
        expected = {0: {'memPercent': 40, 'memFree': '600'}}

        with self._monkeyPatchedMemorySample(freeMemory='600',
                                             totalMemory='1000'):
            memorySample = sampling.NumaNodeMemorySample()
            self.assertEqual(memorySample.nodesMemSample, expected)


class FakeClock(object):

    STEP = 1

    def __init__(self, value=0):
        self.value = value
        self._frozen = False

    def freeze(self, value=None):
        if value is not None:
            self.value = value
        self._frozen = True

    def __call__(self):
        if not self._frozen:
            self.value += self.STEP
        return self.value


class StatsCacheTests(TestCaseBase):

    def setUp(self):
        self.fake_monotonic_time = FakeClock()
        self.cache = sampling.StatsCache(clock=self.fake_monotonic_time)

    def test_empty(self):
        res = self.cache.get('x')  # vmid not relevant
        self.assertTrue(res.is_empty())

    def test_not_enough_samples(self):
        self._feed_cache((
            ({'a': 42}, 1),
        ))
        res = self.cache.get('a')
        self.assertTrue(res.is_empty())

    def test_get(self):
        self._feed_cache((
            ({'a': 'foo'}, 1),
            ({'a': 'bar'}, 2)
        ))
        res = self.cache.get('a')
        self.assertEqual(res,
                         ('foo',
                          'bar',
                          FakeClock.STEP,
                          FakeClock.STEP))

    def test_get_missing(self):
        self._feed_cache((
            ({'a': 'foo'}, 1),
            ({'a': 'bar'}, 2)
        ))
        res = self.cache.get('b')
        self.assertTrue(res.is_empty())

    def test_put_overwrite(self):
        self._feed_cache((
            ({'a': 'foo'}, 1),
            ({'a': 'bar'}, 2),
            ({'a': 'baz'}, 3)
        ))
        res = self.cache.get('a')
        self.assertEqual(res,
                         ('bar',
                          'baz',
                          FakeClock.STEP,
                          FakeClock.STEP))

    def test_put_out_of_order(self):
        self._feed_cache((
            ({'a': 'foo'}, 1),
            ({'a': 'bar'}, 0),
            ({'a': 'baz'}, 3)
        ))
        res = self.cache.get('a')
        self.assertEqual(res,
                         ('foo',
                          'baz',
                          FakeClock.STEP,
                          0))

    def test_skip_one_cycle(self):
        # as unfortunate side effect, there is room only for
        # last two _global_ samples (not per-vm)
        self._feed_cache((
            ({'a': 'foo', 'b': 'foo'}, 1),
            ({'a': 'bar'}, 2),
            # here we lost sampling for 'b'
            ({'a': 'baz', 'b': 'baz'}, 3),
        ))
        self.assertEqual(self.cache.get('a'),
                         ('bar', 'baz', 1, FakeClock.STEP))
        res = self.cache.get('b')
        self.assertTrue(res.is_empty())

    def _feed_cache(self, samples):
        for sample in samples:
            self.cache.put(*sample)
