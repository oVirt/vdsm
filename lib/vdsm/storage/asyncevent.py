#
# Copyright 2016-2017 Red Hat, Inc.
#
# Copyright (c) 2001, 2002, 2003, 2004, 2005, 2006, 2007, 2008, 2009,
# 2010, 2011, 2012, 2013, 2014, 2015 Python Software Foundation; All
# Rights Reserved
#
# Licensed under the PYTHON SOFTWARE FOUNDATION LICENSE VERSION 2; you
# may not use this file except in compliance with the License.
#
# PYTHON SOFTWARE FOUNDATION LICENSE VERSION 2
# --------------------------------------------
#
# 1. This LICENSE AGREEMENT is between the Python Software Foundation
# ("PSF"), and the Individual or Organization ("Licensee") accessing and
# otherwise using this software ("Python") in source or binary form and
# its associated documentation.
#
# 2. Subject to the terms and conditions of this License Agreement, PSF
# hereby grants Licensee a nonexclusive, royalty-free, world-wide
# license to reproduce, analyze, test, perform and/or display publicly,
# prepare derivative works, distribute, and otherwise use Python alone
# or in any derivative version, provided, however, that PSF's License
# Agreement and PSF's notice of copyright, i.e., "Copyright (c) 2001,
# 2002, 2003, 2004, 2005, 2006, 2007, 2008, 2009, 2010, 2011, 2012,
# 2013, 2014, 2015 Python Software Foundation; All Rights Reserved" are
# retained in Python alone or in any derivative version prepared by
# Licensee.
#
# 3. In the event Licensee prepares a derivative work that is based on
# or incorporates Python or any part thereof, and wants to make the
# derivative work available to others as provided herein, then Licensee
# hereby agrees to include in any such work a brief summary of the
# changes made to Python.
#
# 4. PSF is making Python available to Licensee on an "AS IS" basis.
# PSF MAKES NO REPRESENTATIONS OR WARRANTIES, EXPRESS OR IMPLIED.  BY
# WAY OF EXAMPLE, BUT NOT LIMITATION, PSF MAKES NO AND DISCLAIMS ANY
# REPRESENTATION OR WARRANTY OF MERCHANTABILITY OR FITNESS FOR ANY
# PARTICULAR PURPOSE OR THAT THE USE OF PYTHON WILL NOT INFRINGE ANY
# THIRD PARTY RIGHTS.
#
# 5. PSF SHALL NOT BE LIABLE TO LICENSEE OR ANY OTHER USERS OF PYTHON
# FOR ANY INCIDENTAL, SPECIAL, OR CONSEQUENTIAL DAMAGES OR LOSS AS A
# RESULT OF MODIFYING, DISTRIBUTING, OR OTHERWISE USING PYTHON, OR ANY
# DERIVATIVE THEREOF, EVEN IF ADVISED OF THE POSSIBILITY THEREOF.
#
# 6. This License Agreement will automatically terminate upon a material
# breach of its terms and conditions.
#
# 7. Nothing in this License Agreement shall be deemed to create any
# relationship of agency, partnership, or joint venture between PSF and
# Licensee.  This License Agreement does not grant permission to use PSF
# trademarks or trade name in a trademark sense to endorse or promote
# products or services of Licensee, or any third party.
#
# 8. By copying, installing or otherwise using Python, Licensee agrees
# to be bound by the terms and conditions of this License Agreement.
#

from __future__ import absolute_import

import asyncore
import collections
import errno
import heapq
import logging
import os

from vdsm.common import filecontrol
from vdsm.common import osutils
from vdsm.common import time

log = logging.getLogger("storage.asyncevent")


class EventLoop(object):
    """
    Simple event loop implementing a subset of asyncio.BaseEventLoop from
    Python 3.

    This class is not thread safe. To call from other threads, use
    call_soon_threadsafe.

    Most of the code is copied as is from Python 3.4 without changes.  See
    methods docstrings for changes compared with Python 3 code.
    """

    def __init__(self):
        """
        Changes from Python 3:
        - Remove debugging code
        - Use deque for ready queue (taken from Python 3.6)
        - Use asyncore base waekup pipe
        """
        self._channels = {}
        self._scheduled = []
        self._ready = collections.deque()
        self._running = False
        self._closed = False
        self._waker = self.create_dispatcher(Waker)

    # Running an event loop

    def run_forever(self):
        """
        Run the event loop, processing I/O events and scheduled calls until the
        event loop is stopped.

        Each cycle, we process the events, and the calls scheduled for this
        cycle. If you schedule a new call from a callback, it will run only in
        the next cycle, even if the deadline has passed.

        Changes from Python 3:
        - Add logging
        """
        self._check_closed()
        if self._running:
            raise RuntimeError('Event loop is running.')
        self._running = True
        try:
            log.info("Starting %s", self)
            while True:
                try:
                    self._run_once()
                except _StopError:
                    break
        finally:
            self._running = False
            log.debug("%s stopped", self)

    def _run_once(self):
        """
        Run one full iteration of the event loop.

        This calls all currently ready callbacks, polls for I/O,
        schedules the resulting callbacks, and finally schedules
        'call_later' callbacks.

        This method is too big, keeping it as is to make it easier to backport
        fixes from Python 3.

        Changes from Python 3:
        - Process events using asyncore.
        - Use when > now when checking for ready timers, required for using
          time.monotonic_time using 10 millis resolution.
        """
        # Remove delayed calls that were cancelled from head of queue.
        while self._scheduled and self._scheduled[0]._cancelled:
            heapq.heappop(self._scheduled)

        timeout = None
        if self._ready:
            timeout = 0
        elif self._scheduled:
            when = self._scheduled[0]._when
            timeout = max(0, when - self.time())

        # Note: unlike Python 3 version, this run I/O event handlers now. This
        # means that handlers scheduled from I/O event handlers will run in
        # this cycle instead of the next cycle in Python 3.
        asyncore.poll2(timeout, self._channels)

        # Handle 'later' callbacks that are ready.
        now = self.time()
        while self._scheduled:
            handle = self._scheduled[0]
            if handle._when > now:
                break
            heapq.heappop(self._scheduled)
            self._ready.append(handle)

        # This is the only place where callbacks are actually *called*.
        # All other places just add them to ready.
        # Note: We run all currently scheduled callbacks, but not any
        # callbacks scheduled by callbacks run this time around --
        # they will be run the next time (after another I/O poll).
        # Use an idiom that is thread-safe without using locks.
        ntodo = len(self._ready)
        for i in range(ntodo):
            handle = self._ready.popleft()
            if handle._cancelled:
                continue
            handle._run()

        handle = None  # Needed to break cycles when an exception occurs.

    def stop(self):
        """
        May be called from the event loop thread to stop the event loop after
        all events and timers in the current cycle are processed.
        """
        self._check_closed()
        log.info("Stopping %r", self)
        self.call_soon(_raise_stop_error)

    def close(self):
        """
        Close the event loop. The loop must not be running. Pending callbacks
        will be lost.

        This clears the resources used by the event loop, but does not wait
        for completion.

        This is idempotent and irreversible. No other methods should be called
        after this one.

        Changes from Python 3:
        - Remove executor handling
        """
        if self._running:
            raise RuntimeError("Cannot close a running event loop")
        if self._closed:
            return
        log.debug("Closing %r", self)
        self._closed = True
        self._waker.close()
        self._ready.clear()
        del self._scheduled[:]
        asyncore.close_all(map=self._channels)

    # Making calls

    def call_soon(self, callback, *args):
        """
        Arrange for a callback to be called as soon as possible.

        This operates as a FIFO queue: callbacks are called in the
        order in which they are registered.  Each callback will be
        called exactly once.

        Any positional arguments after the callback will be passed to
        the callback when it is called.
        """
        self._check_closed()
        return self._call_soon(callback, args)

    def call_soon_threadsafe(self, callback, *args):
        """
        Like call_soon(), but thread-safe.

        No error is reported if the event loop is closed.
        """
        handle = self._call_soon(callback, args)
        self._write_to_self()
        return handle

    def _call_soon(self, callback, args):
        """
        Changes from Python 3:
        - Remove debugging code
        - Remove unneeded check_loop argument
        """
        handle = Handle(callback, args)
        self._ready.append(handle)
        return handle

    def _write_to_self(self):
        self._waker.wakeup()

    # Scheduling calls

    def call_later(self, delay, callback, *args):
        """
        Arrange for a callback to be called at a given time.

        Return a Handle: an opaque object with a cancel() method that
        can be used to cancel the call.

        The delay can be an int or float, expressed in seconds.  It is
        always relative to the current time.

        Each callback will be called exactly once.  If two callbacks
        are scheduled for exactly the same time, it undefined which
        will be called first.

        Any positional arguments after the callback will be passed to
        the callback when it is called.
        """
        return self.call_at(self.time() + delay, callback, *args)

    def call_at(self, when, callback, *args):
        """
        Like call_later(), but uses an absolute time.

        Absolute time corresponds to the event loop's time() method.

        Changes from Python 3:
        - Remove debugging code
        """
        self._check_closed()
        timer = Timer(when, callback, args)
        heapq.heappush(self._scheduled, timer)
        return timer

    # asyncore support

    def create_dispatcher(self, dispatcher_class, *args, **kwargs):
        """
        Create asyncore.dispatcher or subclasses using the event loop
        channels map.

        The dispatcher adds itself to the channels map, and will remove itself
        when closed.

        This method is not implemented by Python 3 BaseEventLoop. There is no
        way to integrate asyncore with asyncio in Python 3.
        """
        assert "map" not in kwargs, "map is set by the event loop"
        return dispatcher_class(*args, map=self._channels, **kwargs)

    # Testing

    def is_running(self):
        return self._running

    def is_closed(self):
        return self._closed

    # Keeping time

    def time(self):
        """
        Return the time according to the event loop's clock.

        This is a float expressed in seconds since an epoch, but the
        epoch, precision, accuracy and drift are unspecified and may
        differ per event loop.

        Changes from Python 3:
        - Use Python 2 compatible monotonic time
        """
        return time.monotonic_time()

    # Validation

    def _check_closed(self):
        if self._closed:
            raise RuntimeError('Event loop is closed')

    # Debugging

    def __repr__(self):
        """
        Changes from Python 3:
        - Standard vdsmm __repr__ formatting
        """
        return ("<{self.__class__.__name__} "
                "running={self._running} "
                "closed={self._closed} "
                "at 0x{addr}>").format(self=self, addr=id(self))


class _StopError(BaseException):
    """ Raised for stopping the event loop """


def _raise_stop_error():
    raise _StopError


class Handle(object):
    """
    Simplified version of Python 3.4 asyncio.events.Handle.

    Changes from Python 3:
    - Remove debugging code.
    - Remove loop variable.
    - Thread safe cancellation (Python 3 may try to call None).
    - _cancelled property instead of instance variable.
    - Simpler _repr_info
    """

    def __init__(self, callback, args):
        self._callback = callback
        self._args = args

    def cancel(self):
        self._callback = _CANCELLED
        self._args = None

    @property
    def _cancelled(self):
        return self._callback is _CANCELLED

    def _run(self):
        try:
            self._callback(*self._args)
        except Exception:
            log.exception("Unhandled error in %s", self)
        # Break cycles
        self._callback = self._args = None

    def _repr_info(self):
        info = [self.__class__.__name__]
        if self._cancelled:
            info.append("cancelled")
        else:
            info.append("callback=%s" % self._callback)
            if self._args:
                info.append("args=%s" % self._args)
        return info

    def __repr__(self):
        return "<%s at 0x%x>" % (" ".join(self._repr_info()), id(self))


class Timer(Handle):
    """
    Simplified version of Python 3.4 asyncio.events.TimerHandle.

    Changes from Python 3:
    - Remove debugging code.
    - Do not implement __hash__, __eq__, __ne__ since they are not needed for
      timer handling.
    - Simpelr and cheaper __le__, __ge__
    - Simpler __repr__
    """

    def __init__(self, when, callback, args):
        super(Timer, self).__init__(callback, args)
        self._when = when

    # Comparing (rich comperision required for Python 3)

    def __lt__(self, other):
        return self._when < other._when

    def __le__(self, other):
        return self._when <= other._when

    def __gt__(self, other):
        return self._when > other._when

    def __ge__(self, other):
        return self._when >= other._when

    # Debugging

    def _repr_info(self):
        info = super(Timer, self)._repr_info()
        info.insert(1, "when=%.6f" % self._when)
        return info


def _CANCELLED(*args):
    pass


class Waker(asyncore.file_dispatcher):
    """
    Wake up the event loop from another thread.

    The read end of the pipe is watched by the event loop, while other threads
    may wakeup the event loop by writing a byte to the write end.

    Based on twisted.internet.posixbase._UnixWaker.
    """

    def __init__(self, map):
        rfd, wfd = os.pipe()
        asyncore.file_dispatcher.__init__(self, rfd, map=map)
        os.close(rfd)  # file_dispatcher duped it
        filecontrol.set_close_on_exec(self._fileno)
        filecontrol.set_close_on_exec(wfd)
        filecontrol.set_non_blocking(wfd)
        self._wfd = wfd

    def wakeup(self):
        try:
            osutils.uninterruptible(os.write, self._wfd, b"\0")
        except OSError as e:
            if self.closing:
                # Another thread tried to wake up after loop was closed.
                return
            if e.errno == errno.EAGAIN:
                # The pipe is full, no need to write.
                return
            raise

    def handle_read(self):
        osutils.uninterruptible(self.socket.read, 1024)

    def handle_close(self):
        log.error("Wakeup read end was closed")
        self.close()

    def writable(self):
        return False

    def close(self):
        if self.closing:
            return
        self.closing = True
        # Set fd to invalid before closing to make sure other threads do not
        # write to closed fd.
        wfd = self._wfd
        self._wfd = -1
        os.close(wfd)
        asyncore.file_dispatcher.close(self)


class BufferedReader(asyncore.file_dispatcher):
    """
    Read from file until file is close and notify when read was completed.
    """

    def __init__(self, fd, complete, bufsize=4096, map=None):
        asyncore.file_dispatcher.__init__(self, fd, map=map)
        filecontrol.set_close_on_exec(self._fileno)
        self._complete = complete
        self._bufsize = bufsize
        self._data = bytearray()

    def handle_read(self):
        chunk = self.socket.read(self._bufsize)
        if not chunk:
            self.handle_close()
            return
        self._data += chunk

    def handle_close(self):
        # Call complete exactly once.
        if self._complete:
            complete = self._complete
            self._complete = None
            complete(self._data)
        self.close()

    def handle_error(self):
        log.exception("Unhandled error in %s", self)
        self.handle_close()

    def close(self):
        # asyncore.dispatcher define closing attribute, but doe not use it.
        if self.closing:
            return
        self.closing = True
        # Never call complete if closed before completion.
        self._complete = None
        asyncore.file_dispatcher.close(self)

    def writable(self):
        return False


class Reaper(object):
    """
    Wait for process and notify when it has terminated.
    """

    def __init__(self, loop, proc, complete, min_interval=2**-5,
                 max_interval=1.0):
        self._loop = loop
        self._proc = proc
        self._complete = complete
        self._interval = min_interval
        self._max_interval = max_interval
        self._count = 0
        self._loop.call_later(self._interval, self.reap)

    def reap(self):
        self._count += 1
        rc = self._proc.poll()
        if rc is None:
            if self._interval < self._max_interval:
                self._interval *= 2
            self._loop.call_later(self._interval, self.reap)
            return
        log.debug("Process %s terminated (count=%d)", self._proc, self._count)
        self._complete(rc)
        self._complete = None
