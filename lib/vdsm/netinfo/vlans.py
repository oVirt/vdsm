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

from .bonding import bondSpeed
from .misc import _visible_devs
from .nics import speed
from ..ipwrapper import getLink, Link
from ..netlink import link as nl_link


vlans = partial(_visible_devs, Link.isVLAN)


def vlanDevsForIface(iface):
    for linkDict in nl_link.iter_links():
        if linkDict.get('device') == iface:
            yield linkDict['name']


def isVlanned(dev):
    return any(vlan.startswith(dev + '.') for vlan in vlans())


def vlan_device(vlan):
    """ Return the device of the given VLAN. """
    vlanLink = getLink(vlan)
    return vlanLink.device


def vlan_id(vlan):
    """ Return the ID of the given VLAN. """
    vlanLink = getLink(vlan)
    return int(vlanLink.vlanid)


def vlaninfo(link):
    return {'iface': link.device, 'vlanid': link.vlanid}


def vlanSpeed(vlanName):
    """Returns the vlan's underlying device speed."""
    vlanDevName = vlan_device(vlanName)
    vlanDev = getLink(vlanDevName)
    if vlanDev.isNIC():
        speed = speed(vlanDevName)
    elif vlanDev.isBOND():
        speed = bondSpeed(vlanDevName)
    else:
        speed = 0
    return speed
