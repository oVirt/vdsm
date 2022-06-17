#
# Copyright 2022 Red Hat, Inc.
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
Wrapper for running and formatting LVM commands.
"""

import re
import logging

from vdsm.common import commands
from vdsm.common.compat import subprocess
from vdsm.storage import exception as se

log = logging.getLogger("storage.lvmcmd")


class LVMRunner(object):
    """
    Does actual execution of the LVM command and handle output, e.g. decode
    output or log warnings.
    """

    # Warnings written to LVM stderr that should not be logged as warnings.
    SUPPRESS_WARNINGS = re.compile(
        "|".join([
            "WARNING: This metadata update is NOT backed up",
            (r"WARNING: ignoring metadata seqno \d+ on /dev/mapper/\w+ for "
             r"seqno \d+ on /dev/mapper/\w+ for VG \w+"),
            r"WARNING: Inconsistent metadata found for VG \w+",
            ("WARNING: Activation disabled. No device-mapper interaction "
             "will be attempted"),
        ]),
        re.IGNORECASE)

    def run(self, cmd):
        """
        Run LVM command, logging warnings for successful commands.

        An example case is when LVM decide to fix VG metadata when running a
        command that should not change the metadata on non-SPM host. In this
        case LVM will log this warning:

            WARNING: Inconsistent metadata found for VG xxx-yyy-zzz - updating
            to use version 42

        We log warnings only for successful commands since callers are already
        handling failures.
        """

        rc, out, err = self._run_command(cmd)

        out = out.decode("utf-8").splitlines()
        err = err.decode("utf-8").splitlines()

        err = [s for s in err if not self.SUPPRESS_WARNINGS.search(s)]

        if rc == 0 and err:
            log.warning("Command %s succeeded with warnings: %s", cmd, err)

        if rc != 0:
            raise se.LVMCommandError(cmd, rc, out, err)

        return out

    def _run_command(self, cmd):
        p = commands.start(
            cmd,
            sudo=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )
        out, err = commands.communicate(p)
        return p.returncode, out, err
