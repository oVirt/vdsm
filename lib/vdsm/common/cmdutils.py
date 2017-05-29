# Copyright 2017 Red Hat, Inc.
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

import distutils.spawn
import os
import re


class CommandPath(object):
    def __init__(self, name, *args, **kwargs):
        self.name = name
        self.paths = args
        self._cmd = None
        self._search_path = kwargs.get('search_path', True)

    @property
    def cmd(self):
        if not self._cmd:
            for path in self.paths:
                if os.path.exists(path):
                    self._cmd = path
                    break
            else:
                if self._search_path:
                    self._cmd = distutils.spawn.find_executable(self.name)
                if self._cmd is None:
                    raise OSError(os.errno.ENOENT,
                                  os.strerror(os.errno.ENOENT) + ': ' +
                                  self.name)
        return self._cmd

    def __repr__(self):
        return str(self.cmd)

    def __str__(self):
        return str(self.cmd)

    def __unicode__(self):
        return unicode(self.cmd)


def command_log_line(args, cwd=None):
    return "{0} (cwd {1})".format(_list2cmdline(args), cwd)


def retcode_log_line(code, err=None):
    result = "SUCCESS" if code == 0 else "FAILED"
    return "{0}: <err> = {1!r}; <rc> = {2!r}".format(result, err, code)


def _list2cmdline(args):
    """
    Convert argument list for exeCmd to string for logging. The purpose of this
    log is make it easy to run vdsm commands in the shell for debugging.
    """
    parts = []
    for arg in args:
        if _needs_quoting(arg) or arg == '':
            arg = "'" + arg.replace("'", r"'\''") + "'"
        parts.append(arg)
    return ' '.join(parts)


# This function returns truthy value if its argument contains unsafe characters
# for including in a command passed to the shell. The safe characters were
# stolen from pipes._safechars.
_needs_quoting = re.compile(r'[^A-Za-z0-9_%+,\-./:=@]').search
