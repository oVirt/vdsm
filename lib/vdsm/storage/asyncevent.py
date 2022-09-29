# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-FileCopyrightText: 2001-2015 Python Software Foundation
# SPDX-License-Identifier: PSF-2.0

from __future__ import absolute_import

import asyncore
import collections
import errno
import heapq
import logging
import os
import select

import six

from vdsm.common import filecontrol
from vdsm.common import osutils
from vdsm.common import time
from vdsm.common.units import KiB

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

        This method is too big, keeping it as is to make it easier to
        backport fixes from Python 3.

        Changes from Python 3:
        - Use when > now when checking for ready timers, required for
          using time.monotonic_time using 10 millis resolution.
        - Use poll() instead of the selectors module which is not
          available in python 2.
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

        events = poll(timeout, map=self._channels)
        self._process_events(events)

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

    def _process_events(self, events):
        """
        Schedule ready I/O events for calling at the end of current event loop
        iteration.

        Changes compared to python 3.7:
        - We don't remove canceled dispatcher here, they are removed in
          asyncore.dispatcher.del_channel().
        """
        for fd, obj, flags in events:
            handle = Handle(self._readwrite, (fd, obj, flags))
            self._ready.append(handle)

    def _readwrite(self, fd, obj, flags):
        """
        Perform I/O on a ready dispatcher, if the dispatcher is till tracked by
        this event loop.
        """
        if self._channels.get(fd) is obj:
            # TODO: readwrite() is calling handle_xxx_event() on closed
            # dispatcher. This is fixed by this upstream patch:
            # https://github.com/python/cpython/pull/2854
            asyncore.readwrite(obj, flags)

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


def poll(timeout=0.0, map=None):
    """
    Wait for events on readable or writable dispatchers in map, returning list
    of tuples (fd, dispatcher, flags) if any of the file descriptor is ready
    for I/O.

    The caller must verify that a dispatcher is registered in asyncore
    socket_map for that fd before calling the I/O callbacks.

    This is an improved version of asyncore.poll2 from python 3.7, modified for
    integration with the event loop. Unlike the original version, this version
    returns a list of events, instead of invoking the I/O callbacks, which is
    racy. The caller should add a callback for each event to the event loop, to
    be run when the event loop cycle is done.

    Another difference here is not using any global state. This does not use
    asyncore.socket_map, you have to give it the map you want to poll.

    This fixes couple of bugs in the original implementation. These bugs may
    never be fixed in 2.7 or even in 3.x, since asyncore was deprecated since
    version 3.6.

    This is implemented outside of the event loop so we can reuse this
    implementation from other code using asyncore.
    """

    # Keeping compatibility with asyncore.poll2 when using with empty map. This
    # is not relevant to the event loop since we always have a Waker channel,
    # but may needed by other code that use its own possibly empty map.
    if not map:
        return []

    if timeout is not None:
        timeout = int(timeout * 1000)

    pollster = select.poll()

    # No need to copy the map during iteration, nobody can access the map
    # during the iteration, fixes http://bugs.python.org/issue30994.
    for fd, obj in six.iteritems(map):
        flags = 0
        if obj.readable():
            flags |= select.POLLIN | select.POLLPRI
        # accepting sockets should not be writable
        if obj.writable() and not obj.accepting:
            flags |= select.POLLOUT
        if flags:
            pollster.register(fd, flags)

    # The try block is needed only for python 2. In python 3 the call is
    # restarted after EINTR.
    try:
        r = pollster.poll(timeout)
    except select.error as e:
        if e.args[0] != errno.EINTR:
            raise
        return []

    # Fetch the dispatchers from map before invoking any I/O callback fixes
    # http://bugs.python.org/issue30931.
    return [(fd, map[fd], flags) for fd, flags in r]  # NOQA: F812


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
        elif self._callback is not None:
            info.append("callback={}".format(self._callback))
            if self._args:
                info.append("args={}".format(self._args))
        return info

    def __repr__(self):
        return "<{} at 0x{:x}>".format(" ".join(self._repr_info()), id(self))


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
        info.insert(1, "when={:.6f}".format(self._when))
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
        osutils.uninterruptible(self.socket.read, KiB)

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
