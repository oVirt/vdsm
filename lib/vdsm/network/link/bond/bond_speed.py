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
from __future__ import division

import logging

from vdsm.network.link import nic

from .sysfs_options import properties


BONDING_ACTIVE_BACKUP_MODE = frozenset('1')
BONDING_BROADCAST_MODE = frozenset('3')
BONDING_LOADBALANCE_MODES = frozenset(('0', '2', '4', '5', '6'))


def speed(bond_name):
    """Return the bond speed if bond_name refers to a bond, 0 otherwise."""
    opts = properties(
        bond_name, filter_properties=('slaves', 'active_slave', 'mode')
    )
    mode = opts['mode'][1]
    try:
        if opts['slaves']:
            if mode in BONDING_ACTIVE_BACKUP_MODE:
                active_slave = opts['active_slave']
                s = nic.speed(active_slave[0]) if active_slave else 0
            elif mode in BONDING_BROADCAST_MODE:
                s = min(nic.speed(slave) for slave in opts['slaves'])
            elif mode in BONDING_LOADBALANCE_MODES:
                s = sum(nic.speed(slave) for slave in opts['slaves'])
            return s
    except Exception:
        logging.exception('cannot read %s speed', bond_name)
    return 0
