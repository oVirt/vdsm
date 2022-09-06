# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import
from __future__ import division
import os

import six

from vdsm.network.link.bond import Bond

from vdsm.network.link.setup import parse_bond_options

parse_bond_options

BONDING_ACTIVE_SLAVE = '/sys/class/net/%s/bonding/active_slave'
BONDING_OPT = '/sys/class/net/%s/bonding/%s'
BONDING_SLAVES = '/sys/class/net/%s/bonding/slaves'
BONDING_SLAVE_OPT = '/sys/class/net/%s/bonding_slave/%s'


def _file_value(path):
    if os.path.exists(path):
        with open(path, 'r') as f:
            return f.read().replace('N/A', '').strip()


def get_bond_slave_agg_info(nic_name):
    agg_id_path = BONDING_SLAVE_OPT % (nic_name, 'ad_aggregator_id')
    agg_id = _file_value(agg_id_path)
    return {'ad_aggregator_id': agg_id} if agg_id else {}


def get_bond_agg_info(bond_name):
    agg_id_path = BONDING_OPT % (bond_name, 'ad_aggregator')
    ad_mac_path = BONDING_OPT % (bond_name, 'ad_partner_mac')
    agg_id = _file_value(agg_id_path)
    agg_mac = _file_value(ad_mac_path)
    return (
        {'ad_aggregator_id': agg_id, 'ad_partner_mac': agg_mac}
        if agg_id and agg_mac
        else {}
    )


def info(link):
    bond = Bond(link.name)
    return {
        'hwaddr': link.address,
        'slaves': list(bond.slaves),
        'active_slave': bond.active_slave(),
        'opts': bond.options,
    }


def bondOptsForIfcfg(opts):
    """
    Options having symbolic values, e.g. 'mode', are presented by sysfs in
    the order symbolic name, numeric value, e.g. 'balance-rr 0'.
    Choose the numeric value from a list given by bondOpts().
    """
    return ' '.join(
        (opt + '=' + val for (opt, val) in sorted(six.iteritems(opts)))
    )


def permanent_address():
    paddr = {}
    for b in Bond.bonds():
        with open('/proc/net/bonding/' + b) as f:
            for line in f:
                if line.startswith('Slave Interface: '):
                    slave = line[len('Slave Interface: ') : -1]  # noqa: E203
                elif line.startswith('Permanent HW addr: ') and slave:
                    paddr[slave] = line[
                        len('Permanent HW addr: ') : -1  # noqa: E203
                    ]
    return paddr
