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
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA  02110-1301 USA
#
# Refer to the README and COPYING files for full details of the license
#
import logging

from vdsm import throttledlog

from monkeypatch import MonkeyPatch
from testlib import VdsmTestCase


class FakeLogger(object):

    def __init__(self, level):
        self.level = level
        self.messages = []

    def isEnabledFor(self, level):
        return level >= self.level

    def log(self, level, message, *args):
        if not self.isEnabledFor(level):
            return
        self.messages.append(message % args)


class FakeTime(object):

    def __init__(self):
        self.time = 0.0

    def __call__(self):
        return self.time


class TestThrottledLogging(VdsmTestCase):

    @MonkeyPatch(throttledlog, "_logger", FakeLogger(logging.DEBUG))
    def test_throttled_logging(self):
        throttledlog.throttle('test', 3)
        for i in range(5):
            throttledlog.debug('test', "Cycle: %s", i)
        self.assertEqual(throttledlog._logger.messages,
                         ['Cycle: 0', 'Cycle: 3'])

    @MonkeyPatch(throttledlog, "_logger", FakeLogger(logging.INFO))
    def test_no_logging(self):
        throttledlog.throttle('test', 3)
        for i in range(5):
            throttledlog.debug('test', "Cycle: %s", i)
        self.assertEqual(throttledlog._logger.messages, [])

    @MonkeyPatch(throttledlog, "_logger", FakeLogger(logging.DEBUG))
    def test_default(self):
        throttledlog.throttle('test', 3)
        for i in range(5):
            throttledlog.debug('other', "Cycle: %s", i)
        self.assertEqual(throttledlog._logger.messages,
                         ['Cycle: %s' % (i,) for i in range(5)])

    @MonkeyPatch(throttledlog, "_logger", FakeLogger(logging.DEBUG))
    @MonkeyPatch(throttledlog, "monotonic_time", FakeTime())
    def test_timeout(self):
        throttledlog.throttle('test', 10, timeout=7)
        for i in range(12):
            throttledlog.debug('test', "Cycle: %s", i)
            throttledlog.monotonic_time.time += 1.0
        self.assertEqual(throttledlog._logger.messages,
                         ['Cycle: %s' % (i,) for i in (0, 7, 10,)])

    @MonkeyPatch(throttledlog, "_logger", FakeLogger(logging.WARNING))
    def test_logging_warning(self):
        throttledlog.throttle('test', 4)
        for i in range(7):
            throttledlog.warning('test', "Cycle: %s", i)
        self.assertEqual(throttledlog._logger.messages,
                         ['Cycle: 0', 'Cycle: 4'])
