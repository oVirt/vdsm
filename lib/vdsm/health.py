#
# Copyright 2016 Red Hat, Inc.
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
import gc
import logging
import os
import threading

from . config import config
from . import concurrent

_monitor = None


def start():
    global _monitor
    assert _monitor is None
    if config.getboolean("devel", "health_monitor_enable"):
        interval = config.getint("devel", "health_check_interval")
        _monitor = Monitor(interval)
        _monitor.start()


def stop():
    global _monitor
    if _monitor is not None:
        _monitor.stop()
        _monitor = None


class Monitor(object):

    log = logging.getLogger("health")

    def __init__(self, interval):
        self._interval = interval
        self._thread = concurrent.thread(self._run)
        self._done = threading.Event()
        self._last = ProcStat()

    def start(self):
        self.log.info("Starting health monitor (interval=%d)", self._interval)
        self._thread.start()

    def stop(self):
        self.log.info("Stopping health monitor")
        self._done.set()

    def wait(self):
        self.log.debug("Waiting for health monitor")
        self._thread.join()

    def _run(self):
        self.log.debug("Health monitor started")
        saved_flags = gc.get_debug()
        gc.set_debug(0)
        try:
            while not self._done.wait(self._interval):
                try:
                    self._check()
                except Exception:
                    self.log.exception("Error checking health")
        finally:
            gc.set_debug(saved_flags)
        self.log.debug("Health monitor stopped")

    def _check(self):
        self.log.debug("Checking health")
        self._check_garbage()
        self._check_resources()

    def _check_garbage(self):
        collected = gc.collect()
        self.log.debug("Collected %d objects", collected)
        # Copy garbage so it is not modified while iterate over it.
        uncollectable = gc.garbage[:]
        if uncollectable:
            uncollectable = [saferepr(obj) for obj in uncollectable]
            self.log.warning("Found %d uncollectable objects: %s",
                             len(uncollectable), uncollectable)

    def _check_resources(self):
        current = ProcStat()
        utime_pct = (current.utime - self._last.utime) / self._interval * 100
        stime_pct = (current.stime - self._last.stime) / self._interval * 100
        delta_rss = current.rss - self._last.rss
        self._last = current
        self.log.debug("user=%.2f%%, sys=%.2f%%, rss=%d kB (%s%d), threads=%d",
                       utime_pct,
                       stime_pct,
                       current.rss,
                       "+" if delta_rss >= 0 else "-",
                       abs(delta_rss),
                       current.threads)


class ProcStat(object):

    _PAGE_SIZE = os.sysconf("SC_PAGESIZE")
    _TICKS_PER_SEC = os.sysconf("SC_CLK_TCK")
    _PATH = "/proc/self/stat"

    def __init__(self):
        with open(self._PATH, "rb") as f:
            fields = f.readline().split()
        # See proc(5) for available fields and their semantics.
        self.utime = int(fields[13], 10) / float(self._TICKS_PER_SEC)
        self.stime = int(fields[14], 10) / float(self._TICKS_PER_SEC)
        self.threads = int(fields[19], 10)
        self.rss = int(fields[23], 10) * self._PAGE_SIZE / 1024


def saferepr(obj):
    """
    Some objects from standard library fail in repr because of buggy __repr__
    implementation. Try the builtin repr() and if it fails, warn and fallback
    to simple repr.
    """
    try:
        return repr(obj)
    except Exception as e:
        simple_repr = "<%s at 0x%x>" % (type(obj), id(obj))
        logging.warning("repr() failed for %s: %s", simple_repr, e)
        return simple_repr
