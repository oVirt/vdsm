#
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
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA  02110-1301 USA
#
# Refer to the README and COPYING files for full details of the license
#

from __future__ import absolute_import

import os
import collections
import threading

from vdsm import cpuarch
from vdsm import supervdsm
from vdsm.common import cache


_PATH = '/sys/kernel/mm/hugepages'
_VM = '/proc/sys/vm/'

_LOCK = threading.Lock()

DEFAULT_HUGEPAGESIZE = {
    cpuarch.X86_64: 2048,
    cpuarch.PPC64LE: 16384,
}


class NonContignuousMemory(Exception):
    """Raised when the memory is too fragmented to allocate hugepages"""


@cache.memoized
def supported(path=_PATH):
    """Small cached helper to get available hugepage sizes.

    Cached as the sizes don't change in the system's runtime.

    Args:
        path: A path to the hugepages directory. (mostly for testing purposes)

    Returns:
        A list of supported hugepage sizes available on the system.
    """
    return state(path).keys()


def alloc(count, size=None,
          path='/sys/kernel/mm/hugepages/hugepages-{}kB/nr_hugepages'
          ):
    """Thread-safe function to allocate hugepages.

    The default size depends on the architecture:
        x86_64: 2 MiB
        POWER8: 16 MiB

    Args:
        count (int): Number of huge pages to be allocated.

    Returns:
        int: The number of successfully allocated hugepages.
    """
    return _alloc(count, size, path)


def dealloc(count, size=None,
            path='/sys/kernel/mm/hugepages/hugepages-{}kB/nr_hugepages'
            ):
    """Thread-safe function to deallocate hugepages.

    The default size depends on the architecture:
        x86_64: 2 MiB
        POWER8: 16 MiB

    Args:
        count (int): Number of huge pages to be deallocated.

    Returns:
        int: The number of successfully deallocated hugepages.
    """
    return -(_alloc(-count, size, path))


def _alloc(count, size, path):
    """Helper to actually (de)allocate hugepages, called by public facing
        methods.

    Args:
        count: Number of hugepages to allocate (can be negative)
        size: The target hugepage size (must be supported by the system)
        path: Path to the hugepages directory.

    Returns: The amount of allocated pages (can be negative,
        implicating deallocation).

    Raises:
    """
    if size is None:
        size = DEFAULT_HUGEPAGESIZE[cpuarch.real()]

    path = path.format(size)

    with _LOCK:
        ret = supervdsm.getProxy().hugepages_alloc(count, path)
        if ret != count:
            ret = supervdsm.getProxy().hugepages_alloc(-ret, path)

    return ret


def state(path=_PATH):
    """Read the state of hugepages on the system.

    Args:
        path: A path to the hugepages directory. (mostly for testing purposes)

    Returns:
        A (default)dict of hugepage sizes and their properties
            (e.g. free, allocated hugepages of given size)
    """
    sizes = collections.defaultdict(dict)
    for size in os.listdir(path):
        for key in (
                'free_hugepages', 'nr_hugepages',
                'nr_hugepages_mempolicy', 'nr_overcommit_hugepages',
                'resv_hugepages', 'surplus_hugepages'):
            with open(os.path.join(path, size, key)) as f:
                sizes[_size_from_dir(size)][key] = f.read().strip()

    return sizes


def _size_from_dir(path):
    """Get the size portion of a hugepages directory.

    Example: _size_from_dir('hugepages-1048576Kb') ~> 1048576

    Args:
        path: Path to the hugepages directory.

    Returns:
        Just the hugepage size from the name of directory specified in path.
    """
    return int(path[10:-2])
