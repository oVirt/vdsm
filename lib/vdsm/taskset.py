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
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA
#
# Refer to the README and COPYING files for full details of the license
#

from __future__ import absolute_import

from . import cmdutils
from . import constants
from . import utils


def get(pid):
    """
    Get the affinity of a process, by its <pid>, using taskset command.
    We assume all threads of the process have the same affinity, because
    this is the only usecase VDSM cares about - and requires.
    Return a frozenset of ints, each one being a cpu indices on which the
    process can run.
    Example: frozenset([0, 1, 2, 3])
    Raise cmdutils.Error on failure.
    """
    command = [constants.EXT_TASKSET, '--pid', str(pid)]

    rc, out, err = utils.execCmd(command, resetCpuAffinity=False)

    if rc != 0:
        raise cmdutils.Error(rc, out, err)

    return _cpu_set_from_output(out[-1])


def set(pid, cpu_set, all_tasks=False):
    """
    Set the affinity of a process, by its <pid>, using taskset command.
    if all_tasks evaluates to True, set the affinity for all threads of
    the target process.
    <cpu_set> must be an iterable whose items are ints which represent
    cpu indices, on which the process will be allowed to run; the format
    is the same as what the get() function returns.
    Raise cmdutils.Error on failure.
    """
    command = [constants.EXT_TASKSET]
    if all_tasks:
        command.append("--all-tasks")

    command.extend((
                   '--pid',
                   '--cpu-list', ','.join(str(i) for i in cpu_set),
                   str(pid)
                   ))

    rc, out, err = utils.execCmd(command, resetCpuAffinity=False)

    if rc != 0:
        raise cmdutils.Error(rc, out, err)


def _cpu_set_from_output(line):
    """
    Parse the output of taskset, in the format
    pid ${PID}'s current affinity mask: ${HEXMASK}
    and return a list of strings, each one being is a cpu index.
    """
    hexmask = line.rsplit(":", 1)[1].strip()
    mask = int(hexmask, 16)
    return frozenset(i for i in range(mask.bit_length()) if mask & (1 << i))
