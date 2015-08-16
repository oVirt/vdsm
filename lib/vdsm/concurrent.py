#
# Copyright 2015 Red Hat, Inc.
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
import threading
from collections import namedtuple

from . import utils


class Timeout(Exception):
    """ Raised when operation timed out """


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

    Example usage:

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
            deadline = utils.monotonic_time() + timeout
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
                now = utils.monotonic_time()
                if now >= deadline:
                    raise Timeout("Timeout waiting for barrier")
                self._cond.wait(deadline - now)
            else:
                self._cond.wait()

    def _change_state(self, state):
        self._state = state
        self._cond.notify_all()


Result = namedtuple("Result", ["succeeded", "value"])


def tmap(func, iterable):
    args = list(iterable)
    results = [None] * len(args)

    def worker(i, f, arg):
        try:
            results[i] = Result(True, f(arg))
        except Exception as e:
            results[i] = Result(False, e)

    threads = []
    for i, arg in enumerate(args):
        t = threading.Thread(target=worker, args=(i, func, arg))
        t.daemon = True
        t.start()
        threads.append(t)

    for t in threads:
        t.join()

    return results


def thread(func, args=(), name=None, daemon=True, logger=None):
    """
    Create a thread for runnning func with args.

    Arguments:

    func        Function to run in a new thread.

    args        Arguments to pass to func

    name        If set, set thread name.

    daemon      If True, create a daemon thread.

    logger      If set, unhandled exception will be logged on this logger.
                Otherwise the root logger will be used.
    """
    @utils.traceback(on=logger)
    def run():
        return func(*args)

    thread = threading.Thread(target=run, name=name)
    thread.daemon = daemon
    return thread
