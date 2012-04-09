#
# Copyright 2012 Red Hat, Inc.
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

import parted


def getDevicePartedInfo(devPath):
    try:
        partedDevice = parted.Device(devPath)
    except:
        return {}
    try:
        partedDisk = parted.Disk(partedDevice)
    except:
        # partedDevice is a partition or a disk without partition table
        return {'model': partedDevice.model,
                'sectorSize': partedDevice.sectorSize,
                'type': '',
                'freeSpaceRegions': [],
                'partitions': {}}

    freeRegionList = []
    for region in partedDisk.getFreeSpaceRegions():
        freeBytes = region.length * partedDevice.sectorSize
        freeRegionList.append((region.start, region.end,
                               region.length, freeBytes))
    partitions = {}
    for partition in partedDisk.partitions:
        partitions.update({partition.path:
                               (partition.getFlagsAsString().split(),
                                partition.geometry.start,
                                partition.geometry.end)})

    return {'model': partedDevice.model,
            'sectorSize': partedDevice.sectorSize,
            'type': partedDisk.type,
            'freeSpaceRegions': freeRegionList,
            'partitions': partitions}
