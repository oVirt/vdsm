# Copyright 2017 Red Hat, Inc.
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

from vdsm.network.link import bond
from vdsm.network.link import iface
from vdsm.network.link import nic


def speed(dev_name):
    """Return the vlan's underlying device speed."""
    dev_speed = 0
    interface = iface.iface(dev_name)
    iface_type = interface.type()
    if iface_type == iface.Type.NIC:
        # vlans on a nics expose the speed through sysfs
        dev_speed = nic.read_speed_using_sysfs(dev_name)
    elif iface_type == iface.Type.BOND:
        dev_speed = bond.speed(dev_name)

    return dev_speed
