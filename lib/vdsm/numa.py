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

from collections import defaultdict
import xml.etree.ElementTree as ET

from . import utils
from . import libvirtconnection


def _get_libvirt_caps():
    conn = libvirtconnection.get()
    return conn.getCapabilities()


@utils.memoized
def topology(capabilities=None):
    if capabilities is None:
        capabilities = _get_libvirt_caps()
    caps = ET.fromstring(capabilities)
    host = caps.find('host')
    cells = host.find('.//cells')
    cells_info = defaultdict(dict)
    cell_sets = cells.findall('cell')
    for cell in cell_sets:
        cell_index = cell.get('id')
        cells_info[cell_index]['cpus'] = [int(cpu.get('id')) for cpu in
                                          cell.iter(tag='cpu')]
        meminfo = memory_by_cell(int(cell_index))
        cells_info[cell_index]['totalMemory'] = meminfo['total']
    return cells_info


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


@utils.memoized
def distances(capabilities=None):
    if capabilities is None:
        capabilities = _get_libvirt_caps()
    caps = ET.fromstring(capabilities)
    cells = caps.find('host').find('.//cells').findall('cell')
    distances = defaultdict(list)
    for cell in cells:
        cell_index = cell.get('id')
        distances[cell_index] = []
        for sibling in cell.find('distances').findall('sibling'):
            distances[cell_index].append(int(sibling.get('value')))

    return distances
