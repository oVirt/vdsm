# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

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
