#
# Copyright 2015-2017 Red Hat, Inc.
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

from vdsm.common import commands
from . import constants


AUTOMATIC = "auto"

_SYS_ONLINE_CPUS = "/sys/devices/system/cpu/online"


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

    out = commands.run(command, reset_cpu_affinity=False).splitlines()

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

    commands.run(command, reset_cpu_affinity=False)


def online_cpus():
    """
    Return a frozenset which contains identifiers of online CPUs,
    as non-negative integers.
    """
    with open(_SYS_ONLINE_CPUS, 'r') as src:
        return cpulist_parse(src.readline())


def pick_cpu(cpu_set):
    """
    Select the best CPU VDSM should pin to.
    `cpu_set' is any iterable which produces the sequence of all
    available CPUs, among which VDSM should pick the best one.
    """
    cpu_list = sorted(cpu_set)
    return cpu_list[:2][-1]


def _cpu_set_from_output(line):
    """
    Parse the output of taskset, in the format
    pid ${PID}'s current affinity mask: ${HEXMASK}
    and return a list of strings, each one being is a cpu index.
    """
    hexmask = line.decode().rsplit(":", 1)[1].strip()
    mask = int(hexmask, 16)
    return frozenset(i for i in range(mask.bit_length()) if mask & (1 << i))


def cpulist_parse(cpu_range):
    """
    Expand the kernel cpulist syntax (e.g. 0-2,5) into a plain
    frozenset of integers (e.g. frozenset([0,1,2,5]))
    The input format is like the content of the special file
    /sys/devices/system/cpu/online
    or the output of the 'taskset' and 'lscpu' tools.
    """
    cpus = []
    for item in cpu_range.split(','):
        if '-' in item:
            begin, end = item.split('-', 1)
            cpus.extend(range(int(begin), int(end) + 1))
        else:
            cpus.append(int(item))
    return frozenset(cpus)
