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
