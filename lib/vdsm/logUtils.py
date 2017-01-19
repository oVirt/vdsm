#
# Copyright 2011 Red Hat, Inc.
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

import grp
import logging
import logging.handlers
import os
import pwd
import sys
from functools import wraps
from inspect import ismethod


def funcName(func):
    if ismethod(func):
        return func.__func__.__name__

    if hasattr(func, 'func'):
        return func.func.__name__

    return func.__name__


def logcall(loggerName, pattern="%s", loglevel=logging.INFO, printers={},
            resPrinter=repr, resPattern="%(name)s->%(result)s"):
    def phase2(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            logger = logging.getLogger(loggerName)
            logger.log(loglevel, pattern %
                       (call2str(f, args, kwargs, printers),))
            res = f(*args, **kwargs)
            logger.log(loglevel, resPattern %
                       {"name": funcName(f), "result": resPrinter(res)})
            return res

        return wrapper

    return phase2


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
        items = ", ".join("%s='%s'" % (k, v) for k, v in context.iteritems())
        self.prefix = "(%s) " % items

    def process(self, msg, kwargs):
        return self.prefix + msg, kwargs


class TracebackRepeatFilter(logging.Filter):
    """
    Makes sure a traceback is logged only once for each exception.
    """
    def filter(self, record):
        if not record.exc_info:
            return 1

        info = sys.exc_info()
        ex = info[1]
        if ex is None:
            return 1

        if hasattr(ex, "_logged") and ex._logged:
            record.exc_info = False
            ex._logged = True

        return 1


class QueueHandler(logging.Handler):
    """
    This handler sends events to a queue. Typically, it would be used together
    with a multiprocessing Queue to centralise logging to file in one process
    (in a multi-process application), so as to avoid file write contention
    between processes.

    This code is new in Python 3.2, but this class can be copy pasted into
    user code for use with earlier Python versions.
    """

    def __init__(self, queue):
        """
        Initialise an instance, using the passed queue.
        """
        logging.Handler.__init__(self)
        self.queue = queue

    def enqueue(self, record):
        """
        Enqueue a record.

        The base implementation uses put_nowait. You may want to override
        this method if you want to use blocking, timeouts or custom queue
        implementations.
        """
        self.queue.put_nowait(record)

    def emit(self, record):
        """
        Emit a record.

        Writes the LogRecord to the queue, preparing it for pickling first.
        """
        try:
            # The format operation gets traceback text into record.exc_text
            # (if there's exception data), and also puts the message into
            # record.message. We can then use this to replace the original
            # msg + args, as these might be unpickleable. We also zap the
            # exc_info attribute, as it's no longer needed and, if not None,
            # will typically not be pickleable.
            self.format(record)
            record.msg = record.message
            record.args = None
            record.exc_info = None
            self.enqueue(record)
        except (KeyboardInterrupt, SystemExit):
            raise
        except:
            self.handleError(record)


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

        # To trigger cred check:
        self._open()

    def _open(self):
        if (os.geteuid() != self._uid) or (os.getegid() != self._gid):
            raise RuntimeError(
                "Attempt to open log with incorrect credentials")
        return logging.handlers.WatchedFileHandler._open(self)


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
    logging.warning('Setting loglevel on %r to %s (%d)',
                    logger.name, level_name, log_level)
    logger.setLevel(log_level)
