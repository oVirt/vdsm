# Copyright 2018-2020 Red Hat, Inc.
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

from vdsm.network.link import bond
from vdsm.network.link import iface
from vdsm.network.link import nic


def speed(dev_name):
    """Return the vlan's underlying device speed."""
    dev_speed = 0
    dev_vlan = iface.iface(dev_name)
    dev_base_name = dev_vlan.properties()['device']
    dev_base = iface.iface(dev_base_name)
    dev_base_type = dev_base.type()
    if dev_base_type == iface.Type.NIC:
        dev_speed = nic.speed(dev_name)
    elif dev_base_type == iface.Type.BOND:
        dev_speed = bond.speed(dev_base_name)

    return dev_speed
