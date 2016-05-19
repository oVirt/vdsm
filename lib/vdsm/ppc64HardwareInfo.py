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

import os.path

from vdsm import utils


def _from_device_tree(tree_property, tree_path='/proc/device-tree'):
    path = os.path.join(tree_path, tree_property)
    try:
        with open(path) as f:
            value = f.readline().rstrip('\0').replace(',', '')
            return value
    except IOError:
        return 'unavailable'


@utils.memoized
def getHardwareInfoStructure(cpuinfo_path='/proc/cpuinfo'):
    infoStructure = {'systemSerialNumber': 'unavailable',
                     'systemFamily': 'unavailable',
                     'systemVersion': 'unavailable'}

    with open(cpuinfo_path) as info:
        for line in info:
            if line.strip() == '':
                continue
            key, value = map(str.strip, line.split(':', 1))

            if key == 'platform':
                infoStructure['systemFamily'] = value
            elif key == 'model':
                infoStructure['systemSerialNumber'] = value
            elif key == 'machine':
                infoStructure['systemVersion'] = value

    infoStructure['systemUUID'] = _from_device_tree('system-id')

    infoStructure['systemProductName'] = _from_device_tree('model-name')

    infoStructure['systemManufacturer'] = _from_device_tree('vendor')

    return infoStructure
