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
import logging
import os
import re
import subprocess

from vdsm.common.compat import CPopen


SYSTEMD_RUN = "/usr/bin/systemd-run"


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


def systemd_run(cmd, scope=False, unit=None, slice=None, accounting=None):
    command = [SYSTEMD_RUN]
    if scope:
        command.append('--scope')
    if unit:
        command.append('--unit=%s' % unit)
    if slice:
        command.append('--slice=%s' % slice)
    if accounting is not None:
        command.extend(['--property={}Accounting=1'.format(acct)
                        for acct in accounting])
    command.extend(cmd)
    return command


class Accounting(object):
    CPU = 'CPU'
    Memory = 'Memory'
    BlockIO = 'BlockIO'


def exec_cmd(cmd, env=None):
    """
    Execute cmd in an external process, collect its output and returncode

    :param cmd: an iterator of strings to be passed as exec(2)'s argv
    :param env: an optional dictionary to be placed as environment variables
                of the external process. If None, the environment of the
                calling process is used.
    :returns: a 3-tuple of the process's
              (returncode, stdout content, stderr content.)

    This is a bare-bones version of `commands.execCmd`. Unlike the latter, this
    function
    * uses Vdsm cpu pinning, and must not be used for long CPU-bound processes.
    * does not guarantee to kill underlying process if CPopen.communicate()
      raises. Commands that access shared storage may not use this api.
    * does not hide passwords in logs if they are passed in cmd
    """
    logging.debug(command_log_line(cmd))

    p = CPopen(
        cmd, close_fds=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        env=env)

    out, err = p.communicate()

    logging.debug(retcode_log_line(p.returncode, err=err))

    return p.returncode, out, err
