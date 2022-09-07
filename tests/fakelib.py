# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

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
