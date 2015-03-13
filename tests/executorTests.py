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
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA
#
# Refer to the README and COPYING files for full details of the license
#

import threading
import time

from vdsm import executor
from vdsm import schedule

from testValidation import slowtest
from testlib import VdsmTestCase as TestCaseBase


class ExecutorTests(TestCaseBase):

    def setUp(self):
        self.scheduler = schedule.Scheduler()
        self.scheduler.start()
        self.executor = executor.Executor('test',
                                          workers_count=10, max_tasks=20,
                                          scheduler=self.scheduler)
        self.executor.start()
        time.sleep(0.1)  # Give time to start all threads

    def tearDown(self):
        self.executor.stop()
        self.scheduler.stop()

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
        with self.assertRaises(executor.TooManyTasks):
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
        # Slow tasks block half of the workers
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
        slow_tasks = [Task(wait=0.4) for n in range(10)]
        for task in slow_tasks:
            self.executor.dispatch(task, 0.1)
        # All workers are blocked on slow tasks
        time.sleep(0.1)
        # Blocked workers should be replaced with new workers
        tasks = [Task(wait=0.1) for n in range(20)]
        for task in tasks:
            self.executor.dispatch(task, 1.0)
        time.sleep(0.3)
        for task in tasks:
            self.assertTrue(task.executed.is_set())
        for task in slow_tasks:
            self.assertTrue(task.executed.is_set())
        # Discarded workers should exit, executor should operate normally
        tasks = [Task(wait=0.1) for n in range(20)]
        for task in tasks:
            self.executor.dispatch(task, 1.0)
        time.sleep(0.3)
        for task in tasks:
            self.assertTrue(task.executed.is_set())


class Task(object):

    def __init__(self, wait=None, error=None):
        self.wait = wait
        self.error = error
        self.executed = threading.Event()

    def __call__(self):
        if self.wait:
            time.sleep(self.wait)
        self.executed.set()
        if self.error:
            raise self.error
