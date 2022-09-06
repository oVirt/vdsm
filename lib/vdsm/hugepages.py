# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import

import os
import collections
import threading

from vdsm import osinfo
from vdsm.common import cache
from vdsm.common import cpuarch
from vdsm.common import supervdsm
from vdsm.config import config


_PATH = '/sys/kernel/mm/hugepages'
_VM = '/proc/sys/vm/'

lock = threading.Lock()

DEFAULT_HUGEPAGESIZE = {
    cpuarch.X86_64: 2048,
    cpuarch.PPC64LE: 16384,
}


class NonContiguousMemory(Exception):
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
    return list(state(path).keys())


def alloc(count, size=None,
          path='/sys/kernel/mm/hugepages/hugepages-{}kB/nr_hugepages'
          ):
    """Thread *unsafe* function to allocate hugepages.

    The default size depends on the architecture:
        x86_64: 2 MiB
        POWER8: 16 MiB

    It is a responsibility of the caller to properly handle concurrency.

    Args:
        count (int): Number of huge pages to be allocated.

    Returns:
        int: The number of successfully allocated hugepages.
    """

    return _alloc(count, size, path)


def dealloc(count, size=None,
            path='/sys/kernel/mm/hugepages/hugepages-{}kB/nr_hugepages'
            ):
    """Thread *unsafe* function to deallocate hugepages.

    The default size depends on the architecture:
        x86_64: 2 MiB
        POWER8: 16 MiB

    It is a responsibility of the caller to properly handle concurrency.

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

    ret = supervdsm.getProxy().hugepages_alloc(count, path)
    if ret != count:
        supervdsm.getProxy().hugepages_alloc(-ret, path)
        raise NonContiguousMemory

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
            size_in_kb = _size_from_dir(size)
            with open(os.path.join(path, size, key)) as f:
                sizes[size_in_kb][key] = int(f.read())

            # Let's calculate hugepages available for VMs as
            # system.free_hugepages - vdsm.reserved_hugepages. This value
            # could be negative (8 reserved hugepages are used; 0 free
            # hugepages) therefore we floor it at 0 (making the interval [0,
            # sys.nr_hugepages]).
            sizes[size_in_kb]['vm.free_hugepages'] = max(
                int(sizes[size_in_kb]['free_hugepages']) -
                _reserved_hugepages(key), 0
            )

    return sizes


def calculate_required_allocation(cif, vm_hugepages, vm_hugepagesz):
    """

    Args:
        cif: The ClientIF instance. Used as we need to iterate over VMs to
            reason about hugepages consumed by them.
        vm_hugepages: The number of hugepages VM requires.
        vm_hugepagesz: VM's hugepage size.

    It is a responsibility of the caller to properly handle concurrency.

    Returns:
        Number of hugepages to be allocated considering system resources at
        our disposal.
    """
    # Special case: hugepages of size 0 do not exist, but 0 (False) may be
    # used as indicator of disabled hugepages. In that case, we avoid any
    # allocation.
    if vm_hugepagesz == 0:
        return 0

    if not config.getboolean('performance', 'use_preallocated_hugepages'):
        return vm_hugepages

    all_vm_hugepages = _all_vm_hugepages(cif, vm_hugepages, vm_hugepagesz)
    system_hugepages = state()
    free_hugepages = int(system_hugepages[vm_hugepagesz]['free_hugepages'])
    nr_hugepages = int(system_hugepages[vm_hugepagesz]['nr_hugepages'])

    # Number of free_hugepages that are really available (= out of reserved
    # zone)
    really_free_hugepages = min(
        free_hugepages,
        # In this case, some of the hugepages may not be deallocated later.
        # That is not a problem because we're only adjusting to user's
        # configuration.
        nr_hugepages - all_vm_hugepages - _reserved_hugepages(vm_hugepagesz)
    )

    # >= 0
    really_free_hugepages = max(really_free_hugepages, 0)

    # Let's figure out how many hugepages we have to allocate for the VM to
    # fit.
    to_allocate = max(vm_hugepages - really_free_hugepages, 0)

    return to_allocate


def calculate_required_deallocation(vm_hugepages, vm_hugepagesz):
    """

    Args:
        vm_hugepages: The number of hugepages VM requires.
        vm_hugepagesz: VM's hugepage size.

    It is a responsibility of the caller to properly handle concurrency.

    Returns:
        Number of hugepages to be deallocated while making sure not to break
        any constraints (reserved and preallocated pages).
    """
    # Similar to allocation: hugepagesz == 0 indicates disabled hugepages.
    if vm_hugepagesz == 0:
        return 0

    if not config.getboolean('performance', 'use_preallocated_hugepages'):
        return vm_hugepages

    nr_hugepages = int(state()[vm_hugepagesz]['nr_hugepages'])

    to_deallocate = min(
        # At most, deallocate VMs hugepages,
        vm_hugepages,
        # while making sure we don't touch reserved or preallocated ones. That
        # is done since some of the pages initially allocated by VDSM could be
        # moved to reserved pages.
        nr_hugepages - max(_reserved_hugepages(vm_hugepagesz),
                           _preallocated_hugepages(vm_hugepagesz))
    )

    return to_deallocate


def _all_vm_hugepages(cif, vm_hugepages, vm_hugepagesz):
    return sum(
        [vm.nr_hugepages for vm in cif.getVMs().values() if
         vm.hugepagesz == vm_hugepagesz]
    ) - vm_hugepages


def _preallocated_hugepages(vm_hugepagesz):
    kernel_args = osinfo.kernel_args_dict()
    if 'hugepagesz' not in kernel_args:
        hugepagesz = DEFAULT_HUGEPAGESIZE[cpuarch.real()]
    else:
        hugepagesz = _cmdline_hugepagesz_to_kb(
            kernel_args['hugepagesz']
        )

    preallocated_hugepages = 0
    if ('hugepages' in kernel_args and
            hugepagesz == vm_hugepagesz):
        preallocated_hugepages = int(kernel_args['hugepages'])

    return preallocated_hugepages


def _reserved_hugepages(hugepagesz):
    reserved_hugepages = 0
    if config.getboolean('performance', 'use_preallocated_hugepages'):
        reserved_hugepages = (
            config.getint('performance', 'reserved_hugepage_count') if
            config.get('performance', 'reserved_hugepage_size') ==
            str(hugepagesz) else 0
        )

    return reserved_hugepages


def _cmdline_hugepagesz_to_kb(cmdline):
    return {
        '1GB': 1048576,
        '1G': 1048576,
        '2M': 2048,
        '2MB': 2048,
    }[cmdline]


def _size_from_dir(path):
    """Get the size portion of a hugepages directory.

    Example: _size_from_dir('hugepages-1048576Kb') ~> 1048576

    Args:
        path: Path to the hugepages directory.

    Returns:
        Just the hugepage size from the name of directory specified in path.
    """
    return int(path[10:-2])
