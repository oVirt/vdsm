# Copyright 2014-2017 Red Hat, Inc.
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

import glob
import os

from vdsm.common import fileutils
from vdsm.network.netinfo import misc
from vdsm.network.nm import networkmanager
from vdsm.network.nm import errors as nmerrors


NET_CONF_DIR = '/etc/sysconfig/network-scripts/'
NET_CONF_PREF = NET_CONF_DIR + 'ifcfg-'


class IfcfgAcquire(object):
    @staticmethod
    def acquire_device(device):
        if networkmanager.is_running():
            IfcfgAcquireNMonline.acquire_device(device)
        else:
            IfcfgAcquireNMoffline.acquire_device(device)

    @staticmethod
    def acquire_vlan_device(device):
        if networkmanager.is_running():
            IfcfgAcquireNMonline.acquire_vlan_device(device)
        else:
            IfcfgAcquireNMoffline.acquire_vlan_device(device)


class IfcfgAcquireNMonline(object):
    @staticmethod
    def acquire_device(device):
        try:
            dev = networkmanager.Device(device)
            dev.syncoper.waitfor_activated_state()
            dev.cleanup_inactive_connections()
            active_connection = dev.active_connection
        except (
            nmerrors.NMDeviceNotFoundError,
            nmerrors.NMPropertiesNotFoundError,
            nmerrors.NMTimeoutError,
        ):
            return

        if not active_connection:
            return

        con_uuid = active_connection.uuid()
        fpath = IfcfgAcquireNMonline._ifcfg_file_lookup(con_uuid)
        if fpath:
            os.rename(fpath, NET_CONF_PREF + device)

    @staticmethod
    def acquire_vlan_device(device):
        return

    @staticmethod
    def _ifcfg_file_lookup(connection_uuid):
        for ifcfg_file in IfcfgAcquireNMonline._ifcfg_files():
            uuid, _ = networkmanager.ifcfg2connection(ifcfg_file)
            if uuid and uuid == connection_uuid:
                return ifcfg_file
        return None

    @staticmethod
    def _ifcfg_files():
        paths = glob.iglob(NET_CONF_PREF + '*')
        for ifcfg_file_name in paths:
            yield ifcfg_file_name


class IfcfgAcquireNMoffline(object):
    @staticmethod
    def acquire_device(device):
        """
        Attempts to detect a device ifcfg file and rename it to a vdsm
        supported format.
        In case of multiple ifcfg files that treat the same device, all except
        the first are deleted.
        """
        device_files = IfcfgAcquireNMoffline._collect_device_files(device)
        IfcfgAcquireNMoffline._normalize_device_filenames(device, device_files)

    @staticmethod
    def acquire_vlan_device(device):
        """
        VLAN devices may be represented in an ifcfg configuration syntax that
        is different from the common case. Specifically when being created
        using Network Manager.
        """
        device_files = IfcfgAcquireNMoffline._collect_vlan_device_files(device)
        IfcfgAcquireNMoffline._normalize_device_filenames(device, device_files)

    @staticmethod
    def _collect_device_files(device):
        device_files = []
        paths = glob.iglob(NET_CONF_PREF + '*')
        for ifcfg_file in paths:
            conf = misc.ifcfg_config(ifcfg_file)
            if conf.get('DEVICE') == device:
                device_files.append(ifcfg_file)
        return device_files

    @staticmethod
    def _collect_vlan_device_files(device):
        device_files = []
        paths = glob.iglob(NET_CONF_PREF + '*')
        for ifcfg_file in paths:
            conf = misc.ifcfg_config(ifcfg_file)
            is_vlan_device = conf.get('TYPE', '').upper() == 'VLAN'
            config_device = '{}.{}'.format(
                conf.get('PHYSDEV'), conf.get('VLAN_ID')
            )
            if is_vlan_device and config_device == device:
                device_files.append(ifcfg_file)
        return device_files

    @staticmethod
    def _config_entry(line):
        key, value = line.rstrip().split('=', 1)
        if value and value[0] == '\"' and value[-1] == '\"':
            value = value[1:-1]
        return key.upper(), value

    @staticmethod
    def _normalize_device_filenames(device, device_files):
        if device_files:
            os.rename(device_files[0], NET_CONF_PREF + device)
            for filepath in device_files[1:]:
                fileutils.rm_file(filepath)
