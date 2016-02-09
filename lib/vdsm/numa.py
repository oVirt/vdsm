#
# Copyright 2016 Red Hat, Inc.
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

from collections import defaultdict, namedtuple
import xml.etree.ElementTree as ET

from . import utils
from . import libvirtconnection


NumaTopology = namedtuple('NumaTopology', 'topology, distances, cpu_topology')


def _get_libvirt_caps():
    conn = libvirtconnection.get()
    return conn.getCapabilities()


@utils.memoized
def _numa(capabilities=None):
    if capabilities is None:
        capabilities = _get_libvirt_caps()

    topology = defaultdict(dict)
    distances = defaultdict(dict)
    sockets = set()
    siblings = set()
    online_cpus = []

    caps = ET.fromstring(capabilities)
    cells = caps.findall('.host//cells/cell')

    for cell in cells:
        cell_id = cell.get('id')
        meminfo = memory_by_cell(int(cell_id))
        topology[cell_id]['totalMemory'] = meminfo['total']
        topology[cell_id]['cpus'] = []
        distances[cell_id] = []

        for cpu in cell.findall('cpus/cpu'):
            topology[cell_id]['cpus'].append(int(cpu.get('id')))
            if cpu.get('siblings') and cpu.get('socket_id'):
                online_cpus.append(cpu.get('id'))
                sockets.add(cpu.get('socket_id'))
                siblings.add(cpu.get('siblings'))

        if cell.find('distances') is not None:
            for sibling in cell.find('distances').findall('sibling'):
                distances[cell_id].append(int(sibling.get('value')))

    cpu_topology = {
        'sockets': len(sockets),
        'cores': len(siblings),
        'threads': len(online_cpus),
        'onlineCpus': online_cpus,
    }

    return NumaTopology(topology, distances, cpu_topology)


def memory_by_cell(index):
    """
    Get the memory stats of a specified numa node, the unit is MiB.

    :param cell: the index of numa node
    :type cell: int
    :return: dict like {'total': '49141', 'free': '46783'}
    """
    conn = libvirtconnection.get()
    meminfo = conn.getMemoryStats(index, 0)
    meminfo['total'] = str(meminfo['total'] / 1024)
    meminfo['free'] = str(meminfo['free'] / 1024)
    return meminfo


def topology(capabilities=None):
    return _numa(capabilities).topology


def distances():
    return _numa().distances


def cpu_topology(capabilities=None):
    return _numa(capabilities).cpu_topology
