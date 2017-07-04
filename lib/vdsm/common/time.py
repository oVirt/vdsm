# Copyright 2017 Red Hat, Inc.
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

import os
import collections

from contextlib import contextmanager


def monotonic_time():
    """
    Return the amount of time, in secs, elapsed since a fixed
    arbitrary point in time in the past.
    This function is useful if the client just
    needs to use the difference between two given time points.

    With respect to time.time():
    * The resolution of this function is lower. On Linux,
      the resolution is 1/_SC_CLK_TCK, which in turn depends on
      the value of HZ configured in the kernel. A commonly
      found resolution is 10 (ten) ms.
    * This function is resilient with respect to system clock
      adjustments.
    """
    return os.times()[4]


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

    def start(self, name):
        if name in self._timers:
            raise RuntimeError("Timer %r already started" % name)
        self._timers[name] = (monotonic_time(), None)

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
