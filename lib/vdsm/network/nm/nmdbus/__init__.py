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
from dbus.exceptions import DBusException


DBUS_STD_PROPERTIES_IFNAME = 'org.freedesktop.DBus.Properties'


class NMDbus(object):
    BUS_NAME = 'org.freedesktop.NetworkManager'

    bus = None

    @staticmethod
    def init():
        NMDbus.bus = dbus.SystemBus()


class NMDbusManager(object):
    IF_NAME = 'org.freedesktop.NetworkManager'
    OBJ_PATH = '/org/freedesktop/NetworkManager'

    def __init__(self):
        mng_proxy = NMDbus.bus.get_object(
            NMDbus.BUS_NAME, NMDbusManager.OBJ_PATH
        )
        self.properties = dbus.Interface(mng_proxy, DBUS_STD_PROPERTIES_IFNAME)
        self.interface = dbus.Interface(mng_proxy, NMDbusManager.IF_NAME)


class NMDbusIfcfgRH1(object):
    IF_NAME = 'com.redhat.ifcfgrh1'
    OBJ_PATH = '/com/redhat/ifcfgrh1'

    ERROR_INV_CON = [
        "ifcfg file '{}' unknown",
        "ifcfg path '{}' is not an ifcfg base file",
    ]

    def __init__(self):
        ifcfg_proxy = NMDbus.bus.get_object(
            NMDbusIfcfgRH1.IF_NAME, NMDbusIfcfgRH1.OBJ_PATH
        )
        self.ifcfg = dbus.Interface(ifcfg_proxy, NMDbusIfcfgRH1.IF_NAME)

    def ifcfg2connection(self, ifcfg_path):
        """
        Given an ifcfg full file path,
        return a tuple of the NM connection uuid and path.
        In case no connection is found for the given file, return (None, None).
        """
        con_info = (None, None)
        try:
            con_info = self.ifcfg.GetIfcfgDetails(ifcfg_path)
        except DBusException as ex:
            for err_base_message in NMDbusIfcfgRH1.ERROR_INV_CON:
                if err_base_message.format(ifcfg_path) == ex.args[0]:
                    return con_info
            raise

        return con_info
