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
systemctl - wrapper for systemctl command.
"""

from __future__ import absolute_import
from __future__ import division

import os

from . import commands
from . import supervdsm

SYSTEMCTL = "/usr/bin/systemctl"


def stop(pattern):
    """
    Stop (deactivate) one or more units specified in pattern.

    If not running as root, use supervdsm to invoke this function as root.
    """
    if os.geteuid() != 0:
        return supervdsm.getProxy().systemctl_stop(pattern)

    return commands.run([SYSTEMCTL, 'stop', pattern])


def enable(pattern):
    """
    Enable one or more units specified in pattern.

    If not running as root, use supervdsm to invoke this function as root.
    """
    if os.geteuid() != 0:
        return supervdsm.getProxy().systemctl_enable(pattern)

    return commands.run([SYSTEMCTL, 'enable', pattern])


def show(pattern=None, properties=()):
    """
    Show properties of one or more units, jobs, or the manager itself.

    If properties are not specified, show all properties. It seems that the
    available properties and their values are not documented.
    """
    cmd = [SYSTEMCTL, "show"]

    if properties:
        keys = ",".join(properties)
        cmd.append("--property=" + keys)

    if pattern:
        cmd.append(pattern)

    out = commands.run(cmd).decode("utf-8")
    return _parse_properties(out)


def _parse_properties(out):
    """
    Parse systemctl show properties lists.

    Convert:

        Names=foo.service
        LoadState=masked
        ActiveState=inactive

        Names=bar.socket
        LoadState=masked
        ActiveState=inactive

    To:

       [
          {
               "ActiveState": "inactive"
               "LoadState": "masked",
               "Names": "foo.service"
           },
           {
               "ActiveState": "inactive"
               "LoadState": "masked",
               "Names": "foo.socket"
           },
       ]
    """
    # When nothing matches, we get empty output.
    out = out.strip()
    if not out:
        return []

    # Some matches - parse the lines.
    return [dict(pair.split("=", 1) for pair in unit.split("\n"))
            for unit in out.split("\n\n")]
