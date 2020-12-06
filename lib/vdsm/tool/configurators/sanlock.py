# Copyright 2014-2020 Red Hat, Inc.
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

import grp
import os
import re
import sys

from vdsm import constants
from vdsm.common import cmdutils
from vdsm.common import commands

from . import YES, NO

PID_FILE = "/run/sanlock/sanlock.pid"
REQUIRED_GROUPS = {constants.QEMU_PROCESS_GROUP, constants.VDSM_GROUP}

# Configuring requires stopping sanlock.
services = ('sanlock',)


def isconfigured():
    """
    Return YES if sanlock is configured, NO if sanlock need to be configured or
    restarted to pick up the configuration.
    """
    if not _groups_configured():
        _log("sanlock requires configuration")
        return NO

    if _restart_needed():
        _log("sanlock requires a restart")
        return NO

    _log("sanlock is configured for vdsm")
    return YES


def configure():
    """
    Configure sanlock for vdsm. This will be applied when sanlock is started
    after configuration.
    """
    _log("configuring sanlock groups")
    _configure_groups()


def _configure_groups():
    try:
        commands.run([
            '/usr/sbin/usermod',
            '-a',
            '-G',
            ','.join(REQUIRED_GROUPS),
            constants.SANLOCK_USER
        ])
    except cmdutils.Error as e:
        raise RuntimeError("Failed to perform sanlock config: {}".format(e))


def _groups_configured():
    """
    Return True if sanlock user is a member of all required groups.
    """
    actual_groups = {g.gr_name for g in grp.getgrall()
                     if constants.SANLOCK_USER in g.gr_mem}

    return REQUIRED_GROUPS.issubset(actual_groups)


def _restart_needed():
    """
    Return True if sanlock daemon is running without the required supplementary
    groups. Sanlock will apply the groups after restart.
    """
    try:
        with open(PID_FILE) as f:
            sanlock_pid = f.readline().strip()
    except FileNotFoundError:
        return False

    proc_status = os.path.join('/proc', sanlock_pid, 'status')
    try:
        with open(proc_status) as f:
            status = f.read()
    except FileNotFoundError:
        return False

    match = re.search(r"^Groups:\t?(.*)$", status, re.MULTILINE)
    if not match:
        return True

    actual_gids = {int(s) for s in match.group(1).split()}
    required_gids = {grp.getgrnam(name).gr_gid for name in REQUIRED_GROUPS}

    return not required_gids.issubset(actual_gids)


# TODO: use standard logging
def _log(fmt, *args):
    sys.stdout.write(fmt % args + "\n")
