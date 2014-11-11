#
# Copyright 2014 Red Hat, Inc.
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

import blivet
from . import makePublic


def _parseDevices(devices):
    deviceList = []
    for device in devices:
        info = {'name': device.name,
                'size': '%s' % device.size,
                'devPath': device.path,
                'devUuid': device.uuid or '',
                'bus': device.bus or '',
                'model': '',
                'fsType': '',
                'mountPoint': '',
                'uuid': '',
                'createBrick': True}
        if not info['bus'] and device.parents:
            info['bus'] = device.parents[0].bus
        if device.model:
            info['model'] = "%s (%s)" % (device.model, device.type)
        else:
            info['model'] = device.type
        if device.format:
            info['fsType'] = device.format.type or ''
        if hasattr(device.format, 'mountpoint'):
            info['mountPoint'] = device.format.mountpoint or ''
        info['createBrick'] = _canCreateBrick(device)

        deviceList.append(info)
    return deviceList


def _canCreateBrick(device):
    if not device or device.kids > 0 or device.format.type or \
       hasattr(device.format, 'mountpoint') or \
       device.type in ['cdrom', 'lvmvg', 'lvmthinpool', 'lvmlv']:
        return False
    return True


@makePublic
def storageDevicesList():
    blivetEnv = blivet.Blivet()
    blivetEnv.reset()
    return _parseDevices(blivetEnv.devices)
