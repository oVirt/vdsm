#
# Copyright 2016-2018 Red Hat, Inc.
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

import gc
import logging
import os
import threading

from vdsm.common import concurrent
from vdsm.common import cpuarch

from . config import config
from . import metrics

_monitor = None


def start():
    global _monitor
    assert _monitor is None
    if config.getboolean("health", "monitor_enable"):
        interval = config.getint("health", "check_interval")
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
        self._thread = concurrent.thread(self._run, name="health")
        self._done = threading.Event()
        self._last = ProcStat()
        self._stats = {}

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
        self._report_stats()

    def _check_garbage(self):
        collected = gc.collect()
        self.log.debug("Collected %d objects", collected)
        # Copy garbage so it is not modified while iterate over it.
        uncollectable = gc.garbage[:]
        if uncollectable:
            uncollectable = [saferepr(obj) for obj in uncollectable]
            self.log.warning("Found %d uncollectable objects: %s",
                             len(uncollectable), uncollectable)
        self._stats['uncollectable_obj'] = len(uncollectable)

    def _check_resources(self):
        current = ProcStat()
        self._stats['utime_pct'] = ((current.utime - self._last.utime) /
                                    self._interval * 100)
        self._stats['stime_pct'] = ((current.stime - self._last.stime) /
                                    self._interval * 100)
        self._stats['rss'] = current.rss
        delta_rss = current.rss - self._last.rss
        self._stats['threads'] = current.threads
        self._last = current
        self.log.debug("user=%.2f%%, sys=%.2f%%, rss=%d kB (%s%d), threads=%d",
                       self._stats['utime_pct'],
                       self._stats['stime_pct'],
                       self._stats['rss'],
                       "+" if delta_rss >= 0 else "-",
                       abs(delta_rss),
                       self._stats['threads'])

    def _report_stats(self):
        prefix = "hosts.vdsm"
        report = {}
        report[prefix + '.gc.uncollectable'] = \
            self._stats['uncollectable_obj']
        report[prefix + '.cpu.user_pct'] = self._stats['utime_pct']
        report[prefix + '.cpu.sys_pct'] = self._stats['stime_pct']
        report[prefix + '.memory.rss'] = self._stats['rss']
        report[prefix + '.threads_count'] = self._stats['threads']
        metrics.send(report)


class ProcStat(object):

    _TICKS_PER_SEC = os.sysconf("SC_CLK_TCK")
    _PATH = "/proc/self/stat"

    def __init__(self):
        with open(self._PATH, "rb") as f:
            fields = f.readline().split()
        # See proc(5) for available fields and their semantics.
        self.utime = int(fields[13], 10) / self._TICKS_PER_SEC
        self.stime = int(fields[14], 10) / self._TICKS_PER_SEC
        self.threads = int(fields[19], 10)
        self.rss = int(fields[23], 10) * cpuarch.PAGE_SIZE_BYTES // 1024


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
