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
from .common import errors
from .config import config

SUDO_NON_INTERACTIVE_FLAG = "-n"


class Error(errors.Base):
    msg = ("Command {self.cmd} failed with rc={self.rc} out={self.out!r} "
           "err={self.err!r}")

    def __init__(self, cmd, rc, out, err):
        self.cmd = cmd
        self.rc = rc
        self.out = out
        self.err = err


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


class Accounting(object):
    CPU = 'CPU'
    Memory = 'Memory'
    BlockIO = 'BlockIO'


def systemd_run(cmd, scope=False, unit=None, slice=None, accounting=None):
    command = [constants.EXT_SYSTEMD_RUN]
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


def taskset(cmd, cpu_list):
    command = [constants.EXT_TASKSET, "--cpu-list", ",".join(cpu_list)]
    command.extend(cmd)
    return command


_ANY_CPU = ["0-%d" % (os.sysconf('SC_NPROCESSORS_CONF') - 1)]
_USING_CPU_AFFINITY = config.get('vars', 'cpu_affinity') != ""


def wrap_command(command, with_ioclass=None, ioclassdata=None,
                 with_nice=None, with_setsid=False, with_sudo=False,
                 reset_cpu_affinity=True):
    if with_ioclass is not None:
        command = ionice(command, ioclass=with_ioclass,
                         ioclassdata=ioclassdata)

    if with_nice is not None:
        command = nice(command, nice=with_nice)

    if with_setsid:
        command = setsid(command)

    if with_sudo:
        command = sudo(command)

    # warning: the order of commands matters. If we add taskset
    # after sudo, we'll need to configure sudoers to allow both
    # 'sudo <command>' and 'sudo taskset <command>', which is
    # impractical. On the other hand, using 'taskset sudo <command>'
    # is much simpler and delivers the same end result.

    if reset_cpu_affinity and _USING_CPU_AFFINITY:
        # only VDSM itself should be bound
        command = taskset(command, _ANY_CPU)

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
