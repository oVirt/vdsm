# Copyright 2017 Red Hat, Inc.
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

import errno
import os
import subprocess
import time

from vdsm import constants

from . import YES, NO


BONDING_DEFAULTS = constants.P_VDSM + 'bonding-defaults.json'


def isconfigured():
    try:
        modified_time = os.path.getmtime(BONDING_DEFAULTS)
    except OSError as e:
        if e.errno != errno.ENOENT:
            raise
        return NO

    boot_time = time.time() - _get_uptime()
    return YES if modified_time >= boot_time else NO


def configure():
    subprocess.call(['/usr/bin/vdsm-tool', 'dump-bonding-options'])


def _get_uptime():
    with open('/proc/uptime', 'r') as f:
        uptime_seconds = float(f.readline().split()[0])
    return uptime_seconds
