#
# Copyright 2008-2017 Red Hat, Inc.
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

from vdsm.virt import vmxml

from . import core
from . import graphics
from . import hostdevice
from . import hwclass
from . import lease
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

    for x in vmxml.children(vm.domain.devices):
        # Ignore empty nodes and devices without address
        if vmxml.find_first(x, 'address', None) is None:
            continue

        alias = vmxml.find_attr(x, 'alias', 'name')
        if not isKnownDevice(alias):
            address = vmxml.device_address(x)
            # In general case we assume that device has attribute 'type',
            # if it hasn't dom_attribute returns ''.
            device = vmxml.attr(x, 'type')
            newDev = {'type': vmxml.tag(x),
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
    lease.Device.update_device_info(vm, devices[hwclass.LEASE])
    # Obtain info of all unknown devices. Must be last!
    _update_unknown_device_info(vm)


def lookup_device_by_alias(devices, dev_type, alias):
    for dev in devices[dev_type][:]:
        try:
            if dev.alias == alias:
                return dev
        except AttributeError:
            continue
    raise LookupError('Device instance for device identified by alias %s '
                      'and type %s not found' % (alias, dev_type,))


def lookup_conf_by_alias(conf, dev_type, alias):
    for dev_conf in conf[:]:
        try:
            if dev_conf['alias'] == alias and dev_conf['type'] == dev_type:
                return dev_conf
        except KeyError:
            continue
    raise LookupError('Configuration of device identified by alias %s '
                      'and type %s not found' % (alias, dev_type,))
