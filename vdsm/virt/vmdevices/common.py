#
# Copyright 2008-2016 Red Hat, Inc.
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

from .. import vmxml

from . import core
from . import graphics
from . import hostdevice
from . import hwclass
from . import network
from . import storage


def _update_unknown_device_info(vm):
    """
    Obtain info about unknown devices from libvirt domain and update the
    corresponding device structures.  Unknown device is a device that has an
    address but wasn't passed during VM creation request.

    :param vm: VM for which the device info should be updated
    :type vm: `class:Vm` instance

    """
    def isKnownDevice(alias):
        for dev in vm.conf['devices']:
            if dev.get('alias') == alias:
                return True
        return False

    for x in vm.domain.devices.childNodes:
        # Ignore empty nodes and devices without address
        if (x.nodeName == '#text' or
                not x.getElementsByTagName('address')):
            continue

        alias = x.getElementsByTagName('alias')[0].getAttribute('name')
        if not isKnownDevice(alias):
            address = vmxml.device_address(x)
            # I general case we assume that device has attribute 'type',
            # if it hasn't getAttribute returns ''.
            device = x.getAttribute('type')
            newDev = {'type': x.nodeName,
                      'alias': alias,
                      'device': device,
                      'address': address}
            vm.conf['devices'].append(newDev)


def update_device_info(vm, devices):
    """
    Obtain info about VM devices from libvirt domain and update the
    corresponding device structures.

    :param vm: VM for which the device info should be updated
    :type vm: `class:Vm` instance
    :param devices: Device configuration of the given VM.
    :type devices: dict

    """
    network.Interface.update_device_info(vm, devices[hwclass.NIC])
    storage.Drive.update_device_info(vm, devices[hwclass.DISK])
    core.Sound.update_device_info(vm, devices[hwclass.SOUND])
    graphics.Graphics.update_device_info(vm, devices[hwclass.GRAPHICS])
    core.Video.update_device_info(vm, devices[hwclass.VIDEO])
    core.Controller.update_device_info(vm, devices[hwclass.CONTROLLER])
    core.Balloon.update_device_info(vm, devices[hwclass.BALLOON])
    core.Watchdog.update_device_info(vm, devices[hwclass.WATCHDOG])
    core.Smartcard.update_device_info(vm, devices[hwclass.SMARTCARD])
    core.Rng.update_device_info(vm, devices[hwclass.RNG])
    core.Console.update_device_info(vm, devices[hwclass.CONSOLE])
    hostdevice.HostDevice.update_device_info(vm, devices[hwclass.HOSTDEV])
    core.Memory.update_device_info(vm, devices[hwclass.MEMORY])
    # Obtain info of all unknown devices. Must be last!
    _update_unknown_device_info(vm)
