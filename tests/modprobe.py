#
# Copyright 2015 Red Hat, Inc.
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
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA  02110-1301 USA
#
# Refer to the README and COPYING files for full details of the license
import os
from functools import wraps

from vdsm import utils

modprobe = utils.CommandPath("modprobe",
                             "/sbin/modprobe",      # EL6
                             "/usr/sbin/modprobe",  # Fedora
                             )


def RequireDummyMod(f):
    """
    Assumes root privileges to be used after
    ValidateRunningAsRoot decoration.
    """
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not os.path.exists('/sys/module/dummy'):
            cmd_modprobe = [modprobe.cmd, "dummy"]
            rc, out, err = utils.execCmd(cmd_modprobe, sudo=True)
        return f(*args, **kwargs)
    return wrapper


def RequireBondingMod(f):
    """
    Assumes root privileges to be used after
    ValidateRunningAsRoot decoration.
    """
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not os.path.exists('/sys/module/bonding'):
            cmd_modprobe = [modprobe.cmd, "bonding"]
            rc, out, err = utils.execCmd(cmd_modprobe, sudo=True)
        return f(*args, **kwargs)

    return wrapper


def RequireVethMod(f):
    """
    Assumes root privileges to be used after
    ValidateRunningAsRoot decoration.
    """
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not os.path.exists('/sys/module/veth'):
            cmd_modprobe = [modprobe.cmd, "veth"]
            rc, out, err = utils.execCmd(cmd_modprobe, sudo=True)
        return f(*args, **kwargs)
    return wrapper
