# Copyright 2013 Red Hat, Inc.
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

import os
import grp

from .. import constants
from . import expose


SANLOCK_PID = "/var/run/sanlock/sanlock.pid"

PROC_STATUS_PATH = "/proc/%s/status"
PROC_STATUS_GROUPS = "Groups:\t"
PROC_STATUS_GROUPS_LEN = len(PROC_STATUS_GROUPS)


@expose("sanlock-check-service")
def sanlock_check_service(*args):
    """
    Check if sanlock service requires a restart to reload the relevant
    supplementary groups.
    """

    try:
        sanlock_pid = open(SANLOCK_PID, "r").readline().strip()
        sanlock_status = open(PROC_STATUS_PATH % sanlock_pid, "r")
    except IOError as e:
        if e.errno == os.errno.ENOENT:
            return 0  # service is not running, returning
        raise

    for status_line in sanlock_status:
        if status_line.startswith(PROC_STATUS_GROUPS):
            groups = [int(x) for x in
                      status_line[PROC_STATUS_GROUPS_LEN:].strip().split(" ")]
            break
    else:
        raise RuntimeError("Unable to find sanlock service groups")

    diskimage_gid = grp.getgrnam(constants.DISKIMAGE_GROUP)[2]
    return 0 if diskimage_gid in groups else 1
