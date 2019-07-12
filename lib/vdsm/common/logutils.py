#
# Copyright 2011-2018 Red Hat, Inc.
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

import collections
import datetime
import functools
import grp
import logging
import logging.handlers
import os
import pwd
import threading
import time

from dateutil import tz
from inspect import ismethod

import six

from . import concurrent


def funcName(func):
    if ismethod(func):
        return func.__func__.__name__

    if hasattr(func, 'func'):
        return func.func.__name__

    return func.__name__


def call2str(func, args, kwargs, printers={}):
    kwargs = kwargs.copy()
    varnames = func.__code__.co_varnames[:func.__code__.co_argcount]
    if ismethod(func):
        args = [func.__self__] + list(args)
        func = func.__func__

    for name, val in zip(varnames, args):
        kwargs[name] = val

    defaults = func.__defaults__ if func.__defaults__ else []

    for name, val in zip(varnames[-len(defaults):], defaults):
        if name not in kwargs:
            kwargs[name] = val

    argsStrs = []
    for i, argName in enumerate(varnames):
        if i == 0 and argName == "self":
            continue

        val = kwargs[argName]
        printer = printers.get(argName, repr)
        argsStrs.append("%s=%s" % (argName, printer(val)))

    return "%s(%s)" % (func.__name__, ", ".join(argsStrs))


class SimpleLogAdapter(logging.LoggerAdapter):
    # Because of how python implements the fact that warning
    # and warn are the same. I need to reimplement it here. :(
    warn = logging.LoggerAdapter.warning

    def __init__(self, logger, context):
        """
        Initialize an adapter with a logger and a dict-like object which
        provides contextual information. The contextual information is
        prepended to each log message.

        This adapter::

            self.log = SimpleLogAdapter(self.log, {"task": "xxxyyy",
                                                   "res", "foo.bar.baz"})
            self.log.debug("Message")

        Would produce this message::

            "(task='xxxyyy', res='foo.bar.baz') Message"
        """
        self.logger = logger
        items = ", ".join(
            "%s='%s'" % (k, v) for k, v in six.viewitems(context))
        self.prefix = "(%s) " % items

    def process(self, msg, kwargs):
        return self.prefix + msg, kwargs


class UserGroupEnforcingHandler(logging.handlers.WatchedFileHandler):
    """
    This log handler acts like WatchedFileHandler.
    Additionally, upon file access, handler check the credentials of running
    process,to make sure log is not created with wrong permissions by mistake.
    """

    def __init__(self, user, group, *args, **kwargs):
        self._uid = pwd.getpwnam(user).pw_uid
        self._gid = grp.getgrnam(group).gr_gid
        logging.handlers.WatchedFileHandler.__init__(self, *args, **kwargs)

        # Used to defer flushing when used by ThreadedHandler.
        self.buffering = False

        # To trigger cred check:
        self._open()

    def _open(self):
        if (os.geteuid() != self._uid) or (os.getegid() != self._gid):
            raise RuntimeError(
                "Attempt to open log with incorrect credentials")
        return logging.handlers.WatchedFileHandler._open(self)

    def flush(self):
        """
        Extend super implementation to allow deferred flushing.
        """
        if self.buffering:
            return
        logging.handlers.WatchedFileHandler.flush(self)


class TimezoneFormatter(logging.Formatter):
    def converter(self, timestamp):
        return datetime.datetime.fromtimestamp(timestamp,
                                               tz.tzlocal())

    def formatTime(self, record, datefmt=None):
        ct = self.converter(record.created)
        if datefmt:
            s = ct.strftime(datefmt, ct)
        else:
            s = "%s,%03d%s" % (
                ct.strftime('%Y-%m-%d %H:%M:%S'),
                record.msecs,
                ct.strftime('%z')
            )
        return s


class ThreadedHandler(logging.handlers.MemoryHandler):
    """
    A handler queuing records and logging them in a background thread using
    a target handler configured elsewhere.

    This is not a memory handler; the reason we inherit from it is being able
    to use another handler defined in logger.conf as the target.

    When configuring loggers in logging.config.fileConfig, we have this check:

        if issubclass(klass, logging.handlers.MemoryHandler):

    If the logger inherits from MemoryHandler, the target of the logger will be
    set later using setTarget, after all handlers are loaded.
    """

    # Interval for reporting handler stats.
    STATS_INTERVAL = 60

    _CLOSED = object()

    def __init__(self, capacity=2000, adaptive=True, start=True):
        """
        Arguments:
            capacity (int): number of records to queue before dropping records.
                When the queue becomes full, new records are dropped. Testing
                shows that 2000 is large enough to avoid dropping messages when
                using DEBUG log level, and extremely slow storage that takes
                0.4 seconds for every write.
            adaptive (bool): adapt log level to number of queued messages,
                dropping lower priority messages.
            start (bool): start the handler thread automatically. If False, the
                thread must be started explicitly.
        """
        logging.handlers.MemoryHandler.__init__(self, 0)
        if adaptive:
            # Use list instead of dict, so we can support custom log message
            # like TRACING, used in some parts of vdsm.
            self._limits = [
                (logging.DEBUG, int(capacity * 0.6)),
                (logging.INFO, int(capacity * 0.7)),
                (logging.WARNING, int(capacity * 0.8)),
                (logging.ERROR, int(capacity * 0.9)),
                (logging.CRITICAL, capacity),
            ]
        else:
            self._limits = [(logging.CRITICAL, capacity)]
        self._target = _DROPPER
        self._queue = collections.deque()
        self._cond = threading.Condition(threading.Lock())
        # The time of the last report.
        self._last_report = time.time()
        # Number of dropped records for last interval.
        self._dropped_records = 0
        # The maximum number of pending records for the last interval.
        self._max_pending = 0
        self._thread = concurrent.thread(self._run, name="logfile")
        if start:
            self.start()

    # Handler interface

    def createLock(self):
        """
        Override to avoid unneeded lock. We use a condition to synchronize with
        the logging thread.
        """
        self.lock = None

    def handle(self, record):
        """
        Handle a log record.

        If the queue is full, the record is dropped.  If check interval was
        completed, warn about messages dropped during this interval.
        """
        with self._cond:
            # First, handle this record.
            if self._can_handle(record):
                self._queue.append(record)
                self._cond.notify()
            else:
                self._dropped_records += 1

            # Is time to report stats?
            interval = record.created - self._last_report
            if interval < self.STATS_INTERVAL:
                return

            # Prepare stats and reset counters.
            dropped_records = self._dropped_records
            max_pending = self._max_pending
            self._last_report = record.created
            self._dropped_records = 0
            self._max_pending = 0

        # Report outside of the locked region to avoid deadlock.
        self._report_stats(interval, dropped_records, max_pending)

    def close(self):
        """
        Extend Handler.close to stop the thread during shutdown.
        """
        logging.Handler.close(self)
        self._queue.append(self._CLOSED)
        with self._cond:
            self._cond.notify()
        self._thread.join()
        self._target = _DROPPER

    # MemoryHandler interface

    def setTarget(self, target):
        """
        Override to use our private target.

        Called from logging.config.fileConfig to configure another handler as
        the target handler.

        Must be called before logging anything to this handler; messages logged
        before setting the target will be dropped silently.
        """
        self._target = target

    def flush(self):
        pass

    # ThreadedHandler interface

    def start(self):
        """
        Start the handler thread, writing queued records to target handler.
        """
        self._thread.start()

    # Private

    def _can_handle(self, record):
        size = len(self._queue)
        self._max_pending = max(size, self._max_pending)
        for level, limit in self._limits:
            if record.levelno <= level:
                return size < limit
        return True

    def _report_stats(self, interval, dropped_records, max_pending):
        if dropped_records:
            # Note: use critical level for better visibility and to prevent
            # filtering out of the message.
            logging.critical(
                "ThreadedHandler is overloaded, dropped %d log messages in "
                "the last %d seconds (max pending: %d)",
                dropped_records, interval, max_pending)
        else:
            logging.debug(
                "ThreadedHandler is ok in the last %d seconds "
                "(max pending: %d)",
                interval, max_pending)

    def _run(self):
        while True:
            # Wait for messages.
            with self._cond:
                while len(self._queue) == 0:
                    self._cond.wait()

            # Handle all pending messages before taking the lock again. Disable
            # flushing while handling pending messages so we do one write()
            # syscall per cycle instead of one write() syscall per record. This
            # improves throuput significantly.
            self._target.buffering = True
            try:
                while len(self._queue):
                    record = self._queue.popleft()
                    if record is self._CLOSED:
                        return
                    self._target.handle(record)
            finally:
                self._target.buffering = False
                self._target.flush()

            # Avoid reference cycles, specially exc_info that may hold a
            # traceback objects.
            record = None


class _Dropper(object):

    def handle(self, record):
        pass


_DROPPER = _Dropper()


class Suppressed(object):

    def __init__(self, value):
        self._value = value

    @property
    def value(self):
        return self._value

    def __repr__(self):
        return '(suppressed)'


class AllVmStatsValue(Suppressed):

    def __repr__(self):
        return repr({vm.get('vmId'): vm.get('status') for vm in self._value})


def set_level(level_name, name=''):
    log_level = logging.getLevelName(level_name)
    if not isinstance(log_level, type(logging.DEBUG)):
        raise ValueError("unknown log level: %r" % level_name)

    log_name = None if not name else name
    # getLogger() default argument is None, not ''
    logger = logging.getLogger(log_name)
    logging.info(
        'Setting log level on %r to %s (%d)',
        logger.name, level_name, log_level)
    logger.setLevel(log_level)


def volume_chain_to_str(base_first_chain):
    """
    Converts an iterable of volume UUIDs into a standard loggable
    format.  The first UUID should be the base (or oldest ancestor) and
    each subsequent entry a direct descendant of its predecessor.
    """
    return ' < '.join(base_first_chain) + " (top)"


def traceback(log=None, msg="Unhandled exception"):
    """
    Log a traceback for unhandled execptions.

    :param log: Use specific logger instead of root logger
    :type log: `logging.Logger`
    :param msg: Use specified message for the exception
    :type msg: str
    """
    def decorator(f):
        @functools.wraps(f)
        def wrapper(*a, **kw):
            try:
                return f(*a, **kw)
            except Exception:
                logger = log or logging.getLogger()
                logger.exception(msg)
                raise  # Do not swallow
        return wrapper
    return decorator
