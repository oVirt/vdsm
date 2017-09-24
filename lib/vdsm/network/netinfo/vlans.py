#
# Copyright 2015 Hat, Inc.
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
from __future__ import absolute_import
from functools import partial

from . import bonding
from .misc import visible_devs
from vdsm.network.ipwrapper import getLink, Link
from vdsm.network.link import nic
from vdsm.network.netlink import link as nl_link


vlans = partial(visible_devs, Link.isVLAN)


def vlan_devs_for_iface(iface):
    for linkDict in nl_link.iter_links():
        if linkDict.get('device') == iface and linkDict.get('type') == 'vlan':
            yield linkDict['name']


def is_vlanned(device_name):
    return any(vlan_devs_for_iface(device_name))


def vlan_device(vlan_device_name):
    """ Return the device of the given VLAN. """
    vlanLink = getLink(vlan_device_name)
    return vlanLink.device


def vlan_id(vlan_device_name):
    """ Return the ID of the given VLAN. """
    vlanLink = getLink(vlan_device_name)
    return int(vlanLink.vlanid)


def info(link):
    return {'iface': link.device, 'vlanid': link.vlanid}


def speed(vlan_device_name):
    """Returns the vlan's underlying device speed."""
    vlanDevName = vlan_device(vlan_device_name)
    vlanDev = getLink(vlanDevName)
    if vlanDev.isNIC():
        return nic.speed(vlanDevName)
    elif vlanDev.isBOND():
        return bonding.speed(vlanDevName)
    else:
        return 0
