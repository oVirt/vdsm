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
import sys
import grp
import pwd

from . import YES, NO, MAYBE, InvalidConfig
from ... import utils
from ... import constants

SANLOCK_GROUPS = (constants.QEMU_PROCESS_GROUP, constants.VDSM_GROUP)

services = ('sanlock',)


def configure():
    """
    Configure sanlock process groups
    """
    rc, out, err = utils.execCmd(
        (
            '/usr/sbin/usermod',
            '-a',
            '-G',
            ','.join(SANLOCK_GROUPS),
            constants.SANLOCK_USER
        ),
        raw=True,
    )
    sys.stdout.write(out)
    sys.stderr.write(err)
    if rc != 0:
        raise RuntimeError("Failed to perform sanlock config.")


def isconfigured():
    """
    True if sanlock service is configured, False if sanlock service
    requires a restart to reload the relevant supplementary groups.
    """
    configured = NO
    groups = [g.gr_name for g in grp.getgrall()
              if constants.SANLOCK_USER in g.gr_mem]
    gid = pwd.getpwnam(constants.SANLOCK_USER).pw_gid
    groups.append(grp.getgrgid(gid).gr_name)
    if all(group in groups for group in SANLOCK_GROUPS):
        configured = MAYBE

    if configured == MAYBE:
        try:
            with open("/var/run/sanlock/sanlock.pid", "r") as f:
                sanlock_pid = f.readline().strip()
            with open(os.path.join('/proc', sanlock_pid, 'status'),
                      "r") as sanlock_status:
                proc_status_group_prefix = "Groups:\t"
                for status_line in sanlock_status:
                    if status_line.startswith(proc_status_group_prefix):
                        groups = [int(x) for x in status_line[
                            len(proc_status_group_prefix):]
                            .strip().split(" ") if x]
                        break
                else:
                    raise InvalidConfig(
                        "Unable to find sanlock service groups"
                    )

            is_sanlock_groups_set = True
            for g in SANLOCK_GROUPS:
                if grp.getgrnam(g)[2] not in groups:
                    is_sanlock_groups_set = False
            if is_sanlock_groups_set:
                configured = YES

        except IOError as e:
            if e.errno == os.errno.ENOENT:
                configured = YES
            else:
                raise

    return configured
