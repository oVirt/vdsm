# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import

import os

from vdsm.common import commands
from vdsm.common import supervdsm
from vdsm.constants import EXT_DMSETUP


def status(target=None):
    lines = run_status(target=target).decode("utf-8").splitlines()

    # Handle the special "No devices found" case.
    # See https://bugzilla.redhat.com/1787541
    if len(lines) == 1 and ":" not in lines[0]:
        return

    for line in lines:
        name, status = line.split(":", 1)
        yield name, status


def run_status(target=None):
    if os.geteuid() != 0:
        return supervdsm.getProxy().dmsetup_run_status(target)

    cmd = [EXT_DMSETUP, "status"]
    if target is not None:
        cmd.extend(["--target", target])

    out = commands.run(cmd)
    return out
