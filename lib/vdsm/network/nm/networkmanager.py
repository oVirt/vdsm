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

from vdsm.common.cache import memoized
from vdsm.common.cmdutils import CommandPath
from vdsm.network import cmd

from .nmdbus import NMDbus, NMDbusIfcfgRH1
from .nmdbus.active import NMDbusActiveConnections
from .nmdbus.device import NMDbusDevice
from .nmdbus.settings import NMDbusSettings

SYSTEMCTL = CommandPath('systemctl', '/bin/systemctl', '/usr/bin/systemctl')
NM_SERVICE = 'NetworkManager'


@memoized
def is_running():
    rc, out, err = cmd.exec_sync([SYSTEMCTL.cmd, 'status', NM_SERVICE])
    return rc == 0


def init():
    NMDbus.init()


class Device(object):
    def __init__(self, device_name):
        self._name = device_name

        self._nm_settings = NMDbusSettings()
        self._nm_act_connections = NMDbusActiveConnections()
        self._nm_device_service = NMDbusDevice()

    def connections(self):
        device = self._nm_device_service.device(self._name)
        for connection_path in device.connections_path():
            yield self._nm_settings.connection(connection_path)

    @property
    def active_connection(self):
        device = self._nm_device_service.device(self._name)
        ac_path = device.active_connection_path()
        nm_act_cons = self._nm_act_connections
        return nm_act_cons.connection(ac_path) if ac_path != '/' else None

    def cleanup_inactive_connections(self):
        """
        Remove all non active connection that are associated with the device,
        leaving only the active connection.
        """
        for connection in self._non_active_connections():
            connection.delete()

    @property
    def syncoper(self):
        device = self._nm_device_service.device(self._name)
        return device.syncoper

    def _non_active_connections(self):
        active_connection = self.active_connection
        device = self._nm_device_service.device(self._name)
        for connection_path in device.connections_path():
            connection = self._nm_settings.connection(connection_path)
            if (
                not active_connection
                or connection.connection.uuid != active_connection.uuid()
            ):
                yield self._nm_settings.connection(connection_path)


def ifcfg2connection(ifcfg_file_path):
    """
    Given an ifcfg full file path,
    return a tuple of the NM connection uuid and path.
    In case no connection is found for the given file, return (None, None).
    """
    nm_ifcfg = NMDbusIfcfgRH1()
    return nm_ifcfg.ifcfg2connection(ifcfg_file_path)
