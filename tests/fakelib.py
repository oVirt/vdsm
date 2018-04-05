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
from __future__ import division

import logging
from vdsm import schedule


class FakeScheduler(object):

    def __init__(self):
        self.calls = []

    def schedule(self, delay, callable):
        self.calls.append((delay, callable))
        return schedule.ScheduledCall(delay, callable)


class FakeLogger(object):

    def __init__(self, level=logging.DEBUG):
        self.level = level
        self.messages = []

    def log(self, level, fmt, *args, **kwargs):
        if self.isEnabledFor(level):
            # Will fail if fmt does not match args
            self.messages.append((level, fmt % args, kwargs))

    def debug(self, fmt, *args, **kwargs):
        self.log(logging.DEBUG, fmt, *args, **kwargs)

    def info(self, fmt, *args, **kwargs):
        self.log(logging.INFO, fmt, *args, **kwargs)

    def warning(self, fmt, *args, **kwargs):
        self.log(logging.WARNING, fmt, *args, **kwargs)

    def error(self, fmt, *args, **kwargs):
        self.log(logging.ERROR, fmt, *args, **kwargs)

    def exception(self, fmt, *args):
        self.log(logging.ERROR, fmt, *args, exc_info=True)

    def isEnabledFor(self, level):
        return level >= self.level


class FakeNotifier(object):

    def __init__(self):
        self.calls = []

    def notify(self, event_id, params=None):
        self.calls.append((event_id, params))
