# Copyright 2016-2018 Red Hat, Inc.
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
from __future__ import division

import time

import dbus

from vdsm.network.nm import errors

from . import DBUS_STD_PROPERTIES_IFNAME
from . import NMDbus, NMDbusManager
from . import nmtypes


WAITFOR_RESOLUTION = 0.2
WAITFOR_TIMEOUT = 2


class NMTimeoutDeviceStateNotReached(errors.NMTimeoutError):
    pass


class NMDbusDevice(object):
    def __init__(self):
        self.manager = NMDbusManager()

    def devices(self):
        devices = self.manager.interface.GetDevices()
        for device_path in devices:
            yield _NMDbusDeviceProperties(self._properties(device_path))

    @errors.nmerror_dev_not_found()
    def device(self, iface_name):
        device = self.manager.interface.GetDeviceByIpIface(iface_name)
        return _NMDbusDeviceProperties(self._properties(device))

    def _properties(self, device_path):
        device_proxy = NMDbus.bus.get_object(NMDbus.BUS_NAME, device_path)
        return dbus.Interface(device_proxy, DBUS_STD_PROPERTIES_IFNAME)


class _NMDbusDeviceProperties(object):
    IF_NAME = 'org.freedesktop.NetworkManager.Device'

    def __init__(self, device_properties):
        self._properties = device_properties
        self.syncoper = _NMDbusDeviceSyncOperations(self)

    def interface(self):
        return self._property('Interface')

    def state(self):
        return self._property('State')

    def active_connection_path(self):
        return self._property('ActiveConnection')

    def connections_path(self):
        return self._property('AvailableConnections')

    @errors.nmerror_properties_not_found()
    def _property(self, property_name):
        return self._properties.Get(self.IF_NAME, property_name)


class _NMDbusDeviceSyncOperations(object):
    def __init__(self, device):
        self._device = device

    def waitfor_activated_state(self, timeout=WAITFOR_TIMEOUT):
        self.waitfor_state(nmtypes.NMDeviceState.ACTIVATED, timeout)

    def waitfor_state(self, state, timeout=WAITFOR_TIMEOUT):
        for _ in range(_round_up(timeout / WAITFOR_RESOLUTION)):
            actual_state = self._device.state()
            if actual_state == state:
                return
            time.sleep(WAITFOR_RESOLUTION)

        raise NMTimeoutDeviceStateNotReached(
            'Actual: {}, Desired: {}', actual_state, state
        )


def _round_up(number):
    return int(number) if number == int(number) else int(number + 1)
