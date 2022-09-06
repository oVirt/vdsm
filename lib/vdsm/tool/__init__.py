# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import
from __future__ import division
import functools
import os


class expose(object):
    def __init__(self, name):
        self.name = name

    def __call__(self, fun):
        fun._vdsm_tool = {"name": self.name}
        return fun


def requiresRoot(func):
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        if os.geteuid() != 0:
            raise NotRootError()
        func(*args, **kwargs)
    return wrapper


class UsageError(RuntimeError):
    """ Raise on runtime when usage is invalid """


class NotRootError(UsageError):
    def __init__(self):
        super(NotRootError, self).__init__("Must run as root")


class ExtraArgsError(UsageError):
    def __init__(self, n=0):
        if n == 0:
            message = "Command does not take extra arguments"
        else:
            message = \
                "Command takes exactly %d argument%s" % (n,
                                                         's' if n != 1 else '')
        super(ExtraArgsError, self).__init__(message)
