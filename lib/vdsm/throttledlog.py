#
# Copyright 2016-2021 Red Hat, Inc.
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

import logging

from vdsm.common.time import monotonic_time

_DEFAULT_TIMEOUT_SEC = 3600

_logger = logging.getLogger('throttled')

_periodic = {}


class _Periodic(object):

    def __init__(self, interval, timeout):
        self._interval = interval
        self._timeout = timeout
        self._counter = 0
        self._last_time = 0

    def tick(self):
        now = monotonic_time()
        result = self._result(now)
        self._counter = (self._counter + 1) % self._interval
        if result:
            self._last_time = now
        return result

    def _result(self, now):
        return (self._counter == 0 or
                (now - self._last_time) >= self._timeout)


def throttle(name, interval, timeout=_DEFAULT_TIMEOUT_SEC):
    """
    Throttle log messages for `name`, logging at most one message per
    `interval` calls or always after `timeout` seconds of silence.  Throttling
    applies only to logging performed via `log()` function of this module.  The
    first call of `log()` never throttles the log, following calls are
    throttled according to the given parameters.

    If this function has already been called for `name`, replace the throttling
    parameters for `name` with the new ones given here and start throttling
    from beginning.

    :param name: Arbitrary identifier to be matched in `log()` calls.
    :type name: basestring
    :param interval: The number of `log()` calls that should log at least once.
    :type interval: int
    :param timeout: The number of seconds without log emitted after which
      `log()` should always unthrottle the next message.
    :type timeout: int
    """
    _periodic[name] = _Periodic(interval, timeout)


def log(name, level, message, *args):
    """
    Log `message` and `args` if throttling settings for `name` allow it.
    See `throttle()` for information about throttling and `name`.
    `level`, `message` and `args` are passed to `logging.Logger.log()`
    unchanged.

    :param name: Arbitrary identifier to be matched by `throttle()` settings.
    :type name: basestring

    .. note::

      Depending on throttling settings and the current logging level `message`
      and `args` may not be logged at all.  So don't perform expensive
      preprocessing of `args` before calling this function.  If you need to
      modify it before logging it, you may want to use something like
      `vdsm.common.password.HiddenValue`.
    """
    try:
        periodic = _periodic[name]
    except KeyError:
        pass  # unthrottled
    else:
        if not periodic.tick():
            return

    _logger.log(level, message, *args)


def debug(name, message, *args):
    log(name, logging.DEBUG, message, *args)


def info(name, message, *args):
    log(name, logging.INFO, message, *args)


def warning(name, message, *args):
    log(name, logging.WARNING, message, *args)
