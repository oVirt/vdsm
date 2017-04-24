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

'''
VDSM vfio-mdev hook

The hook is able to utilize system's mdev-capable devices, creating and
deleting mdev instances as they're required.

Usage:
Set custom property

mdev_type

to the desired mdev type. The available types can be listed on the machine
with mdev-capable devices (hypervisor) using a bit of bash magic such as

$ for device in /sys/class/mdev_bus/*; do for mdev_type in \
$device/mdev_supported_types/*; do echo "mdev_type: \
\"$(basename $mdev_type)\" --- description: $(cat $mdev_type/description)"; \
done; done
'''

import collections
import os
import sys
import uuid

import hooking
from vdsm import supervdsm

_MDEV_PATH = '/sys/class/mdev_bus'
_MDEV_FIELDS = ('name', 'description', 'available_instances', 'device_api')
_MdevDetail = collections.namedtuple('_MdevDetail', _MDEV_FIELDS)

# Arbitrary value (generated on development machine).
_OVIRT_MDEV_NAMESPACE = uuid.UUID('8524b17c-f0ca-44a5-9ce4-66fe261e5986')


def _each_device():
    for pci_device in sorted(os.listdir(_MDEV_PATH)):
        yield pci_device


def _each_supported_mdev_type(pci_device):
    path = os.path.join(_MDEV_PATH, pci_device, 'mdev_supported_types')
    for mdev_type in os.listdir(path):
        yield mdev_type, path


def _mdev_type_details(mdev_type, path):
    ret = []

    for field in _MDEV_FIELDS:
        syspath = os.path.join(path, mdev_type, field)
        with open(syspath, 'r') as f:
            ret.append(f.read().strip())

    return _MdevDetail(*ret)


def _suitable_device_for_mdev_type(target_mdev_type):
    target_device = None

    for device in _each_device():
        vendor = None
        with open(os.path.join(_MDEV_PATH, device, 'vendor'), 'r') as f:
            vendor = f.read().strip()

        for mdev_type, path in _each_supported_mdev_type(device):
            if mdev_type != target_mdev_type:
                # If different type is already allocated and the vendor doesn't
                # support different types, skip the device.
                if vendor == '0x10de' and len(
                    os.listdir(os.path.join(path, mdev_type, 'devices'))
                ) > 0:
                    target_device = None
                    break
                continue
            if _mdev_type_details(mdev_type, path).available_instances < 1:
                continue

            target_device = device

        if target_device is not None:
            return target_device

    return target_device


if 'mdev_type' in os.environ:
    domxml = hooking.read_domxml()

    vm_name = str(
        domxml.getElementsByTagName('name')[0].firstChild.nodeValue
    )

    target_mdev_type = os.environ['mdev_type']
    # Sufficient as the hook only supports single mdev instance per VM.
    mdev_uuid = str(uuid.uuid3(_OVIRT_MDEV_NAMESPACE, vm_name))
    device = _suitable_device_for_mdev_type(target_mdev_type)
    if device is None:
        sys.stderr.write('vgpu: No device with type {} is available.\n'.format(
            target_mdev_type)
        )
        sys.exit(1)
    try:
        supervdsm.getProxy().mdev_create(device, target_mdev_type, mdev_uuid)
    except IOError:
        sys.stderr.write('vgpu: Failed to create mdev type {}.\n'.format(
            target_mdev_type)
        )
        sys.exit(1)

    supervdsm.getProxy().appropriateIommuGroup(
        os.path.basename(os.path.realpath(
            os.path.join(_MDEV_PATH, device, mdev_uuid, 'iommu_group')
        ))
    )

    hostdev = domxml.createElement('hostdev')
    hostdev.setAttribute('mode', 'subsystem')
    hostdev.setAttribute('type', 'mdev')
    hostdev.setAttribute('model', 'vfio-pci')

    source = domxml.createElement('source')
    address = domxml.createElement('address')
    address.setAttribute('uuid', mdev_uuid)
    source.appendChild(address)

    hostdev.appendChild(source)

    domxml.getElementsByTagName('devices')[0].appendChild(hostdev)

    hooking.write_domxml(domxml)
