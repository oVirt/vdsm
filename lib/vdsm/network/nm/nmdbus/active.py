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
from __future__ import division

import dbus

from . import DBUS_STD_PROPERTIES_IFNAME
from . import NMDbus, NMDbusManager


class NMDbusActiveConnections(object):
    def __init__(self):
        self.manager = NMDbusManager()

    def connections(self):
        active_connections = self.manager.properties.Get(
            NMDbusManager.IF_NAME, 'ActiveConnections'
        )
        for connection_path in active_connections:
            yield self.connection(connection_path)

    def connection(self, active_connection_path):
        con_properties = self._properties(active_connection_path)
        return _NMDbusActiveConnectionProperties(con_properties)

    def _properties(self, connection):
        con_proxy = NMDbus.bus.get_object(NMDbus.BUS_NAME, connection)
        return dbus.Interface(con_proxy, DBUS_STD_PROPERTIES_IFNAME)


class _NMDbusActiveConnectionProperties(object):
    IF_NAME = 'org.freedesktop.NetworkManager.Connection.Active'

    def __init__(self, connection_properties):
        self._properties = connection_properties

    def uuid(self):
        return self._property('Uuid')

    def con_path(self):
        return self._property('Connection')

    def id(self):
        return self._property('Id')

    def type(self):
        return self._property('Type')

    def devices_path(self):
        return self._property('Devices')

    def state(self):
        return self._property('State')

    def default(self):
        return self._property('Default')

    def ip4config(self):
        return self._property('Ip4Config')

    def dhcp4config(self):
        return self._property('Dhcp4Config')

    def default6(self):
        return self._property('Default6')

    def ip6config(self):
        return self._property('Ip6Config')

    def dhcp6config(self):
        return self._property('Dhcp6Config')

    def master_con_path(self):
        return self._property('Master')

    def _property(self, property_name):
        return self._properties.Get(self.IF_NAME, property_name)
