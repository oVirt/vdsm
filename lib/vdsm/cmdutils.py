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

from __future__ import absolute_import
import os
import re

from . import constants

SUDO_NON_INTERACTIVE_FLAG = "-n"


def nice(cmd, nice):
    command = [constants.EXT_NICE, '-n', str(nice)]
    command.extend(cmd)
    return command


def ionice(cmd, ioclass, ioclassdata=None):
    command = [constants.EXT_IONICE, '-c', str(ioclass)]
    if ioclassdata is not None:
        command.extend(('-n', str(ioclassdata)))
    command.extend(cmd)
    return command


def setsid(cmd):
    command = [constants.EXT_SETSID]
    command.extend(cmd)
    return command


def sudo(cmd):
    if os.geteuid() == 0:
        return cmd
    command = [constants.EXT_SUDO, SUDO_NON_INTERACTIVE_FLAG]
    command.extend(cmd)
    return command


def systemd_run(cmd, scope=False, unit=None, slice=None):
    command = [constants.EXT_SYSTEMD_RUN]
    if scope:
        command.append('--scope')
    if unit:
        command.append('--unit=%s' % unit)
    if slice:
        command.append('--slice=%s' % slice)
    command.extend(cmd)
    return command


# This function returns truthy value if its argument contains unsafe characters
# for including in a command passed to the shell. The safe characters were
# stolen from pipes._safechars.
_needs_quoting = re.compile(r'[^A-Za-z0-9_%+,\-./:=@]').search


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


def command_log_line(args, cwd=None):
    return "{0} (cwd {1})".format(_list2cmdline(args), cwd)


def retcode_log_line(code, err=None):
    result = "SUCCESS" if code == 0 else "FAILED"
    return "{0}: <err> = {1!r}; <rc> = {2!r}".format(result, err, code)
