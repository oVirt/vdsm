# Copyright 2016 Red Hat, Inc.
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


class NMDbus(object):
    DBUS_PROPERTIES = 'org.freedesktop.DBus.Properties'
    NM_IF_NAME = 'org.freedesktop.NetworkManager'
    NM_PATH = '/org/freedesktop/NetworkManager'

    bus = None

    @staticmethod
    def init():
        NMDbus.bus = dbus.SystemBus()


class NMDbusManager(object):

    def __init__(self):
        mng_proxy = NMDbus.bus.get_object(NMDbus.NM_IF_NAME, NMDbus.NM_PATH)
        self.properties = dbus.Interface(mng_proxy, NMDbus.DBUS_PROPERTIES)
        self.interface = dbus.Interface(mng_proxy, NMDbus.NM_IF_NAME)


class NMDbusIfcfgRH1(object):
    NM_IFCFGRH_IF_NAME = 'com.redhat.ifcfgrh1'
    NM_IFCFGRH_PATH = '/com/redhat/ifcfgrh1'

    ERROR_INV_CON = ["ifcfg file '{}' unknown",
                     "ifcfg path '{}' is not an ifcfg base file"]

    def __init__(self):
        ifcfg_proxy = NMDbus.bus.get_object(NMDbusIfcfgRH1.NM_IFCFGRH_IF_NAME,
                                            NMDbusIfcfgRH1.NM_IFCFGRH_PATH)
        self.ifcfg = dbus.Interface(ifcfg_proxy,
                                    NMDbusIfcfgRH1.NM_IFCFGRH_IF_NAME)

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
