# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

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
