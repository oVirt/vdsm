#
# Copyright 2014 Red Hat, Inc.
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
This module provides cpu profiling.
"""

from functools import wraps
import logging
import os
import threading

from vdsm import constants
from vdsm.config import config

# Import yappi lazily when profile is started
yappi = None

_FILENAME = os.path.join(constants.P_VDSM_RUN, 'vdsmd.prof')
_FORMAT = config.get('vars', 'profile_format')
_BUILTINS = config.getboolean('vars', 'profile_builtins')

_lock = threading.Lock()


class Error(Exception):
    """ Raised when profiler is used incorrectly """


def start():
    """ Starts application wide profiling """
    if is_enabled():
        _start_profiling(_BUILTINS)


def stop():
    """ Stops application wide profiling """
    if is_enabled():
        _stop_profiling(_FILENAME, _FORMAT)


def is_enabled():
    return config.getboolean('vars', 'profile_enable')


def is_running():
    with _lock:
        return yappi and yappi.is_running()


def profile(filename, format=_FORMAT, builtins=_BUILTINS):
    """
    Profile decorated function, saving profile to filename using format.

    Note: you cannot use this when the application wide profile is enabled, or
    profile multiple functions in the same code path.
    """
    def decorator(f):
        @wraps(f)
        def wrapper(*a, **kw):
            _start_profiling(builtins)
            try:
                return f(*a, **kw)
            finally:
                _stop_profiling(filename, format)
        return wrapper
    return decorator


def _start_profiling(builtins):
    global yappi
    logging.debug("Starting profiling")
    with _lock:
        import yappi
        # yappi start semantics are a bit too liberal, returning success if
        # yappi is already started, happily having too different code paths
        # that thinks they own the single process profiler.
        if yappi.is_running():
            raise Error('Profiler is already running')
        yappi.start(builtins=builtins)


def _stop_profiling(filename, format):
    logging.debug("Stopping profiling")
    with _lock:
        if yappi.is_running():
            yappi.stop()
            stats = yappi.get_func_stats()
            stats.save(filename, format)
            yappi.clear_stats()
