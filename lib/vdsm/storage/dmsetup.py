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
