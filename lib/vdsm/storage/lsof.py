#
# Copyright 2020 Red Hat, Inc.
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

import logging
import os

from vdsm.common import cmdutils
from vdsm.common import commands
from vdsm.common import supervdsm

LSOF = cmdutils.CommandPath('lsof', '/usr/bin/lsof')

log = logging.getLogger("storage.lsof")


def run(path):
    if os.geteuid() != 0:
        return supervdsm.getProxy().lsof_run(path)

    cmd = [
        LSOF.cmd,
        # Produce 4 lines per each process:
        # p<PID>
        # c<COMMAND>
        # L<USER>
        # f<FD>
        "-FcLpf",
        path
    ]

    try:
        return commands.run(cmd)
    except cmdutils.Error as e:
        # lsof returns rc=1 either when no processes are found or on error.
        # TODO: Add debug log precondition once BZ#1854048 is fixed.
        log.debug("lsof failed: %s", e)
        return None


def proc_info(path):
    out = run(path)
    if out is None:
        return

    lines = out.decode("utf-8").splitlines()

    # lsof runs with -FcLpf, producing a 4 lines format per each process:
    # p<PID>
    # c<COMMAND>
    # L<USER>
    # f<FD>
    for i in range(0, len(lines), 4):
        record = {}
        try:
            for item in lines[i:i + 4]:
                key, value = item[0], item[1:]
                if key == "p":
                    record["pid"] = int(value)
                elif key == "c":
                    record["command"] = value
                elif key == "L":
                    record["user"] = value
                elif key == "f":
                    record["fd"] = int(value)
                else:
                    log.warning("Unexpected key=%r value=%r", key, value)
        except Exception as e:
            log.warning("Failed to parse lines %r: %s", lines[i:i + 4], e)
            continue

        yield record
