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


@utils.memoized
def getHardwareInfoStructure():
    infoStructure = {'systemProductName': 'unavailable',
                     'systemSerialNumber': 'unavailable',
                     'systemFamily': 'unavailable',
                     'systemVersion': 'unavailable',
                     'systemUUID': 'unavailable',
                     'systemManufacturer': 'unavailable'}

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

    if os.path.exists('/proc/device-tree/system-id'):
        with open('/proc/device-tree/system-id') as f:
            vdsmId = f.readline().rstrip('\0').replace(',', '')

        infoStructure['systemUUID'] = vdsmId

    return infoStructure
