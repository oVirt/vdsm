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

from vdsm import utils

import os


def _getFromDeviceTree(treeProperty):
    path = '/proc/device-tree/%s' % treeProperty
    if os.path.exists(path):
        with open(path) as f:
            value = f.readline().rstrip('\0').replace(',', '')
            return value
    else:
        return 'unavailable'


@utils.memoized
def getHardwareInfoStructure():
    infoStructure = {'systemSerialNumber': 'unavailable',
                     'systemFamily': 'unavailable',
                     'systemVersion': 'unavailable'}

    for line in file('/proc/cpuinfo'):
        if line.strip() == '':
            continue
        key, value = map(str.strip, line.split(':', 1))

        if key == 'platform':
            infoStructure['systemFamily'] = value
        elif key == 'model':
            infoStructure['systemSerialNumber'] = value
        elif key == 'machine':
            infoStructure['systemVersion'] = value

    infoStructure['systemUUID'] = _getFromDeviceTree('system-id')

    infoStructure['systemProductName'] = _getFromDeviceTree('model-name')

    infoStructure['systemManufacturer'] = _getFromDeviceTree('vendor')

    return infoStructure


@utils.memoized
def getCpuTopology(capabilities):
    topology = {}

    if capabilities is None:
        retcode, out, err = utils.execCmd(['lscpu'], raw=True)
        capabilities = out

    corePS = None
    threadsPC = None
    sockets = None

    for line in capabilities.splitlines():
        if line.strip() == '':
            continue
        key, value = map(str.strip, line.split(':', 1))

        if key == 'Socket(s)':
            sockets = int(value)
        elif key == 'Thread(s) per core':
            threadsPC = int(value)
        elif key == 'Core(s) per socket':
            corePS = int(value)

    if corePS and threadsPC and sockets:
        topology['sockets'] = sockets
        topology['cores'] = corePS * sockets
        topology['threads'] = threadsPC * corePS * sockets
    else:
        raise RuntimeError('Undefined topology')

    return topology
