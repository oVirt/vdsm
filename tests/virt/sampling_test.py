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

import itertools
import threading

from vdsm.virt import sampling
from vdsm import numa

from monkeypatch import MonkeyPatchScope

from testlib import permutations, expandPermutations
from testlib import VdsmTestCase as TestCaseBase
import vmfakelib as fake


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
        _, collected = win.last()
        self.assertEqual(self._VALUES[1], collected)

    def test_second_last(self):
        win = sampling.SampleWindow(size=2)
        win.append(self._VALUES[0])
        win.append(self._VALUES[1])
        _, collected = win.last(nth=2)
        self.assertEqual(self._VALUES[0], collected)

    def test_last_error(self):
        win = sampling.SampleWindow(size=2)
        win.append(self._VALUES[0])
        win.append(self._VALUES[1])
        _, collected = win.last(nth=3)
        self.assertEqual(None, collected)

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

    def test_get_batch(self):
        self._feed_cache((
            ({'a': 'old', 'b': 'old'}, 1),
            ({'a': 'new', 'b': 'new'}, 2),
            ({'a': 'exold', 'b': 'exold'}, 0)
        ))
        res = self.cache.get_batch()
        self.assertEqual(
            sorted(('a', 'b',)),
            sorted(res.keys())
        )

    def test_get_batch_missing(self):
        self._feed_cache((
            ({'a': 'old', 'b': 'old'}, 1),
            ({'a': 'new'}, 2),
        ))
        res = self.cache.get_batch()
        self.assertEqual(
            ['a', ],
            sorted(res.keys())
        )

    def test_get_batch_alternating(self):
        self._feed_cache((
            ({'b': 'old'}, 1),
            ({'a': 'new'}, 2),
        ))
        res = self.cache.get_batch()
        self.assertEqual([], list(res.keys()))

    def test_get_batch_from_empty(self):
        res = self.cache.get_batch()
        self.assertIs(res, None)

    def test_get_missing(self):
        self._feed_cache((
            ({'a': 'foo'}, 1),
            ({'a': 'bar'}, 2)
        ))
        self.fake_monotonic_time.freeze(value=3)
        res = self.cache.get('b')
        self.assertTrue(res.is_empty())
        self.assertEqual(res.stats_age, 3)

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
        self.fake_monotonic_time.freeze(value=4)
        self.assertEqual(self.cache.get('a'),
                         ('bar', 'baz', 1, 1))
        res = self.cache.get('b')
        self.assertTrue(res.is_empty())
        self.assertEqual(res.stats_age, 1)

    def test_missing_initially(self):
        self._feed_cache((
            ({'a': 'foo'}, 1),
            ({'a': 'bar', 'b': 'baz'}, 2)
        ))
        self.fake_monotonic_time.freeze(value=3)
        res = self.cache.get('b')
        self.assertTrue(res.is_empty())
        self.assertEqual(res.stats_age, 1)

    def test_seen_long_ago(self):
        # simulate a sample long evicted from the cache
        self.cache.add('a')
        # but the cache must not be empty!
        self._feed_cache((
            ({'b': 'foo'}, 1),
            ({'b': 'foo', 'c': 'bar'}, 2),
        ))
        self.fake_monotonic_time.freeze(value=101)
        res = self.cache.get('a')
        self.assertTrue(res.is_empty())
        self.assertEqual(res.stats_age, 100)

    def _feed_cache(self, samples):
        for sample in samples:
            self.cache.put(*sample)


class NumaNodeMemorySampleTests(TestCaseBase):

    def _monkeyPatchedMemorySample(self, freeMemory, totalMemory):
        node_id, cpu_id = 0, 0

        def fakeMemoryStats(cell):
            return {
                'free': freeMemory,
                'total': totalMemory
            }

        def fakeNumaTopology():
            return {
                node_id: {
                    'cpus': [cpu_id],
                    'hugepages': {
                        4: {'totalPages': '2500'},
                        2048: {'totalPages': '100'}}
                }
            }

        fakeConnection = fake.Connection()
        fakeConnection.free_pages = {
            '4': '5',
            '2048': '10'
        }

        return MonkeyPatchScope([(numa, 'topology',
                                  fakeNumaTopology),
                                 (numa, 'memory_by_cell',
                                  fakeMemoryStats),
                                 (numa.libvirtconnection,
                                  'get',
                                  lambda : fakeConnection)])

    def testMemoryStatsWithZeroMemoryAsString(self):
        expected = {0: {
            'memPercent': 100,
            'memFree': '0',
            'hugepages': {
                    4: {'freePages': '5'},
                    2048: {'freePages': '10'}
                    }}}

        with self._monkeyPatchedMemorySample(freeMemory='0', totalMemory='0'):
            memorySample = sampling.NumaNodeMemorySample()
            self.assertEqual(memorySample.nodesMemSample, expected)

    def testMemoryStatsWithZeroMemoryAsInt(self):
        expected = {0: {
            'memPercent': 100,
            'memFree': '0',
            'hugepages': {
                    4: {'freePages': '5'},
                    2048: {'freePages': '10'}
                    }}}

        with self._monkeyPatchedMemorySample(freeMemory='0', totalMemory=0):
            memorySample = sampling.NumaNodeMemorySample()
            self.assertEqual(memorySample.nodesMemSample, expected)

    def testMemoryStats(self):
        expected = {0: {
            'memPercent': 40,
            'memFree': '600',
            'hugepages': {
                    4: {'freePages': '5'},
                    2048: {'freePages': '10'}
                    }}}

        with self._monkeyPatchedMemorySample(freeMemory='600',
                                             totalMemory='1000'):
            memorySample = sampling.NumaNodeMemorySample()
            self.assertEqual(memorySample.nodesMemSample, expected)


class HostStatsMonitorTests(TestCaseBase):
    FAILED_SAMPLE = 3  # random 'small' value
    STOP_SAMPLE = 6  # ditto

    def setUp(self):
        self._hs = None
        self._sampleCount = 0
        self._samplingDone = threading.Event()

    def testSamplesWraparound(self):
        NUM = sampling.HOST_STATS_AVERAGING_WINDOW + 1

        samples = sampling.SampleWindow(
            sampling.HOST_STATS_AVERAGING_WINDOW)

        class FakeHostSample(object):

            counter = 0

            def __repr__(self):
                return "FakeHostSample(id=%i)" % self.id

            def __init__(self, *args):
                self.id = FakeHostSample.counter
                FakeHostSample.counter += 1

        with MonkeyPatchScope([(sampling, 'HostSample', FakeHostSample)]):
            hs = sampling.HostMonitor(samples=samples)
            for _ in range(NUM):
                hs()

            first, last, _ = samples.stats()
            self.assertEqual(first.id,
                             FakeHostSample.counter -
                             sampling.HOST_STATS_AVERAGING_WINDOW)
            self.assertEqual(last.id,
                             FakeHostSample.counter - 1)


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
