# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import
from __future__ import division

import collections
import contextlib
import threading

import pytest

from vdsm import executor
from vdsm import schedule
from vdsm.common.time import monotonic_time

from vdsm.virt import sampling

from testlib import VdsmTestCase as TestCaseBase
from testlib import recorded


CacheSample = collections.namedtuple('CacheSample', ['stats', 'timestamp'])


class TestVMBulkSampling(TestCaseBase):

    CALL_TIMEOUT = 0.2  # seconds

    TIMEOUT = 2  # seconds

    def setUp(self):
        self.sched = schedule.Scheduler(name="test.Scheduler",
                                        clock=monotonic_time)
        self.sched.start()

        self.exc = executor.Executor(name="test.Executor",
                                     workers_count=1,
                                     max_tasks=100,
                                     scheduler=self.sched)
        self.exc.start()

    def tearDown(self):
        self.exc.stop()
        self.exc = None

        self.sched.stop()
        self.sched = None

    def test_collect_fast_path_as_default(self):
        vms = make_vms(num=3)
        conn = FakeConnection(vms=vms)
        cache = FakeStatsCache()

        sampler = sampling.VMBulkstatsMonitor(conn, conn.getVMs, cache)

        with cache.await_completion(self.CALL_TIMEOUT):
            self.exc.dispatch(sampler, self.CALL_TIMEOUT)

        self.assertCallSequence(
            conn.__calls__,
            ['getAllDomainStats']
        )

    @pytest.mark.slow
    def test_collect_slow_path_after_blocked(self):
        vms = make_vms(num=3)
        conn = FakeConnection(vms=vms)
        cache = FakeStatsCache()

        sampler = sampling.VMBulkstatsMonitor(conn, conn.getVMs, cache)

        with conn.stuck(self.TIMEOUT * 2):
            with cache.await_completion(self.TIMEOUT):
                # we expect only the call #2 (slow) to _complete_
                # while te connection is stuck. Please note that
                # call will always be recorded, even if not completed
                self.exc.dispatch(sampler, self.CALL_TIMEOUT)
                self.exc.dispatch(sampler, self.CALL_TIMEOUT)

        self.assertCallSequence(
            conn.__calls__,
            ['getAllDomainStats', 'domainListGetStats']
        )

    @pytest.mark.slow
    def test_collect_vm_unresponsive(self):
        vms = make_vms(num=3)
        conn = FakeConnection(vms=vms)
        cache = FakeStatsCache()

        sampler = sampling.VMBulkstatsMonitor(conn, conn.getVMs, cache)

        with conn.stuck(self.TIMEOUT * 2):
            with cache.await_completion(self.TIMEOUT, expected=2):
                # we only expect call #2 (slow) and call #3 (slow)
                # to _complete_, hence expected=2
                self.exc.dispatch(sampler, self.CALL_TIMEOUT)
                vms['1'].ready = False
                self.exc.dispatch(sampler, self.CALL_TIMEOUT)
                self.exc.dispatch(sampler, self.CALL_TIMEOUT)

        self.assertCallSequence(
            conn.__calls__,
            ['getAllDomainStats', 'domainListGetStats', 'domainListGetStats']
        )

    @pytest.mark.slow
    def test_slow_collect_while_vm_unresponsive(self):
        vms = make_vms(num=3)
        conn = FakeConnection(vms=vms)
        cache = FakeStatsCache()

        sampler = sampling.VMBulkstatsMonitor(conn, conn.getVMs, cache)

        with conn.stuck(self.TIMEOUT * 2):
            with cache.await_completion(self.TIMEOUT):
                self.exc.dispatch(sampler, self.CALL_TIMEOUT)
                vms['1'].ready = False
                self.exc.dispatch(sampler, self.CALL_TIMEOUT)
            # now we succesfully waited_for the second (slow) call:
            # call #1 (fast) recorded, not yet completed
            # call #2 (slow) recorded, completed, waited
            # now we need to be able to wait for the still pending call,
            # hence we re-prepare to wait
            cache.clear()

        # so we check indeed we recorded the right calls.
        # the call #1 (fast) may complete any moment, asynchronously
        expected = ['getAllDomainStats', 'domainListGetStats']
        self.assertCallSequence(conn.__calls__, expected)
        # now we make sure the call #1 (fast) is completed.
        # we expect NOT to wait here, timeout added just in case
        assert (cache.sync.wait(self.TIMEOUT))

        # reset fake environment to pristine state
        vms['1'].ready = True
        sampler._skip_doms.clear()

        expected.append('getAllDomainStats')
        with cache.await_completion(self.TIMEOUT):
            self.exc.dispatch(sampler, self.CALL_TIMEOUT)

        self.assertCallSequence(conn.__calls__, expected)

    def assertCallSequence(self, actual_calls, expected_calls):
        for actual, expected in zip(actual_calls, expected_calls):
            # we don't care about the arguments
            assert actual[0] == expected
        assert len(actual_calls) == len(expected_calls)


class FakeStatsCache(object):
    def __init__(self, clock=monotonic_time):
        self.data = []
        self.clock = clock
        self.sync = threading.Event()
        self.expected = 1
        self._count = 0

    def put(self, bulk_stats, timestamp):
        self.data.append(CacheSample(bulk_stats, timestamp))
        self._count += 1
        if self._count >= self.expected:
            self.sync.set()

    # test utility methods

    def clear(self):
        self._count = 0
        self.sync.clear()

    @property
    def received(self):
        return self._count

    @contextlib.contextmanager
    def await_completion(self, timeout, expected=1):
        """
        waits for the *completion* of an expected
        number of calls, using the given timeout.
        """
        self.expected = expected
        self.clear()
        try:
            yield self
        finally:
            assert (self.sync.wait(timeout))


class FakeDomain(object):
    def __init__(self, name):
        self._name = name

    @property
    def dom(self):
        # Some code check the underlying domain's UUIDString().
        return self

    def UUIDString(self):
        # yes this is cheating
        return self._name


class FakeVM(object):
    def __init__(self, vmid):
        self.id = vmid
        self._dom = FakeDomain(vmid)
        self.ready = True

    def isDomainReadyForCommands(self):
        return self.ready


def make_vms(num=1):
    vms = {}
    for index in range(1, num + 1):
        vm = FakeVM(str(index))
        vms[vm.id] = vm
    return vms


class FakeConnection(object):
    def __init__(self, vms):
        self.vms = vms
        self._delay = 0
        self._block = threading.Event()
        self.__calls__ = []

    def getVMs(self):
        return self.vms

    @recorded
    def getAllDomainStats(self, stats=0, flags=0):
        if not self._block.wait(self._delay):
            return []
        return [
            (vm._dom, {
                'vmid': vm._dom.UUIDString()
            })
            for vm in self.vms.values()
        ]

    @recorded
    def domainListGetStats(self, doms, stats=0, flags=0):
        return [
            (dom, {
                'vmid': dom.UUIDString()
            })
            for dom in doms
            if dom.UUIDString() in self.vms
        ]

    # test utility methods

    def sleep(self, delay):
        self._delay = delay

    def prepare_block(self):
        self._delay = 0

    @property
    def sleeping(self):
        return self._delay > 0

    def wakeup(self):
        self.prepare_block()
        self._block.set()

    @contextlib.contextmanager
    def stuck(self, delay):
        self.sleep(delay)
        try:
            yield self
        finally:
            self.wakeup()
