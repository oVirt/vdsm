# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import
from __future__ import division

from contextlib import contextmanager

from vdsm.storage import threadPool

from . storagetestlib import Callable

# Fail tests if callable is hang for longer time.
HANG_TIMEOUT = 5


@contextmanager
def thread_pool(workers, wait_timeout=0.05):
    tp = threadPool.ThreadPool("name", workers, waitTimeout=wait_timeout)
    yield tp
    # TODO: Way to abort running tasks left by broken tests would be useful.
    tp.joinAll(waitForThreads=True)


def test_empty():
    with thread_pool(1):
        # No interesting behaviour to test, just ensure that stopping empty
        # pool does not fail.
        pass


def test_queue_task():
    with thread_pool(1) as tp:
        c = Callable()
        tp.queueTask("id", c)
        # Raises Timeout if not called.
        c.wait_until_running(timeout=1)


def test_queue_task_with_args():
    with thread_pool(1) as tp:
        args = (1, 2)
        c = Callable()
        tp.queueTask("id", c, args=args)
        # Raises Timeout if not called.
        c.wait_until_running(timeout=1)
        assert c.args == args


def test_queue_many_tasks():
    running = []
    queued = []
    workers = 10
    with thread_pool(workers) as tp:
        try:
            # These tasks should run.
            for i in range(workers):
                c = Callable(hang_timeout=HANG_TIMEOUT)
                tp.queueTask("running-{}".format(i), c)
                running.append(c)

            # These tasks should be queued.
            for i in range(workers):
                c = Callable(hang_timeout=HANG_TIMEOUT)
                tp.queueTask("queued-{}".format(i), c)
                queued.append(c)

            # Wait until running tasks start.
            for c in running:
                c.wait_until_running(timeout=1)

            # Queued tasks should not run yet.
            for c in queued:
                assert not c.was_called()

            # Finish running tasks.
            for c in running:
                c.finish()

            # Queued tasks should run now.
            for c in queued:
                c.wait_until_running(timeout=1)
        finally:
            for c in running + queued:
                c.finish()


def test_failing_task():
    with thread_pool(1) as tp:
        tasks = []

        # These tasks will fail, without affecting the next tasks.
        for i in range(2):
            c = Callable(result=RuntimeError("no task for you!"))
            tp.queueTask("failure-{}".format(i), c)
            tasks.append(c)

        # These tasks should succeed.
        for i in range(2):
            c = Callable()
            tp.queueTask("success-{}".format(i), c)
            tasks.append(c)

        # Wait until all tasks are running.
        for c in tasks:
            c.wait_until_running(timeout=1)
