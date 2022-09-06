# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import
from __future__ import division

from collections import defaultdict, namedtuple
import xml.etree.ElementTree as ET

from vdsm import taskset
from vdsm.common import cache
from vdsm.common import commands
from vdsm.common import libvirtconnection
from vdsm.common.cmdutils import CommandPath

NumaTopology = namedtuple('NumaTopology',
                          ['topology', 'distances', 'cpu_topology',
                           'cpu_info', 'core_cpus'])
CpuTopology = namedtuple('CpuTopology',
                         ['sockets', 'cores', 'threads', 'online_cpus'])
CpuInfo = namedtuple('CpuInfo',
                     ['cpu_id', 'numa_cell_id', 'socket_id', 'die_id',
                      'core_id'])


_SYSCTL = CommandPath("sysctl", "/sbin/sysctl", "/usr/sbin/sysctl")


AUTONUMA_STATUS_DISABLE = 0
AUTONUMA_STATUS_ENABLE = 1
AUTONUMA_STATUS_UNKNOWN = 2


def topology():
    '''
    Get what we call 'numa topology' of the host from libvirt. This topology
    contains mapping numa cell -> (cpu ids, total memory).

    Example:
        {'0': {'cpus': [0, 1, 2, 3, 4, 10, 11, 12, 13, 14],
               'totalMemory': '32657'},
         '1': {'cpus': [5, 6, 7, 8, 9, 15, 16, 17, 18, 19],
               'totalMemory': '32768'}}
    '''
    return _numa().topology


def distances():
    '''
    Get distances between numa nodes. The information is a mapping
    numa cell -> [distance], where distances are sorted relatively to cell id
    in ascending order.

    Example:
        {'0': [10, 21],
         '1': [21, 10]}
    '''
    return _numa().distances


def cpu_topology():
    '''
    Get 'cpu topology' of the host from libvirt. This topology tries to
    summarize the cpu attributes over all numa cells. It is not reliable and
    should be reworked in future.

    Example:
        (sockets, cores, threads, online_cpus)
        (1, 10, 20, [0, 1, 2, 3, 4, 10, 11, 12, 13,
                     14, 5, 6, 7, 8, 9, 15, 16, 17, 18, 19])
    '''
    return _numa().cpu_topology


def cpu_info():
    '''
    Returns a list of all CPUs and information about them. Each CPU is
    described as:
        (cpu id, numa cell id, socket id, die id, core id)

    Example:
        [(0, 0, 0, 0, 0), (1, 1, 1, 0, 0)]
    '''
    return _numa().cpu_info


def core_cpus():
    '''
    Returns dictionary containing CPUs on each core. Key is a tuple
    (socket id, die id, core id) and value is a set of CPU ids.

    Example:
    {
      (0, 0, 0): set([0,4])
      (0, 0, 1): set([1,5])
      (1, 0, 0): set([2,6])
      (1, 0, 1): set([3,7])
    }
    '''
    return _numa().core_cpus


@cache.memoized
def autonuma_status():
    '''
    Query system for autonuma status. Returns one of following:

        AUTONUMA_STATUS_DISABLE = 0
        AUTONUMA_STATUS_ENABLE = 1
        AUTONUMA_STATUS_UNKNOWN = 2
    '''
    out = commands.run([_SYSCTL.cmd, '-n', '-e', 'kernel.numa_balancing'])

    if not out:
        return AUTONUMA_STATUS_UNKNOWN
    elif out[0] == '0':
        return AUTONUMA_STATUS_DISABLE
    elif out[0] == '1':
        return AUTONUMA_STATUS_ENABLE
    else:
        return AUTONUMA_STATUS_UNKNOWN


def memory_by_cell(index):
    '''
    Get the memory stats of a specified numa node, the unit is MiB.

    :param cell: the index of numa node
    :type cell: int
    :return: dict like {'total': '49141', 'free': '46783'}
    '''
    conn = libvirtconnection.get()
    meminfo = conn.getMemoryStats(index, 0)
    meminfo['total'] = str(meminfo['total'] // 1024)
    meminfo['free'] = str(meminfo['free'] // 1024)
    return meminfo


def pages_by_cell(cell, index):
    pages = {int(page.get('size')): {'totalPages': page.text}
             for page in cell.findall('pages')}
    return pages


# Example of a value returned from conn.getFreePages:
# {1: {2048: 250, 1048576: 0, 4: 862794}}
def free_pages_by_cell(page_sizes, numa_index):
    free_pages = {}
    if page_sizes:
        conn = libvirtconnection.get()
        libvirt_freepages = conn.getFreePages(page_sizes, numa_index, 1)
        free_pages = \
            {int(page_size): {'freePages': free_pages}
             for page_size, free_pages in
                libvirt_freepages[numa_index].items()}
    return free_pages


class _cache:
    capabilities = None
    numa = None


def _numa():
    if _cache.numa is None:
        update()
    return _cache.numa


def update():
    capabilities = libvirtconnection.get().getCapabilities()
    if capabilities == _cache.capabilities:
        return

    topology = defaultdict(dict)
    distances = defaultdict(dict)
    sockets = set()
    siblings = set()
    core_cpus = defaultdict(lambda: set())  # Sets of CPU IDs
    online_cpus = []
    cpu_info = []

    caps = ET.fromstring(capabilities)
    cells = caps.findall('.host//cells/cell')

    for cell in cells:
        cell_id = cell.get('id')
        # work around libvirt bug (if not built with numactl)
        if len(cells) == 1:
            idx = -1
        else:
            idx = int(cell_id)
        meminfo = memory_by_cell(idx)
        topology[cell_id]['totalMemory'] = meminfo['total']
        topology[cell_id]['hugepages'] = pages_by_cell(cell, idx)
        topology[cell_id]['cpus'] = []
        distances[cell_id] = []

        for cpu in cell.findall('cpus/cpu'):
            cpu_id = int(cpu.get('id'))
            topology[cell_id]['cpus'].append(cpu_id)
            if cpu.get('siblings') and cpu.get('socket_id') and \
                    cpu.get('core_id'):
                core_id = int(cpu.get('core_id'))
                die_id = int(cpu.get('die_id', 0))
                socket_id = int(cpu.get('socket_id'))
                online_cpus.append(cpu_id)
                sockets.add(socket_id)
                siblings.add(cpu.get('siblings'))
                cpu_info.append(
                    CpuInfo(cpu_id=cpu_id, numa_cell_id=int(cell_id),
                            socket_id=socket_id, die_id=die_id,
                            core_id=core_id))
                core_cpus[(socket_id, die_id, core_id)].add(cpu_id)

        if cell.find('distances') is not None:
            for sibling in cell.find('distances').findall('sibling'):
                distances[cell_id].append(int(sibling.get('value')))

    cpu_topology = CpuTopology(len(sockets), len(siblings),
                               len(online_cpus), online_cpus)

    if not cells:
        hostcputop = caps.find('.host/cpu/topology')

        if hostcputop is not None:
            socketnum = int(hostcputop.get('sockets'))
            corenum = int(hostcputop.get('cores')) * socketnum
            threadnum = int(hostcputop.get('threads')) * corenum

            online_cpus = taskset.online_cpus()

            cpu_topology = CpuTopology(socketnum, corenum,
                                       threadnum, online_cpus)

    _cache.numa = NumaTopology(topology, distances, cpu_topology, cpu_info,
                               core_cpus)
    _cache.capabilities = capabilities
