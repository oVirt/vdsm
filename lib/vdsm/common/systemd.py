# Copyright 2018 Red Hat, Inc.
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
"""
systemd - wrapper for systemd-run command.
"""

from __future__ import absolute_import
from __future__ import division

import os

from . import commands
from . import supervdsm

SYSTEMD_RUN = "/usr/bin/systemd-run"


class Accounting(object):
    CPU = 'CPU'
    Memory = 'Memory'
    BlockIO = 'BlockIO'


def run(cmd, scope=False, unit=None, slice=None, uid=None, gid=None,
        accounting=None):
    """
    Run a command using systemd-run.

    If not running as root, use supervdsm to invoke this function as root.
    """

    if os.geteuid() != 0:
        return supervdsm.getProxy().systemd_run(
            cmd,
            scope=scope,
            unit=unit,
            slice=slice,
            uid=uid,
            gid=gid,
            accounting=accounting)

    cmd = wrap(
        cmd,
        scope=scope,
        unit=unit,
        slice=slice,
        uid=uid,
        gid=gid,
        accounting=accounting)

    return commands.run(cmd)


def wrap(cmd, scope=False, unit=None, slice=None, uid=None, gid=None,
         accounting=None):
    """
    Wrap a command with systemd-run invocation.

    Should be used if you already run as root, and need to invoke a command via
    systemd-run only in some cases.
    """
    command = [SYSTEMD_RUN]
    if scope:
        command.append('--scope')
    if unit:
        command.append('--unit=%s' % unit)
    if slice:
        command.append('--slice=%s' % slice)
    if uid is not None:
        command.append('--uid=%s' % uid)
    if gid is not None:
        command.append('--gid=%s' % gid)
    if accounting is not None:
        command.extend(['--property={}Accounting=1'.format(acct)
                        for acct in accounting])
    command.extend(cmd)
    return command
