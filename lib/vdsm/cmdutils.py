#
# Copyright 2014 Red Hat, Inc.
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

from . import constants

SUDO_NON_INTERACTIVE_FLAG = "-n"


def nice(cmd, nice):
    command = [constants.EXT_NICE, '-n', str(nice)]
    command.extend(cmd)
    return command


def ionice(cmd, ioclass, ioclassdata=None):
    command = [constants.EXT_IONICE, '-c', str(ioclass)]
    if ioclassdata is not None:
        command.extend(('-n', str(ioclassdata)))
    command.extend(cmd)
    return command


def setsid(cmd):
    command = [constants.EXT_SETSID]
    command.extend(cmd)
    return command


def sudo(cmd):
    if os.geteuid() == 0:
        return cmd
    command = [constants.EXT_SUDO, SUDO_NON_INTERACTIVE_FLAG]
    command.extend(cmd)
    return command
