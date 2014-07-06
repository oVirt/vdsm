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

"""
This module provides a Scheduler class scheduling execution of
a callable on a background thread.

To use a scheduler, create an instance and start it:

    scheduler = schedule.Scheduler()
    scheduler.start()

The scheduler default clock is time.time. This clock is not monotonic, which
may cause scheduled calls to fire too early or too late if the system time is
modified by the administrator or by ntp service. If you care about this and can
live with utils.monotonic_time's lower resolution, you can use it as the clock.

    scheduler = schedule.Scheduler(clock=utils.monotonic_time)

When you want to schedule some callable:

    def task():
        print '30 seconds passed'

    scheduler.schedule(30.0, task)

Task will be called after 30.0 seconds on the scheduler background thread.

If you need to cancel a scheduled call, keep the ScheduledCall object returned
from Scheduler.schedule(), and cancel the task:

    scheduled_call = scheduler.schedule(30.0, call)
    ...
    scheduled_call.cancel()

Finally, when the scheduler is not needed any more:

    scheduler.stop()

This will cancel any pending calls and terminate the scheduler thread.
"""

import heapq
import logging
import threading
import time

from . import utils


class Scheduler(object):
    """
    Schedule calls for future execution in a background thread.

    This class is thread safe; multiple threads can schedule calls or cancel
    the scheduler.
    """

    DEFAULT_DELAY = 30.0  # Used if no call are scheduled

    _log = logging.getLogger("Scheduler")

    def __init__(self, name="Scheduler", clock=time.time):
        """
        Initialize a scheduler.

        Arguments:
          name      Used as sheculer thread name
          clock     Callable returning current time (defualt time.time)
        """
        self._name = name
        self._clock = clock
        self._cond = threading.Condition(threading.Lock())
        self._running = False
        self._calls = []
        self._thread = threading.Thread(target=self._run, name=self._name)
        self._thread.daemon = True

    def start(self):
        self._log.debug("Starting scheduler %s", self._name)
        with self._cond:
            if self._running:
                raise AssertionError("Scheduler already running")
            self._running = True
            self._thread.start()

    def stop(self, wait=False):
        """
        Cancel all scheduled calls and stop the scheduler. Scheduling calls
        after the scheduler was stopped will raise AssertionError.
        """
        self._log.debug("Stopping scheduler %s", self._name)
        with self._cond:
            self._running = False
            self._cond.notify()
        if wait:
            self._thread.join()

    def schedule(self, delay, callable):
        """
        Schedule callable to be called after delay seconds on the scheduler
        thread.

        Callable must not block or take excessive time to complete. If it does
        not finish quickly, it may delay other scheduled calls on the scheduler
        thread.

        Returns a ScheduledCall that may be canceled if callable was not called
        yet.
        """
        deadline = self._clock() + delay
        call = ScheduledCall(callable)
        with self._cond:
            if not self._running:
                raise AssertionError("Scheduler not running")
            heapq.heappush(self._calls, (deadline, call))
            next_call = self._calls[0][1]
            if next_call is call:
                self._cond.notify()
        return call

    @utils.traceback(on=_log.name)
    def _run(self):
        self._log.debug("started")
        try:
            self._loop()
            self._log.debug("stopped")
        finally:
            self._cancel_calls()

    def _loop(self):
        while True:
            with self._cond:
                if not self._running:
                    return
                delay = self._time_until_deadline()
                if delay > 0.0:
                    self._cond.wait(delay)
                    if not self._running:
                        return
                expired = self._pop_expired_calls()
            for call in expired:
                call._execute()

    def _time_until_deadline(self):
        if len(self._calls) > 0:
            next_deadline = self._calls[0][0]
            return next_deadline - self._clock()
        return self.DEFAULT_DELAY

    def _pop_expired_calls(self):
        now = self._clock()
        expired = []
        while len(self._calls) > 0:
            deadline, call = self._calls[0]
            if deadline > now:
                break
            heapq.heappop(self._calls)
            if call.valid():
                expired.append(call)
        return expired

    def _cancel_calls(self):
        # Help the garbage collector by breaking reference cycles
        with self._cond:
            for deadline, call in self._calls:
                call.cancel()


class ScheduledCall(object):
    """
    Returned when a callable is scheduled. The caller may cancel the call if it
    was not called yet.

    This class is thread safe; any thread can cancel a call.
    """

    __slots__ = ('_callable',)

    _log = logging.getLogger("Scheduler")

    def __init__(self, callable):
        self._callable = callable

    def cancel(self):
        self._callable = _INVALID

    def valid(self):
        return self._callable is not _INVALID

    def _execute(self):
        try:
            self._callable()
        except Exception:
            self._log.exception("Unhandled exception in %s", self._callable)
        finally:
            self._callable = _INVALID


# Sentinel for marking calls as invalid. Callable so we can invalidate a call
# in a thread safe manner without locks.
def _INVALID():
    pass
