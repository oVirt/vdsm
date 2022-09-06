# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import
from __future__ import division

import errno

from vdsm.network.link import bond
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

    stats['speed'] = speed
    stats['duplex'] = nic.duplex(interface.device)

    return stats
