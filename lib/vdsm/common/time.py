# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import
from __future__ import division

import collections
import time

from contextlib import contextmanager


def event_time():
    """
    Clock used for notifications and VM status reports. The value is used to
    process events in the correct order on engine side.

    Returns:
        integer value in milliseconds resolution.
    """
    # Older engines had broken parsing code, failing to parse time values
    # smaller than INT_MAX. This issue was hiden because os.times()[4] starts
    # at 2**32 / 1000 on boot, but time.monotonic() starts at 0.
    base = 2**31 - 1

    return base + int(monotonic_time() * 1000)


def monotonic_time():
    """
    Return monotonic time that cannot go backwards.
    """
    return time.monotonic()


class Clock(object):
    """
    Measure time for complex flows.

    This clock is useful for timing complex flows, when you want to record
    multiple timings for a single flow. For example, the total time, and the
    time of each step in the flow.

    This is a simpler and more strict version of MoinMoin.util.clock.Clock.

    Usage::

        clock = time.Clock()
        ...
        clock.start("total")
        clock.start("step1")
        ...
        clock.stop("step1")
        clock.start("step2")
        ...
        clock.stop("step2")
        clock.stop("total")
        log.info("times=%s", clock)

    """

    def __init__(self):
        self._timers = collections.OrderedDict()

    def start(self, name, start_time=None):
        if name in self._timers:
            raise RuntimeError("Timer %r already started" % name)
        if start_time is None:
            start_time = monotonic_time()
        self._timers[name] = (start_time, None)

    def stop(self, name):
        if name not in self._timers:
            raise RuntimeError("Timer %r was not started" % name)
        started, stopped = self._timers[name]
        if stopped is not None:
            raise RuntimeError("Timer %r already stopped" % name)
        self._timers[name] = (started, monotonic_time())

    @contextmanager
    def run(self, name):
        self.start(name)
        try:
            yield
        finally:
            self.stop(name)

    def __repr__(self):
        now = monotonic_time()
        timers = []
        for name, (started, stopped) in self._timers.items():
            if stopped:
                timers.append("%s=%.2f" % (name, stopped - started))
            else:
                # "*" indicates a running timer
                timers.append("%s=%.2f*" % (name, now - started))
        return "<Clock(%s)>" % ", ".join(timers)
