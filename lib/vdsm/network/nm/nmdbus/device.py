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

from vdsm.network.nm.errors import nmerror_dev_not_found

from . import DBUS_STD_PROPERTIES_IFNAME
from . import NMDbus, NMDbusManager


class NMDbusDevice(object):

    def __init__(self):
        self.manager = NMDbusManager()

    def devices(self):
        devices = self.manager.interface.GetDevices()
        for device_path in devices:
            yield _NMDbusDeviceProperties(self._properties(device_path))

    @nmerror_dev_not_found()
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

    @property
    def interface(self):
        return self._property('Interface')

    @property
    def state(self):
        return self._property('State')

    @property
    def active_connection_path(self):
        return self._property('ActiveConnection')

    @property
    def connections_path(self):
        return self._property('AvailableConnections')

    def _property(self, property_name):
        return self._properties.Get(self.IF_NAME, property_name)
