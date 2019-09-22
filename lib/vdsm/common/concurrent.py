#
# Copyright 2015-2017 Red Hat, Inc.
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

from __future__ import absolute_import
from __future__ import division

import itertools
import logging
import sys
import threading
import traceback

from collections import namedtuple

from six.moves import queue

from vdsm.common import pthread
from vdsm.common import time


class Timeout(Exception):
    """ Raised when operation timed out """


class InvalidEvent(Exception):
    """
    Raised when waiting on invalided event.
    """


class Barrier(object):
    """
    A barrier is synchronizing number of threads specified when the barrier
    was created.

    A barrier is created in a "filling" state. In this state, all threads
    invoking wait() will block until count threads have entered the barrier, or
    the specified timeout has expired.

    When the last thread has entered the barrier, the barrier switch state to
    "draining", and all threads are woken up. When the last thread has left the
    barrier, it switch state to "filling" again.

    Threads trying to enter the barrier when it is in "draining" state will
    block until it switch state to "filling", and then wait again until the
    barrier is in "draining" state.

    This class is behaves mostly like pthread_barrier_wait():
    http://linux.die.net/man/3/pthread_barrier_wait.

    Unlike pthread_barrier_wait(), this class supports an optional timeout,
    ensuring that threads will not wait forever. However, if one thread timed
    out, all threads waiting on this stage will timeout, since threads are
    waiting to each other.

    Example usage::

        barrier = Barrier(5)

        def thread():
            barrier.wait()

        for i in range(4):
            threading.Thread(target=thread).start()

        # Will block until all threads started above are blocked on wait(). The
        # last thread calling wait() will wake up all waiting threads.
        barrier.wait()

    """
    FILLING = 0
    DRAINING = 1

    def __init__(self, count):
        """
        Create a barrier synchronizing count threads in FILLING state.

        Raises ValueError if count is less than one.
        """
        if count < 1:
            raise ValueError("Invalid count %d (expecting count >= 1)" % count)
        self._count = count
        self._waiting = 0
        self._state = self.FILLING
        self._cond = threading.Condition(threading.Lock())

    def wait(self, timeout=None):
        """
        Wait until count threads are waiting on this barrier or timeout has
        expired.

        Raises Timeout if specified timeout has expired.
        """
        if timeout is not None:
            deadline = time.monotonic_time() + timeout
        else:
            deadline = None

        with self._cond:
            self._wait_for_state(self.FILLING, deadline)
            self._waiting += 1
            try:
                if self._waiting == self._count:
                    self._change_state(self.DRAINING)
                else:
                    self._wait_for_state(self.DRAINING, deadline)
            finally:
                self._waiting -= 1
                if self._waiting == 0:
                    self._change_state(self.FILLING)

    def _wait_for_state(self, state, deadline):
        while self._state != state:
            if deadline is not None:
                now = time.monotonic_time()
                if now >= deadline:
                    raise Timeout("Timeout waiting for barrier")
                self._cond.wait(deadline - now)
            else:
                self._cond.wait()

    def _change_state(self, state):
        self._state = state
        self._cond.notify_all()


Result = namedtuple("Result", ["succeeded", "value"])

_tmap_count = itertools.count()


def tmap(func, values, max_workers=10, name=None):
    """
    Execute func with every value in values in a thread pool.

    To consume the results as soon as they available:

        for res in concurrent.tmap(func, values):
            print res

    To wait until all results are available:

        results = list(concurrent.tmap(func, values))

    Arguments:
        func (callable): callable object accepting one argument.
        values (iterable): iterable of func arguments.
        max_workers (int): maximum number of threads in the pool. Must be
            larger than 0.
        name (str): worker thread name prefix, "tmap-<count>" if not set.

    Return:
        Iterator of Result tuples; Result(True, result) if func succeeded or
        Result(False, exception) if func raised an exception.
    """
    # Implementation notes
    #
    # To simplify the implemention, we consume all values in the iterable
    # before starting the workers. If iterating values is slow, this will delay
    # processing.
    #
    # Processing the results may be slow, so we yield the results immediately
    # as they are available. This allows the caller to process the results
    # while we process the pending values.
    #
    # Finally, since we use daemon threads joining the worker threads is not
    # required, but it helps tests that monitor number of threads.

    if max_workers < 1:
        raise ValueError("max_workers {} < 1".format(max_workers))

    if name is None:
        name = "tmap-{}".format(next(_tmap_count))

    pending = queue.Queue()
    results = queue.Queue()

    def worker():
        while True:
            try:
                value = pending.get(block=False)
            except queue.Empty:
                break

            try:
                results.put(Result(True, func(value)))
            except Exception as e:
                results.put(Result(False, e))
                del e

            del value

    values = tuple(values)
    workers_needed = min(len(values), max_workers)

    for value in values:
        pending.put(value)

    workers = []

    for i in range(workers_needed):
        w = thread(worker, name="{}/{}".format(name, i))
        w.start()
        workers.append(w)

    for _ in values:
        yield results.get()

    for w in workers:
        w.join()


def thread(func, args=(), kwargs=None, name=None, daemon=True, log=None):
    """
    Create a thread for runnning func with args.

    Arguments:

    func        Function to run in a new thread.

    args        Arguments to pass to func

    kwargs      Keyword arguments to pass to func

    name        If set, set thread name.

    daemon      If True, create a daemon thread.

    log         If set, unhandled exception will be logged on this logger.
                Otherwise the root logger will be used.
    """
    if kwargs is None:
        kwargs = {}
    if log is None:
        log = logging.getLogger()

    def run():
        thread = threading.current_thread()
        try:
            log.debug("START thread %s (func=%s, args=%s, kwargs=%s)",
                      thread, func, args, kwargs)
            pthread.setname(thread.name[:15])
            ret = func(*args, **kwargs)
            log.debug("FINISH thread %s", thread)
            return ret
        except (SystemExit, KeyboardInterrupt) as e:
            # Unlikley, but not interesting.
            log.debug("FINISH thread %s (%s)", thread, e)
        except:
            log.exception("FINISH thread %s failed", thread)

    thread = threading.Thread(target=run, name=name)
    thread.daemon = daemon
    return thread


class ValidatingEvent(object):
    """
    Event that can be invalidated.

    This Event behaves like threading.Event, but allows failing current and
    future waiters by invalidating the event.

    Waiters will raise immediately InvalidEvent exception if the event was
    invalid when calling wait(), or it was invalidated during wait().

    Based on Python 3 threading.Event.
    """

    def __init__(self):
        self._cond = threading.Condition(threading.Lock())
        self._flag = False
        self._valid = True

    def is_set(self):
        with self._cond:
            return self._flag

    def set(self):
        """
        Set the internal flag to true.

        All threads waiting for the flag to become true are awakened. Threads
        that call wait() once the flag is true will not block at all.
        """
        with self._cond:
            self._flag = True
            self._cond.notify_all()

    def clear(self):
        """
        Reset the internal flag to false.

        Subsequently, threads calling wait() will block until set() is called
        to set the internal flag to true again.
        """
        with self._cond:
            self._flag = False

    def wait(self, timeout=None):
        """
        Block until the internal flag is true.

        If the internal flag is true on entry, return immediately. Otherwise,
        block until another thread calls set() to set the flag to true, or
        until the optional timeout occurs.

        When the timeout argument is present and not None, it should be a
        floating point number specifying a timeout for the operation in seconds
        (or fractions thereof).

        This method returns the internal flag on exit, so it will always return
        True except if a timeout is given and the operation times out.

        If the event is invalid when calling wait, or was invalidted during the
        wait, raise InvalidEvent.
        """
        with self._cond:
            if not self._valid:
                raise InvalidEvent
            if not self._flag:
                self._cond.wait(timeout)
                if not self._valid:
                    raise InvalidEvent
            return self._flag

    @property
    def valid(self):
        """
        Return event validity.
        """
        with self._cond:
            return self._valid

    @valid.setter
    def valid(self, value):
        """
        Change event validity.

        Invalidating and event will wake up and raise InvalidEvent in all
        waiting threads.
        """
        with self._cond:
            wake_up = self._valid and not value
            self._valid = value
            if wake_up:
                self._cond.notify_all()


def format_traceback(ident):
    """
    Return thread traceback by ident (thread identifier).
    """
    stack = sys._current_frames()[ident]

    lines = []
    for filename, lineno, name, line in traceback.extract_stack(stack):
        lines.append('File: "%s", line %d, in %s' %
                     (filename, lineno, name))
        if line:
            lines.append("  %s" % (line.strip()))
    return '\n'.join(lines)
