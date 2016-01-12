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
import threading

from . config import config
from . import utils

_monitor = None


def start():
    global _monitor
    assert _monitor is None
    if config.getboolean("vars", "health_monitor_enable"):
        interval = config.getint("vars", "health_check_interval")
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
        self._thread = utils.thread(self._run)
        self._done = threading.Event()

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
        collected = gc.collect()
        self.log.debug("Collected %d objects", collected)
        # Copy garbage so it is not modified while iterate over it.
        uncollectable = gc.garbage[:]
        if uncollectable:
            uncollectable = [saferepr(obj) for obj in uncollectable]
            self.log.warning("Found %d uncollectable objects: %s",
                             len(uncollectable), uncollectable)


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
