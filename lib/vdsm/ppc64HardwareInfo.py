# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import

import os.path

from vdsm import cpuinfo
from vdsm.common import cache


def _from_device_tree(tree_property, tree_path='/proc/device-tree'):
    path = os.path.join(tree_path, tree_property)
    try:
        with open(path) as f:
            value = f.readline().rstrip('\0').replace(',', '')
            return value
    except IOError:
        return 'unavailable'


@cache.memoized
def getHardwareInfoStructure():
    return {
        'systemSerialNumber': cpuinfo.ppcmodel(),
        'systemFamily': cpuinfo.platform(),
        'systemVersion': cpuinfo.machine(),
        'systemUUID': _from_device_tree('system-id'),
        'systemProductName': _from_device_tree('model-name'),
        'systemManufacturer': _from_device_tree('vendor'),
    }
