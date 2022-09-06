# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import
"""Threaded based executor.
Blocked tasks may be discarded, and the worker pool is automatically
replenished."""

import collections
import functools
import logging
import threading

from vdsm.common import concurrent
from vdsm.common import exception
from vdsm.common import time


class NotRunning(Exception):
    """Executor not yet started or shutting down."""


class AlreadyStarted(Exception):
    """Executor started multiple times."""


class Executor(object):
    """
    Executes potentially blocking task into background
    threads. Can replace stuck threads with fresh ones.

    It works as follows:

    - Newly added tasks (see `dispatch()` method) are put into the executor's
      task queue.  The maximum length of the queue is set with `max_tasks`
      constructor parameter.

    - There are workers (threads) running concurrently and taking the requests
      from the queue.  The initial number of the workers is set with
      `workers_count` parameter.

    - Each of the workers waits a certain amount of time for the completion of
      any processed task.  If the time is exceeded then the worker continues
      processing the task but another worker is created not to limit
      availability of task processing.  When the original worker finishes
      executing the long task, it's finished.

    - However there is a limit on the total number of the workers in the
      executor, set with `max_workers`.  If the limit is reached, no new
      workers are created and the whole processing may get stuck until some of
      the stuck task finishes.  This prevents creating an excessive number
      of threads when many tasks are stuck.

    """
    _log = logging.getLogger('Executor')

    def __init__(self, name, workers_count, max_tasks, scheduler,
                 max_workers=None, log=None):
        """
        :param name: Name of the executor; no special purpose, just for
          logging and debugging.
        :type name: basestring
        :param workers_count: Number of workers (threads) processing the tasks
          that are added to the executor (via `dispatch()` method).
        :type workers_count: int
        :param max_tasks: Maximum number of tasks waiting for execution in the
          executor's task queue.
        :type max_tasks: int
        :param scheduler: Scheduler passed to _Worker instances to set the
          maximum time to wait for their completion.
        :type scheduler: `Scheduler` instance
        :param max_workers: Maximum number of workers.  If some tasks get
          stuck during their processing, the corresponding workers are put
          aside and new workers ready for processing are created.  This
          parameter limits the total number of the workers (threads), including
          both those ready for processing and those hanging on stuck tasks.  If
          it is not None and it gets reached then no further workers are
          created.
        :type max_workers: int or None
        :param log: logger instance to override the default logger. This is
          useful for testing
        :type log: logger as returned by logging.getLogger()

        """
        self._name = name
        self._workers_count = workers_count
        self._max_workers = max_workers
        self._worker_id = 0
        self._tasks = TaskQueue(name, max_tasks)
        self._scheduler = scheduler
        if log is not None:
            self._log = log
        self._workers = set()
        self._lock = threading.Lock()
        self._running = False

    def __repr__(self):
        return "<Executor %s workers=%d max_workers=%s %s at 0x%x>" % (
            self._name,
            self._workers_count,
            self._max_workers,  # either None or int
            repr(self._tasks),
            id(self)
        )

    @property
    def name(self):
        return self._name

    def start(self):
        self._log.debug('Starting executor')
        with self._lock:
            if self._running:
                raise AlreadyStarted()
            self._running = True
            for _ in range(self._workers_count):
                self._add_worker()

    def stop(self, wait=True):
        self._log.debug('Stopping executor')
        with self._lock:
            self._running = False
            self._tasks.clear()
            for _ in range(self._workers_count):
                self._tasks.put(_STOP)
            workers = tuple(self._workers) if wait else ()
        for worker in workers:
            worker.join()

    def dispatch(self, callable, timeout=None, discard=True):
        """
        Dispatches a new task to the executor.

        The task may be any callable.
        The task will be executed as soon as possible
        in one of the active workers of the executor.

        :param callable: callable to be run in the executor,
          as soon as possible but still under best-effort service level
        :type callable: any callable python object. Will be called without
          any argument like_this().
        :param timeout: timeout in seconds for the callable to complete,
          measured from the time the callable is called.
        :type timeout: float
        :param discard: if true the worker thread will be discarded once
          the timeout expires and callable is not yet
          completed. Otherwise, the executor just checks if the callable
          completed, emits a warning in the log if it didn't complete,
          and reschedules the check after `timeout` seconds.
        :type discard: boolean
        """
        if not self._running:
            raise NotRunning()
        self._tasks.put(Task(callable, timeout, discard))

    # Serving workers

    @property
    def _active_workers(self):
        return len([w for w in tuple(self._workers) if not w.discarded])

    @property
    def _total_workers(self):
        return len(self._workers)

    def _may_add_workers(self):
        return (self._active_workers < self._workers_count and
                (self._max_workers is None or
                 self._total_workers < self._max_workers))

    def _worker_discarded(self, worker):
        """
        Called from scheduler thread when worker was discarded. The worker
        thread is blocked on a task, and will exit when the task finishes.

        .. note::
            This thread is different from `_worker_stopped()` thread (the
            worker thread) and the order of `_worker_discarded()` and
            `_worker_stopped()` execution may be arbitrary.
        """
        worker_added = False

        with self._lock:
            if not self._running:
                return
            if self._may_add_workers():
                self._add_worker()
                worker_added = True

        # intentionally done outside the lock
        if not worker_added:
            self._log.warning("Too many workers (limit=%s), not adding more",
                              self._max_workers)
        # this is a debug helper, it is not that important to be precise
        self._log.warning("executor state: count=%d workers=%s",
                          self._total_workers, self._workers)

    def _worker_stopped(self, worker):
        """
        Called from the worker thread before it exits.

        .. note::
            This thread is different from `_worker_discarded()` thread (the
            scheduler thread) and the order of `_worker_stopped()` and
            `_worker_discarded()` execution may be arbitrary.
        """
        worker_added = False

        with self._lock:
            self._workers.remove(worker)
            if not self._running:
                return
            if self._may_add_workers():
                self._add_worker()
                worker_added = True

        if worker_added:
            self._log.info("New worker added (%s active, %s total workers)",
                           self._active_workers, self._total_workers)

    def _next_task(self):
        """
        Called from the worker thread to get the next task from the task queue.
        Raises NotRunning exception if executor was stopped.
        """
        task = self._tasks.get()
        if task is _STOP:
            raise NotRunning()
        return task

    # Private

    def _add_worker(self):
        name = "%s/%d" % (self.name, self._worker_id)
        self._worker_id += 1
        worker = _Worker(self, self._scheduler, name, self._log)
        worker.start()
        self._workers.add(worker)


_STOP = object()


class _WorkerDiscarded(Exception):
    """ Raised if worker was discarded during execution of a task """


class _Worker(object):

    _log = logging.getLogger('Executor')

    def __init__(self, executor, scheduler, name, log=None):
        self._executor = executor
        self._scheduler = scheduler
        self._discarded = False
        self._task_counter = 0
        self._lock = threading.Lock()
        if log is not None:
            self._log = log
        self._thread = concurrent.thread(self._run, name=name, log=self._log)
        self._task = None
        self._scheduled_check = None

    @property
    def name(self):
        return self._thread.name

    def start(self):
        self._log.debug('Starting worker %s' % self.name)
        self._thread.start()

    def join(self):
        self._log.debug('Waiting for worker %s', self.name)
        self._thread.join()

    @property
    def discarded(self):
        return self._discarded

    def _run(self):
        self._log.debug('Worker started')
        try:
            while True:
                self._execute_task()
        except NotRunning:
            self._log.debug('Worker stopped')
        except _WorkerDiscarded:
            self._log.info('Worker was discarded')
        finally:
            self._executor._worker_stopped(self)

    def _execute_task(self):
        task = self._executor._next_task()
        with self._lock:
            self._scheduled_check = self._check_after(task.timeout)
        self._task = task
        try:
            task()
        except Exception:
            self._log.exception("Unhandled exception in %s", task)
        finally:
            self._task = None
            # We want to discard workers that were too slow to disarm
            # the timer. It does not matter if the thread was still
            # blocked on callable when we discard it or it just finished.
            # However, we expect that most of times only blocked threads
            # will be discarded.
            with self._lock:
                if self._scheduled_check is not None:
                    self._scheduled_check.cancel()
                    self._scheduled_check = None
                self._task_counter += 1
            if self._discarded:
                raise _WorkerDiscarded()

    def _check_after(self, timeout):
        if timeout is not None:
            check_task = functools.partial(
                self._check_task, self._task_counter)
            return self._scheduler.schedule(timeout, check_task)
        return None

    def _check_task(self, task_number):
        with self._lock:
            if task_number != self._task_counter:
                return
            if self._task.discard:
                if self._discarded:
                    raise AssertionError("Attempt to discard worker twice")
                self._discarded = True
            else:
                self._scheduled_check = self._check_after(self._task.timeout)
        if self._discarded:
            # Please make sure the executor call is performed outside the lock
            # -- there is another lock involved in the executor and we don't
            # want to fall into a deadlock incidentally.
            self._executor._worker_discarded(self)
            self._log.info("Worker discarded: %s", self)
        else:
            # we want to avoid to log with the lock held, so we do it here.
            try:
                trace = concurrent.format_traceback(self._thread.ident)
            except KeyError:
                trace = "(traceback not available)"
            self._log.warning("Worker blocked: %s, traceback:\n%s", self,
                              trace)

    def __repr__(self):
        return "<Worker name=%s %s%s task#=%s at 0x%x>" % (
            self.name,
            "running %s" % (self._task,) if self._task else "waiting",
            " discarded" if self._discarded else "",
            self._task_counter,
            id(self)
        )


class Task(object):

    def __init__(self, callable, timeout, discard=True):
        self._callable = callable
        self.timeout = timeout
        self.discard = discard
        self._start = None

    @property
    def duration(self):
        if self._start is None:
            return 0
        return time.monotonic_time() - self._start

    def __call__(self):
        self._start = time.monotonic_time()
        self._callable()

    def __repr__(self):
        return "<Task %s%s timeout=%s, duration=%.2f at 0x%x>" % (
            "discardable " if self.discard else "",
            self._callable,
            self.timeout,
            self.duration,
            id(self)
        )


class TaskQueue(object):
    """
    Replacement for Queue.Queue, with two important changes:

    * Queue.Queue blocks when full. We want to raise ResourceExhausted instead.
    * Queue.Queue lacks the clear() operation, which is needed to implement
      the 'poison pill' pattern (described for example in
      http://pymotw.com/2/multiprocessing/communication.html )
    """

    def __init__(self, name, max_tasks):
        """
        :param name: Name of the executor; no special purpose, just for
          logging and debugging.
        :type name: basestring
        :param max_tasks: Maximum number of tasks waiting for execution in the
          executor's task queue.
        :type max_tasks: int
        """
        self._name = name
        self._max_tasks = max_tasks
        self._tasks = collections.deque()
        # Deque supports thread-safe append and pop from both ends. We need
        # this condition for waking up threads waiting on an empty queue and
        # protecting other methods which are not documented as thread-safe.
        # https://docs.python.org/2/library/collections.html#deque-objects
        self._cond = threading.Condition(threading.Lock())

    def __repr__(self):
        return "<TaskQueue %s max_tasks=%i tasks(%i)=%s at 0x%x>" % (
            self._name,
            self._max_tasks,
            len(self._tasks),
            repr(self._tasks),
            id(self)
        )

    def put(self, task):
        """
        Put a new task in the queue.
        Do not block when full, raises ResourceExhausted instead.
        """
        with self._cond:
            if len(self._tasks) == self._max_tasks:
                raise exception.ResourceExhausted(
                    "Too many tasks",
                    resource=self._name,
                    current_tasks=self._max_tasks)
            self._tasks.append(task)
            self._cond.notify()

    def get(self):
        """
        Get a new task. Blocks if empty.
        """
        while True:
            try:
                return self._tasks.popleft()
            except IndexError:
                with self._cond:
                    if not self._tasks:
                        self._cond.wait()

    def clear(self):
        with self._cond:
            self._tasks.clear()
