#!/usr/bin/python2
#
# Copyright 2017 Red Hat, Inc.
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
import os
import sys

import hooking
from vdsm import supervdsm

_MDEV_PATH = '/sys/class/mdev_bus'


def _each_device():
    for pci_device in sorted(os.listdir(_MDEV_PATH)):
        yield pci_device


if 'mdev_type' in os.environ:
    mdev_uuid = None
    domxml = hooking.read_domxml()
    for hostdev in domxml.getElementsByTagName('hostdev'):
        for address in hostdev.getElementsByTagName('address'):
            uuid = address.getAttribute('uuid')
            if uuid:
                mdev_uuid = uuid
                break

    device = None
    for dev in _each_device():
        if mdev_uuid in os.listdir(os.path.join(_MDEV_PATH, dev)):
            device = dev
            break

    if device is None or mdev_uuid is None:
        sys.stderr.write('vgpu: No mdev found.\n')
        sys.exit(1)

    supervdsm.getProxy().rmAppropriateIommuGroup(
        os.path.basename(os.path.realpath(
            os.path.join(_MDEV_PATH, device, mdev_uuid, 'iommu_group')
        ))
    )

    try:
        supervdsm.getProxy().mdev_delete(device, mdev_uuid)
    except IOError:
        # This is destroy flow, we can't really fail
        pass
