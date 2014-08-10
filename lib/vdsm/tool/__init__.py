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
        if os.getuid() != 0:
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
