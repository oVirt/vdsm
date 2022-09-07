# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import
from __future__ import division

from __future__ import print_function
import threading
import time

import vdsm.common.time
from vdsm import schedule
from testlib import VdsmTestCase
from testValidation import broken_on_ci
from testValidation import stresstest
from testlib import permutations, expandPermutations


@expandPermutations
class SchedulerTests(VdsmTestCase):

    # Time  to wait for completion, so test will not fail on overloaded
    # machines. If tests fails on CI, increase this value.
    GRACETIME = 0.1

    MAX_TASKS = 1000
    PERMUTATIONS = ((time.time,), (vdsm.common.time.monotonic_time,))

    def setUp(self):
        self.scheduler = None

    def tearDown(self):
        if self.scheduler:
            self.scheduler.stop(wait=True)

    @broken_on_ci("timing sensitive, may fail on overloaded machine")
    @permutations(PERMUTATIONS)
    def test_schedule_after(self, clock):
        self.create_scheduler(clock)
        delay = 0.3
        task1 = Task(clock)
        task2 = Task(clock)
        deadline = self.clock() + delay
        self.scheduler.schedule(delay, task1)
        self.scheduler.schedule(delay + 1, task2)
        task1.wait(delay + self.GRACETIME)
        self.assertTrue(deadline <= task1.call_time)
        self.assertTrue(task1.call_time < deadline + self.GRACETIME)
        self.assertEqual(task2.call_time, None)

    @broken_on_ci("timing sensitive, may fail on overloaded machine")
    @permutations(PERMUTATIONS)
    def test_schedule_before(self, clock):
        self.create_scheduler(clock)
        delay = 0.3
        task1 = Task(clock)
        task2 = Task(clock)
        deadline = self.clock() + delay
        self.scheduler.schedule(delay + 1, task2)
        self.scheduler.schedule(delay, task1)
        task1.wait(delay + self.GRACETIME)
        self.assertTrue(deadline <= task1.call_time)
        self.assertTrue(task1.call_time < deadline + self.GRACETIME)
        self.assertEqual(task2.call_time, None)

    @broken_on_ci("timing sensitive, may fail on overloaded machine")
    @permutations(PERMUTATIONS)
    def test_continue_after_failures(self, clock):
        self.create_scheduler(clock)
        self.scheduler.schedule(0.3, FailingTask())
        task = Task(clock)
        self.scheduler.schedule(0.4, task)
        task.wait(0.4 + self.GRACETIME)
        self.assertTrue(task.call_time is not None)

    @permutations(PERMUTATIONS)
    def test_cancel_call(self, clock):
        self.create_scheduler(clock)
        delay = 0.3
        task = Task(clock)
        call = self.scheduler.schedule(delay, task)
        self.assertTrue(call.valid())
        call.cancel()
        self.assertFalse(call.valid())
        task.wait(delay + self.GRACETIME)
        self.assertEqual(task.call_time, None)

    @stresstest
    @permutations(PERMUTATIONS)
    def test_cancel_call_many(self, clock):
        self.create_scheduler(clock)
        delay = 0.3
        tasks = []
        for i in range(self.MAX_TASKS):
            task = Task(clock)
            call = self.scheduler.schedule(delay, task)
            tasks.append((task, call))
        for task, call in tasks:
            call.cancel()
        last_task = tasks[-1][0]
        last_task.wait(delay + self.GRACETIME)
        for task, call in tasks:
            self.assertEqual(task.call_time, None)

    @permutations(PERMUTATIONS)
    def test_stop_scheduler(self, clock):
        self.create_scheduler(clock)
        delay = 0.3
        task = Task(clock)
        self.scheduler.schedule(delay, task)
        self.scheduler.stop()
        task.wait(delay + self.GRACETIME)
        self.assertEqual(task.call_time, None)

    @stresstest
    @permutations(PERMUTATIONS)
    def test_stop_scheduler_many(self, clock):
        self.create_scheduler(clock)
        delay = 0.3
        tasks = []
        for i in range(self.MAX_TASKS):
            task = Task(clock)
            call = self.scheduler.schedule(delay, task)
            tasks.append((task, call))
        self.scheduler.stop()
        last_task = tasks[-1][0]
        last_task.wait(delay + self.GRACETIME)
        for task, call in tasks:
            self.assertEqual(task.call_time, None)

    @stresstest
    @permutations(PERMUTATIONS)
    def test_latency(self, clock):
        # Test how the scheduler cope with load of 1000 calls per seconds.
        # This is not the typical use but it is interesting to see how good we
        # can do this. This may also reveal bad changes to the scheduler code
        # that otherwise may be hidden in the noise.
        self.create_scheduler(clock)
        interval = 1.0
        tickers = []
        for i in range(self.MAX_TASKS):
            ticker = Ticker(self.scheduler, interval, clock)
            tickers.append(ticker)
        time.sleep(10)
        for ticker in tickers:
            ticker.stop()
            ticker.latency.sort()
            min = ticker.latency[0]
            avg = sum(ticker.latency) / len(ticker.latency)
            med = ticker.latency[len(ticker.latency) // 2]
            max = ticker.latency[-1]
            print('latency - avg: %.3f min: %.3f median: %.3f max: %.3f' % (
                avg, min, med, max))
            # This may be too strict on overloaded machines. We may need to
            # increase this value if it breaks in the CI.  On my laptop I get
            # avg latency 1 millisecond.
            self.assertTrue(max < 0.1)

    # Helpers

    def create_scheduler(self, clock):
        self.clock = clock
        self.scheduler = schedule.Scheduler(clock=clock)
        self.scheduler.start()


class Task(object):

    def __init__(self, clock):
        self.clock = clock
        self.cond = threading.Condition(threading.Lock())
        self.call_time = None

    def __call__(self):
        with self.cond:
            self.call_time = self.clock()
            self.cond.notify()

    def wait(self, timeout):
        with self.cond:
            if self.call_time is None:
                self.cond.wait(timeout)


class Ticker(object):

    def __init__(self, scheduler, interval, clock):
        self.scheduler = scheduler
        self.interval = interval
        self.clock = clock
        self.latency = []
        self.running = True
        self.last = self.clock()
        self.scheduler.schedule(self.interval, self.tick)

    def stop(self):
        self.running = False

    def tick(self):
        if self.running:
            now = self.clock()
            self.latency.append(now - self.last - self.interval)
            self.last = now
            self.scheduler.schedule(self.interval, self.tick)


class FailingTask(object):

    def __call__(self):
        raise Exception("This task is broken")


class TestScheduledCall(VdsmTestCase):

    def setUp(self):
        self.count = 0

    def callback(self):
        self.count += 1

    def test_create(self):
        call = schedule.ScheduledCall(0, self.callback)
        self.assertEqual(0, self.count)
        self.assertTrue(call.valid())

    def test_execute(self):
        call = schedule.ScheduledCall(0, self.callback)
        call._execute()
        self.assertEqual(1, self.count)
        self.assertFalse(call.valid())

    def test_execute_callback_once(self):
        call = schedule.ScheduledCall(0, self.callback)
        call._execute()
        call._execute()
        self.assertEqual(1, self.count)

    def test_order(self):
        now = vdsm.common.time.monotonic_time()
        call_soon = schedule.ScheduledCall(now, self.callback)
        call_later = schedule.ScheduledCall(now + 1, self.callback)
        self.assertLess(call_soon, call_later)
