# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import
from __future__ import division
"""
This module provides cpu profiling.
"""

from functools import wraps
import logging
import threading

from vdsm.config import config

from .errors import UsageError

# Import yappi lazily when profile is started
yappi = None

# Defaults

_lock = threading.Lock()
_profiler = None


class Profiler(object):

    def __init__(self, filename, format='pstat', clock='cpu', builtins=True,
                 threads=True):
        self.filename = filename
        self.format = format
        self.clock = clock
        self.builtins = builtins
        self.threads = threads

    def start(self):
        # Lazy import so we do not effect runtime environment if profiling is
        # not used.
        global yappi
        import yappi  # pylint: disable=import-error

        # yappi start semantics are a bit too liberal, returning success if
        # yappi is already started, happily having two different code paths
        # that thinks they own the single process profiler.
        if yappi.is_running():
            raise UsageError('CPU profiler is already running')

        logging.info("Starting CPU profiling")
        yappi.set_clock_type(self.clock)
        yappi.start(builtins=self.builtins, profile_threads=self.threads)

    def stop(self):
        if not yappi.is_running():
            raise UsageError("CPU profiler is not running")

        logging.info("Stopping CPU profiling")
        yappi.stop()
        stats = yappi.get_func_stats()
        stats.save(self.filename, self.format)
        yappi.clear_stats()

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, t, v, tb):
        try:
            self.stop()
        except Exception:
            if t is None:
                raise
            # Do not hide original exception
            logging.exception("Error stopping profiler")


def start():
    """ Starts application wide CPU profiling """
    global _profiler
    if is_enabled():
        with _lock:
            if _profiler:
                raise UsageError('CPU profiler is already running')
            _profiler = Profiler(
                config.get('devel', 'cpu_profile_filename'),
                format=config.get('devel', 'cpu_profile_format'),
                clock=config.get('devel', 'cpu_profile_clock'),
                builtins=config.getboolean('devel', 'cpu_profile_builtins'),
                threads=True)
            _profiler.start()


def stop():
    """ Stops application wide CPU profiling """
    global _profiler
    if is_enabled():
        with _lock:
            _profiler.stop()
            _profiler = None


def is_enabled():
    return config.getboolean('devel', 'cpu_profile_enable')


def is_running():
    with _lock:
        return yappi and yappi.is_running()


def profile(filename, format='pstat', clock='cpu', builtins=True,
            threads=True):
    """
    Profile decorated function, saving profile to filename using format.

    Note: you cannot use this when the application wide profile is enabled, or
    profile multiple functions in the same code path.
    """
    def decorator(f):
        @wraps(f)
        def wrapper(*a, **kw):
            profiler = Profiler(filename, format=format, clock=clock,
                                builtins=builtins, threads=threads)
            with profiler:
                return f(*a, **kw)
        return wrapper
    return decorator
