# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

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
            else:
                raise ValueError()
            return s
    except Exception:
        logging.exception('cannot read %s speed', bond_name)
    return 0
