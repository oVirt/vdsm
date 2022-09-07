# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import logging
import threading
import time

from vdsm import executor
from vdsm import schedule
from vdsm import utils
from vdsm.common import concurrent
from vdsm.common import exception
from vdsm.common import pthread

from fakelib import FakeLogger
from testValidation import slowtest
from testlib import VdsmTestCase as TestCaseBase


class ExecutorTests(TestCaseBase):

    def setUp(self):
        self.scheduler = schedule.Scheduler()
        self.scheduler.start()
        self.max_tasks = 20
        self.max_workers = 15
        self.executor = executor.Executor('test',
                                          workers_count=10,
                                          max_tasks=self.max_tasks,
                                          scheduler=self.scheduler,
                                          max_workers=self.max_workers)
        self.executor.start()
        time.sleep(0.1)  # Give time to start all threads

    def tearDown(self):
        self.executor.stop()
        self.scheduler.stop()

    def test_repr_defaults(self):
        # we are using the kwargs syntax, but we are omitting arguments
        # with default values - thus using their defaults.
        exc = executor.Executor('test',
                                workers_count=10,
                                max_tasks=self.max_tasks,
                                scheduler=self.scheduler)
        self.assertTrue(repr(exc))

    def test_dispatch_not_running(self):
        self.executor.stop()
        self.assertRaises(executor.NotRunning,
                          self.executor.dispatch,
                          Task())

    def test_start_twice(self):
        self.assertRaises(executor.AlreadyStarted,
                          self.executor.start)

    def test_dispatch(self):
        task = Task()
        self.executor.dispatch(task)
        task.executed.wait(0.1)
        self.assertTrue(task.executed.is_set())

    def test_dispatch_after_fault(self):
        faulty_task = Task(error=RuntimeError("fake error"))
        self.executor.dispatch(faulty_task)
        faulty_task.executed.wait(0.1)
        task = Task()
        self.executor.dispatch(task)
        task.executed.wait(0.1)
        self.assertTrue(task.executed.is_set())

    @slowtest
    def test_dispatch_with_timeout(self):
        task = Task(wait=0.2)
        self.executor.dispatch(task, 0.1)
        task.executed.wait(0.3)
        self.assertTrue(task.executed.is_set())  # task must have executed!

    def test_too_many_tasks(self):
        tasks = [Task(wait=0.1) for n in range(31)]
        with self.assertRaises(exception.ResourceExhausted):
            for task in tasks:
                self.executor.dispatch(task)

    @slowtest
    def test_concurrency(self):
        tasks = [Task(wait=0.1) for n in range(20)]
        for task in tasks:
            self.executor.dispatch(task, 1.0)
        time.sleep(0.3)
        for task in tasks:
            self.assertTrue(task.executed.is_set())

    @slowtest
    def test_blocked_workers(self):
        slow_tasks = [Task(wait=0.4) for n in range(5)]
        for task in slow_tasks:
            self.executor.dispatch(task, 1.0)
        time.sleep(0.1)
        # Slow tasks block 5 of the workers, so these tasks should finished
        # after (20 * 0.1) / 5 seconds.
        tasks = [Task(wait=0.1) for n in range(20)]
        for task in tasks:
            self.executor.dispatch(task, 1.0)
        time.sleep(0.5)
        for task in tasks:
            self.assertTrue(task.executed.is_set())
        for task in slow_tasks:
            self.assertTrue(task.executed.is_set())

    @slowtest
    def test_discarded_workers(self):
        n_slow_tasks = 10
        barrier = concurrent.Barrier(n_slow_tasks + 1)
        slow_tasks = [Task(start_barrier=barrier) for n in range(n_slow_tasks)]
        for task in slow_tasks:
            self.executor.dispatch(task, 0.1)
        # All workers are blocked on slow tasks
        time.sleep(0.1)
        # Blocked workers should be replaced with new workers
        tasks = [Task(wait=0.1) for n in range(20)]
        for task in tasks:
            self.executor.dispatch(task, 1.0)
        self.assertFalse([t for t in tasks if not task.executed.wait(1)])
        barrier.wait(timeout=3)
        self.assertFalse([t for t in slow_tasks if not task.executed.wait(1)])
        # Discarded workers should exit, executor should operate normally
        tasks = [Task(wait=0.1) for n in range(20)]
        for task in tasks:
            self.executor.dispatch(task, 1.0)
        self.assertFalse([t for t in tasks if not task.executed.wait(1)])

    @slowtest
    def test_max_workers(self):
        limit = self.max_workers
        blocked_forever = threading.Event()
        blocked = threading.Event()

        try:
            # Fill the executor with stuck tasks, one of them can be unblocked
            # later
            start_barrier = concurrent.Barrier(limit + 1)
            tasks = [Task(event=blocked_forever, start_barrier=start_barrier)
                     for n in range(limit - 1)]
            tasks.append(Task(event=blocked, start_barrier=start_barrier))
            for t in tasks:
                self.executor.dispatch(t, 0)
            # Wait until all tasks are started, i.e. the executor reaches its
            # maximum number of workers
            start_barrier.wait(timeout=3)

            # Try to run new tasks on the executor now, when the maximum number
            # of workers is reached
            n_extra_tasks = 2
            extra_tasks = [Task() for i in range(n_extra_tasks)]
            for t in extra_tasks:
                self.executor.dispatch(t, 0)

            # Check that none of the new tasks got executed (the number of the
            # executor workers is at the maximum limit, so nothing more may be
            # run)
            self.assertEqual([t for t in extra_tasks if t.executed.wait(1)],
                             [])

            # Unblock one of the tasks and check the new tasks run
            blocked.set()
            # The last task, the only unblocked one, should be executed now
            self.assertTrue(tasks[-1].executed.wait(1))

            # The other tasks shouldn't be unblocked and executed, let's check
            # things go as expected before proceeding (however we don't want to
            # stop and wait on each of the tasks, the first one is enough)
            self.assertFalse(tasks[0].executed.wait(1))
            self.assertEqual([t for t in tasks if t.executed.is_set()],
                             [tasks[-1]])

            # Extra tasks are not blocking, they were blocked just by the
            # overflown executor, so they should be all executed now when there
            # is one free worker
            self.assertEqual([t for t in extra_tasks
                             if not t.executed.wait(1)],
                             [])

        finally:
            # Cleanup: Finish all the executor jobs
            blocked.set()
            blocked_forever.set()

    @slowtest
    def test_max_workers_many_tasks(self):
        # Check we don't get ResourceExhausted exception after reaching
        # the limit on the total number of workers if TaskQueue is not full.

        blocked = threading.Event()
        barrier = concurrent.Barrier(self.max_workers + 1)

        try:
            # Exhaust workers
            for i in range(self.max_workers):
                task = Task(event=blocked, start_barrier=barrier)
                self.executor.dispatch(task, 0)
            barrier.wait(3)

            # Task queue should accept further tasks up to its capacity
            for i in range(self.max_tasks):
                self.executor.dispatch(Task(), 0)

            # Check we did what we intended -- the next task shouldn't be
            # accepted
            self.assertRaises(exception.ResourceExhausted,
                              self.executor.dispatch, Task(), 0)

        finally:
            # Cleanup: Finish all the executor jobs
            blocked.set()

    @slowtest
    def test_report_blocked_workers(self):
        REPORT_PERIOD = 1.0  # seconds
        WAIT = 10.0  # seconds
        WORKERS = 3
        log = FakeLogger(level=logging.DEBUG)

        self.executor = executor.Executor('test',
                                          workers_count=10,
                                          max_tasks=self.max_tasks,
                                          scheduler=self.scheduler,
                                          max_workers=self.max_workers,
                                          log=log)
        self.executor.start()
        time.sleep(0.1)  # Give time to start all threads

        # make sure we have plenty of slow tasks
        slow_tasks = [Task(wait=WAIT) for n in range(WORKERS * 2)]
        for task in slow_tasks:
            # and also make sure to discard workers
            self.executor.dispatch(task, 1.0, discard=False)
        # we want to catch at least one report
        time.sleep(REPORT_PERIOD * 2)

        print(log.messages)  # troubleshooting aid when test fails
        self.assertTrue(any(
            text.startswith('Worker blocked')
            for (level, text, _) in log.messages))


class TestWorkerSystemNames(TestCaseBase):

    def test_worker_thread_system_name(self):
        names = []
        workers = 2
        done = concurrent.Barrier(workers + 1)

        def get_worker_name():
            names.append(pthread.getname())
            done.wait()

        foo = executor.Executor('foo', workers, workers, None)
        with utils.running(foo):
            for i in range(workers):
                foo.dispatch(get_worker_name)
            done.wait()

        self.assertEqual(sorted(names), ["foo/0", "foo/1"])

    def test_multiple_executors(self):
        names = []
        workers = 2
        done = concurrent.Barrier(2 * workers + 1)

        def get_worker_name():
            names.append(pthread.getname())
            done.wait()

        foo = executor.Executor('foo', workers, workers, None)
        bar = executor.Executor('bar', workers, workers, None)
        with utils.running(foo), utils.running(bar):
            for i in range(workers):
                foo.dispatch(get_worker_name)
                bar.dispatch(get_worker_name)
            done.wait()

        self.assertEqual(sorted(names),
                         ["bar/0", "bar/1", "foo/0", "foo/1"])


class ExecutorTaskTests(TestCaseBase):

    def test_duration_none_if_not_called(self):
        task = executor.Task(lambda: None, None)
        self.assertIs(task.duration, 0)

    def test_duration_increases(self):
        STEP = 0.1
        TIMES = 3
        task = executor.Task(lambda: None, None)
        task()
        for i in range(TIMES):
            time.sleep(STEP)
            self.assertGreaterEqual(task.duration, i * STEP)

    def test_repr_timeout(self):
        # temporaries only for readability
        timeout = None
        discard = True
        task = executor.Task(lambda: None, timeout, discard)
        msg = repr(task)
        self.assertTrue(msg.startswith('<Task discardable'))


class Task(object):

    def __init__(self, wait=None, error=None, event=None, start_barrier=None):
        self.wait = wait
        self.error = error
        self.event = event
        self.start_barrier = start_barrier
        self.started = threading.Event()
        self.executed = threading.Event()

    def __call__(self):
        self.started.set()
        if self.start_barrier is not None:
            self.start_barrier.wait(10)
        if self.wait:
            time.sleep(self.wait)
        if self.event is not None:
            if not self.event.wait(10):
                return
        self.executed.set()
        if self.error:
            raise self.error

    def __repr__(self):
        return ('<Task; started=%s, executed=%s>' %
                (self.started.is_set(), self.executed.is_set(),))
