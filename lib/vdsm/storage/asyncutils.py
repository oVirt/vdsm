# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Utilties for working with the event loop.
"""

from __future__ import absolute_import
import logging

log = logging.getLogger("storage.asyncutils")


class LoopingCall(object):
    """
    A simplified version of `twisted.internet.task.LoopingCall`.

    This class implements the common pattern of running a call every
    interval seconds.

    The callback will be invoked exactly every interval seconds since
    the looping call was started, unless the event loop was delayed, and
    missed some of the deadlines.

    The callback must not block, or it will delay the entire event loop.
    If you need to invoke a blocking operation, run the blocking
    operation in another thread.

    Example usage:

        lc = LoopingCall(loop, callback, arg1, arg2)
        lc.start(10)

    To stop the calls:

        lc.stop()

    Note: This class is not thread safe and will never be. All calls
    must be done from the event loop thread.  If you want to call from
    another thread, use EventLoop.call_soon_threadsafe().
    """

    # Threshold for warning about delayed callbacks. This can happen if the
    # event loop is too busy, or was blocked by bad callback. Both cases are
    # not expected and we should easlily detect them in the field.
    warning_delay = 0.5

    def __init__(self, loop, callback, *args):
        """
        Initialize a LoopingCall

        Arguments:
            loop (`storage.asyncevent.EventLoop`): The event loop that
                should run the looping call.
            callback (callable): A callable object
            *args: Arguments for the callback

        Note: A LoopingCall may be created only from the event loop
        thread.
        """
        self._loop = loop
        self._callback = callback
        self._args = args
        self._running = False
        self._deadline = None
        self._interval = None
        self._timer = None

    def start(self, interval):
        """
        Start a LoopingCall

        Arguments:
            interval (float): Interval to call looping call callback.

        The first call is performed immediately. To delay the first
        call, use EventLoop.call_after():

            loop.call_after(10, lc.start, 10)

        Then, every interval seconds, looping call's callback will be
        invoked.
        """
        assert not self._running, "LoopingCall is already running"
        self._running = True
        self._interval = interval
        self._deadline = self._loop.time()
        self()

    def is_running(self):
        """
        Return True if the looping calls is running.
        """
        return self._running

    @property
    def deadline(self):
        """
        Return the next time the callback should be invoked.

        The deadline is updated after the callback is invoked.
        """
        return self._deadline

    def stop(self):
        """
        Stop the looping calls.
        """
        if self._running:
            self._running = False
        if self._timer:
            self._timer.cancel()
            self._timer = None

    def __call__(self):
        if not self._running:
            return
        delay = self._loop.time() - self._deadline
        if delay > self.warning_delay:
            log.warning("Call %s delayed by %.2f seconds",
                        self._callback, delay)
        try:
            self._callback(*self._args)
        finally:
            # Schedule next call after callback, so we skip missed deadlines if
            # callback was slow. For example, if you schedule callback to run
            # every 1 second, but callback blocks for 1.1 seconds, it will be
            # called every 2 seconds.
            if self._running:
                self._schedule_next_call()

    def _schedule_next_call(self):
        """
        Schedule the next call, skipping missed deadlines in the past.
        """
        self._deadline += self._interval
        now = self._loop.time()
        if self._deadline <= now:
            # We missed at least one deadline.
            missed = (now - self._deadline) // self._interval + 1
            self._deadline += missed * self._interval
            log.warning("Call %s missed %d deadlines, scheduling next call "
                        "at %.2f",
                        self._callback, missed, self._deadline)
        self._timer = self._loop.call_at(self._deadline, self)
