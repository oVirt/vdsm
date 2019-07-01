# Copyright 2018-2019 Red Hat, Inc.
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

import errno

from vdsm.network.link import bond
from vdsm.network.link import dpdk
from vdsm.network.link import iface
from vdsm.network.link import nic
from vdsm.network.link import vlan


def report():
    stats = {}
    for iface_properties in iface.list():
        try:
            interface = iface.iface(iface_properties['name'])
            stats[interface.device] = _generate_iface_stats(interface)
        except IOError as e:
            if e.errno != errno.ENODEV:
                raise
    return stats


def _generate_iface_stats(interface):
    stats = interface.statistics()
    speed = 0
    if interface.type() == iface.Type.NIC:
        speed = nic.speed(interface.device)
    elif interface.type() == iface.Type.BOND:
        speed = bond.speed(interface.device)
    elif interface.type() == iface.Type.VLAN:
        speed = vlan.speed(interface.device)
    elif interface.type() == iface.Type.DPDK:
        speed = dpdk.speed(interface.device)

    stats['speed'] = speed
    stats['duplex'] = nic.duplex(interface.device)

    return stats
