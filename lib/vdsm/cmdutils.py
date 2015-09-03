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
"""This is only a partial version of the module that exists in the master
branch. It was backported here only to support systemd_run for the use of
network configurators."""

from . import constants


def systemd_run(cmd, scope=False, unit=None, slice=None):
    # this is supported only on OS versions that use systemd (el7 and later).
    command = [constants.EXT_SYSTEMD_RUN]
    if scope:
        command.append('--scope')
    if unit:
        command.append('--unit=%s' % unit)
    if slice:
        command.append('--slice=%s' % slice)
    command.extend(cmd)
    return command


def taskset(cmd, cpu_list):
    command = [constants.EXT_TASKSET, "--cpu-list", ",".join(cpu_list)]
    command.extend(cmd)
    return command
