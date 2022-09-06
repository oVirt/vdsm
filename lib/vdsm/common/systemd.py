# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

"""
systemd - wrapper for systemd-run command.
"""

from __future__ import absolute_import
from __future__ import division

from . import commands

SYSTEMD_RUN = "/usr/bin/systemd-run"


class Accounting(object):
    CPU = 'CPU'
    Memory = 'Memory'
    BlockIO = 'BlockIO'


def run(cmd, scope=False, unit=None, slice=None, uid=None, gid=None,
        accounting=None):
    """
    Run a command using systemd-run.

    Caller must run as root, otherwise the call will fail.
    """
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
