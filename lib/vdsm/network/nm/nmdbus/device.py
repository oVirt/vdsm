# Copyright 2016-2017 Red Hat, Inc.
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

import dbus
from dbus.exceptions import DBusException

from vdsm.network.nm.errors import NMDeviceNotFoundError

from . import DBUS_STD_PROPERTIES_IFNAME
from . import NMDbus, NMDbusManager


class NMDbusDevice(object):

    def __init__(self):
        self.manager = NMDbusManager()

    def devices(self):
        devices = self.manager.interface.GetDevices()
        for device_path in devices:
            yield _NMDbusDeviceProperties(self._properties(device_path))

    def device(self, iface_name):
        try:
            device = self.manager.interface.GetDeviceByIpIface(iface_name)
        except DBusException as ex:
            if ex.args[0] == 'No device found for the requested iface.':
                raise NMDeviceNotFoundError()
        return _NMDbusDeviceProperties(self._properties(device))

    def _properties(self, device_path):
        device_proxy = NMDbus.bus.get_object(NMDbus.BUS_NAME, device_path)
        return dbus.Interface(device_proxy, DBUS_STD_PROPERTIES_IFNAME)


class _NMDbusDeviceProperties(object):
    IF_NAME = 'org.freedesktop.NetworkManager.Device'

    def __init__(self, device_properties):
        self._properties = device_properties

    @property
    def interface(self):
        return self._get_property('Interface')

    @property
    def state(self):
        return self._get_property('State')

    @property
    def active_connection_path(self):
        return self._get_property('ActiveConnection')

    @property
    def connections_path(self):
        return self._get_property('AvailableConnections')

    @property
    def managed(self):
        return self._get_property('Managed')

    @managed.setter
    def managed(self, value):
        return self._set_property('Managed', value)

    def _get_property(self, property_name):
        return self._properties.Get(self.IF_NAME, property_name)

    def _set_property(self, property_name, property_value):
        return self._properties.Set(
            self.IF_NAME, property_name, property_value)
