#
# Copyright 2016-2017 Red Hat, Inc.
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

"""
This module provides event loop based infrastructure for scalable storage
health monitoring:

CheckService     entry point for starting and stopping path checkers.

DirectioChecker  checker using dd process for file or block based
                 volumes.

CheckResult      result object provided to user callback on each check.
"""

from __future__ import absolute_import

import logging
import re
import subprocess
import threading

from vdsm import cmdutils
from vdsm import constants
from vdsm.common import concurrent
from vdsm.common.compat import CPopen
from vdsm.storage import asyncevent
from vdsm.storage import asyncutils
from vdsm.storage import exception

EXEC_ERROR = 127

_log = logging.getLogger("storage.check")


class CheckService(object):
    """
    Provides path checking service.

    This object is a simple thread safe entry point for starting and stopping
    path checkers, keeping the internals decoupled from client code.

    Usage:

        # Start the service

        service = CheckService()
        service.start()

        # Start checking path

        service.start_checking(path, complete)

        # Stop checking path, waiting up to 30 seconds

        service.stop_checking(path, timeout=30)

        # Stop the service

        service.stop()

    """

    def __init__(self):
        self._lock = threading.Lock()
        self._loop = asyncevent.EventLoop()
        self._thread = concurrent.thread(self._loop.run_forever,
                                         name="check/loop")
        self._checkers = {}

    def start(self):
        """
        Start the service thread.
        """
        _log.info("Starting check service")
        self._thread.start()

    def stop(self):
        """
        Stop all checkers and the service thread.

        Do not wait for running check processes since the application is
        shutting down. To wait for all processes, stop all checkers and wait
        for them before stoping.
        """
        if not self._thread.is_alive():
            return
        _log.info("Stopping check service")
        with self._lock:
            for checker in self._checkers.values():
                self._loop.call_soon_threadsafe(checker.stop)
            self._checkers.clear()
            self._loop.call_soon_threadsafe(self._loop.stop)
            self._thread.join()
            self._loop.close()

    def start_checking(self, path, complete, interval=10.0):
        """
        Start checking path every interval secconds. On check, invoke the
        complete callback with a CheckResult instance.

        Note that the complete callback is invoked in the check thread, and
        must not block, as it will block all other checkers.
        """
        _log.info("Start checking %r", path)
        with self._lock:
            if path in self._checkers:
                raise RuntimeError("Already checking path %r" % path)
            checker = DirectioChecker(self._loop, path, complete,
                                      interval=interval)
            self._checkers[path] = checker
        self._loop.call_soon_threadsafe(checker.start)

    def stop_checking(self, path, timeout=None):
        """
        Stop checking path. If timeout is set, wait until the checker has
        stopped, or the timeout has expired.
        """
        _log.info("Stop checking %r", path)
        with self._lock:
            checker = self._checkers.pop(path)
        self._loop.call_soon_threadsafe(checker.stop)
        if timeout:
            return checker.wait(timeout)

    def is_checking(self, path):
        return path in self._checkers


# Checker state
IDLE = "idle"
RUNNING = "running"
STOPPING = "stopping"


class DirectioChecker(object):
    """
    Check path availability using direct I/O.

    DirectioChecker is created with a complete callback.  Each time a check
    cycle is completed, the complete callback will be invoked with a
    CheckResult instance.

    CheckResult provides a delay() method returning the read delay in
    seconds. If the check failed, the delay() method will raise the
    appropriate exception that can be reported to engine.

    Note that the complete callback must not block as it will block the entire
    event loop thread.

    The checker runs exactly every interval seconds. If a check did not
    complete before the next check is scheduled, the next check will be delayed
    to the next interval.

    Checker is not thread safe. Use EventLoop.call_soon_threadsafe() to start
    or stop a checker. The only thread safe method is wait().

    Usage::

        # Start the event loop thread

        loop = asyncevent.EventLoop()
        concurrent.thread(loop.run_forever).start()

        # The complete callback

        def complete(result):
            try:
                check_delay = result.delay()
            except Exception as e:
                check_error = e
            check_time = time.time()

        # Start a checker on the event loop thread

        checker = DirectioChecker(loop, path, complete)
        loop.call_soon_threadsafe(checker.start)

        ...

        # Stop a checker from another thread

        loop.call_soon_threadsafe(checker.stop)

        # If needed, wait until a checker actually stopped.

        checker.wait(30)

    """

    log = logging.getLogger("storage.directiochecker")

    def __init__(self, loop, path, complete, interval=10.0):
        self._loop = loop
        self._path = path
        self._complete = complete
        self._interval = interval
        self._looper = asyncutils.LoopingCall(loop, self._check)
        self._check_time = None
        self._proc = None
        self._reader = None
        self._reaper = None
        self._err = None
        self._state = IDLE
        self._stopped = threading.Event()

    def start(self):
        """
        Start the checker.

        Raises RuntimeError if the checker is running.
        """
        if self._state is not IDLE:
            raise RuntimeError("Checker is %s", self._state)
        self._state = RUNNING
        _log.debug("Checker %r started", self._path)
        self._stopped.clear()
        self._looper.start(self._interval)

    def stop(self):
        """
        Stop the checker.

        If the checker is waiting for the next check, the next check will be
        cancelled. If the checker is in the middle of a check, it will stop
        when the check completes.

        If the checker is not running, the call is ignored silently.
        """
        if self._state is not RUNNING:
            return
        _log.debug("Checker %r stopping", self._path)
        self._state = STOPPING
        self._looper.stop()
        if self._proc is None:
            self._stop_completed()

    def wait(self, timeout=None):
        """
        Wait until a checker has stopped.

        Returns True if checker has stopped, False if timeout expired.
        """
        return self._stopped.wait(timeout)

    def is_running(self):
        return self._state is not IDLE

    def _stop_completed(self):
        self._state = IDLE
        _log.debug("Checker %r stopped", self._path)
        self._stopped.set()

    def _check(self):
        """
        Called when starting the checker, and then every interval seconds until
        the checker is stopped.
        """
        assert self._state is RUNNING
        if self._proc:
            _log.warning("Checker %r is blocked for %.2f seconds",
                         self._path, self._loop.time() - self._check_time)
            return
        self._check_time = self._loop.time()
        _log.debug("START check %r (delay=%.2f)",
                   self._path, self._check_time - self._looper.deadline)
        try:
            self._start_process()
        except Exception as e:
            self._err = "Error starting process: %s" % e
            self._check_completed(EXEC_ERROR)

    def _start_process(self):
        """
        Starts a dd process performing direct I/O to path, reading the process
        stderr. When stderr has closed, _read_completed will be called.
        """
        cmd = [constants.EXT_DD, "if=%s" % self._path, "of=/dev/null",
               "bs=4096", "count=1", "iflag=direct"]
        cmd = cmdutils.wrap_command(cmd)
        self._proc = CPopen(cmd, stdin=None, stdout=None,
                            stderr=subprocess.PIPE)
        self._reader = self._loop.create_dispatcher(
            asyncevent.BufferedReader, self._proc.stderr, self._read_completed)

    def _read_completed(self, data):
        """
        Called when dd process has closed stderr. At this point the process may
        be still running.
        """
        assert self._state is not IDLE
        self._reader = None
        self._err = data
        rc = self._proc.poll()
        # About 95% of runs, the process has terminated at this point. If not,
        # start the reaper to wait for it.
        if rc is None:
            self._reaper = asyncevent.Reaper(self._loop, self._proc,
                                             self._check_completed)
            return
        self._check_completed(rc)

    def _check_completed(self, rc):
        """
        Called when the dd process has exited with exit code rc.
        """
        assert self._state is not IDLE
        now = self._loop.time()
        elapsed = now - self._check_time
        _log.debug("FINISH check %r (rc=%s, elapsed=%.02f)",
                   self._path, rc, elapsed)
        self._reaper = None
        self._proc = None
        if self._state is STOPPING:
            self._stop_completed()
            return
        result = CheckResult(self._path, rc, self._err, self._check_time,
                             elapsed)
        try:
            self._complete(result)
        except Exception:
            _log.exception("Unhandled error in complete callback")

    def __repr__(self):
        info = [self.__class__.__name__,
                self._path,
                self._state]
        if self._state is RUNNING:
            info.append("next_check=%.2f" % self._looper.deadline)
        return "<%s at 0x%x>" % (" ".join(info), id(self))


class CheckResult(object):

    _PATTERN = re.compile(br".*, ([\de\-.]+) s,[^,]+")

    def __init__(self, path, rc, err, time, elapsed):
        self.path = path
        self.rc = rc
        self.err = err
        self.time = time
        self.elapsed = elapsed

    def delay(self):
        # TODO: Raising MiscFileReadException for all errors to keep the old
        # behavior. Should probably use StorageDomainAccessError.
        if self.rc != 0:
            raise exception.MiscFileReadException(self.path, self.rc, self.err)
        if not self.err:
            raise exception.MiscFileReadException(self.path, "no stats")
        stats = self.err.splitlines()[-1]
        match = self._PATTERN.match(stats)
        if not match:
            raise exception.MiscFileReadException(self.path,
                                                  "no match: %r" % stats)
        seconds = match.group(1)
        try:
            return float(seconds)
        except ValueError as e:
            raise exception.MiscFileReadException(self.path, e)

    def __repr__(self):
        return "<%s path=%s rc=%d err=%r time=%.2f elapsed=%.2f at 0x%x>" % (
            self.__class__.__name__, self.path, self.rc, self.err, self.time,
            self.elapsed, id(self))
