#
# Copyright 2015-2017 Hat, Inc.
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
import errno
from functools import partial
from glob import iglob
import io
import json
import logging
import os

import six

from vdsm import constants
from vdsm.network.ipwrapper import Link
from vdsm.utils import memoized

from .misc import visible_devs
from . import nics

from vdsm.network.link.bond import Bond

# In order to limit the scope of change, this module is now acting as a proxy
# to the link.bond.sysfs_options module.
from vdsm.network.link.bond.sysfs_options import _bond_opts_read_elements
from vdsm.network.link.bond.sysfs_options import properties
from vdsm.network.link.bond.sysfs_options import getDefaultBondingOptions
from vdsm.network.link.bond.sysfs_options import getAllDefaultBondingOptions
from vdsm.network.link.bond.sysfs_options import EXCLUDED_BONDING_ENTRIES
from vdsm.network.link.bond.sysfs_options import BONDING_MODES_NAME_TO_NUMBER
from vdsm.network.link.bond.sysfs_options import BONDING_MODES_NUMBER_TO_NAME
from vdsm.network.link.bond.sysfs_options import BONDING_DEFAULTS
from vdsm.network.link.setup import parse_bond_options
getDefaultBondingOptions
getAllDefaultBondingOptions
parse_bond_options
BONDING_MODES_NAME_TO_NUMBER
BONDING_MODES_NUMBER_TO_NAME
BONDING_DEFAULTS

BONDING_ACTIVE_SLAVE = '/sys/class/net/%s/bonding/active_slave'
BONDING_FAILOVER_MODES = frozenset(('1', '3'))
BONDING_LOADBALANCE_MODES = frozenset(('0', '2', '4', '5', '6'))
BONDING_NAME2NUMERIC_PATH = constants.P_VDSM + 'bonding-name2numeric.json'
BONDING_MASTERS = '/sys/class/net/bonding_masters'
BONDING_OPT = '/sys/class/net/%s/bonding/%s'
BONDING_SLAVES = '/sys/class/net/%s/bonding/slaves'
BONDING_SLAVE_OPT = '/sys/class/net/%s/bonding_slave/%s'

bondings = partial(visible_devs, Link.isBOND)


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
    return {
        'ad_aggregator_id': agg_id, 'ad_partner_mac': agg_mac
    } if agg_id and agg_mac else {}


def info(link):
    bond = Bond(link.name)
    return {'hwaddr': link.address, 'slaves': list(bond.slaves),
            'active_slave': bond.active_slave(),
            'opts': bond.options}


def speed(bond_name):
    """Returns the bond speed if bondName refers to a bond, 0 otherwise."""
    opts = properties(bond_name,
                      filter_properties=('slaves', 'active_slave', 'mode'))
    try:
        if opts['slaves']:
            if opts['mode'][1] in BONDING_FAILOVER_MODES:
                active_slave = opts['active_slave']
                s = nics.speed(active_slave[0]) if active_slave else 0
            elif opts['mode'][1] in BONDING_LOADBALANCE_MODES:
                s = sum(nics.speed(slave) for slave in opts['slaves'])
            return s
    except Exception:
        logging.exception('cannot read %s speed', bond_name)
    return 0


def bondOptsForIfcfg(opts):
    """
    Options having symbolic values, e.g. 'mode', are presented by sysfs in
    the order symbolic name, numeric value, e.g. 'balance-rr 0'.
    Choose the numeric value from a list given by bondOpts().
    """
    return ' '.join((opt + '=' + val for (opt, val)
                     in sorted(opts.iteritems())))


def permanent_address():
    paddr = {}
    for b in Bond.bonds():
        with open('/proc/net/bonding/' + b) as f:
            for line in f:
                if line.startswith('Slave Interface: '):
                    slave = line[len('Slave Interface: '):-1]
                elif line.startswith('Permanent HW addr: ') and slave:
                    paddr[slave] = line[len('Permanent HW addr: '):-1]
    return paddr


def bond_opts_name2numeric_filtered(bond):
    """
    Return a dictionary in the same format as _bond_opts_name2numeric().
    Exclude entries that are not bonding options,
    e.g. 'ad_num_ports' or 'slaves'.
    """
    return dict(((opt, val) for (opt, val)
                 in six.iteritems(_bond_opts_name2numeric(bond))
                 if opt not in EXCLUDED_BONDING_ENTRIES))


def get_bonding_option_numeric_val(mode_num, option_name, val_name):
    bond_opts_map = _get_bonding_option_name2numeric()
    opt = bond_opts_map[mode_num].get(option_name, None)
    return opt.get(val_name, None) if opt else None


@memoized
def _get_bonding_option_name2numeric():
    """
    Return options per mode, in a dictionary of dictionaries.
    For each mode, there are options with name values as keys
    and their numeric equivalent.
    """
    with open(BONDING_NAME2NUMERIC_PATH) as f:
        return json.loads(f.read())


def _bond_opts_name2numeric(bond):
    """
    Returns a dictionary of bond option name and a values iterable. E.g.,
    {'mode': ('balance-rr', '0'), 'xmit_hash_policy': ('layer2', '0')}
    """
    bond_mode_path = BONDING_OPT % (bond, 'mode')
    paths = (p for p in iglob(BONDING_OPT % (bond, '*'))
             if p != bond_mode_path)
    opts = {}

    for path in paths:
        elements = _bond_opts_read_elements(path)
        if len(elements) == 2:
            opts[os.path.basename(path)] = \
                _bond_opts_name2numeric_scan(path)
    return opts


def _bond_opts_name2numeric_scan(opt_path):
    vals = {}
    with io.open(opt_path, 'wb', buffering=0) as opt_file:
        for numeric_val in range(32):
            name, numeric = _bond_opts_name2numeric_getval(opt_path, opt_file,
                                                           numeric_val)
            if name is None:
                break

            vals[name] = numeric

    return vals


def _bond_opts_name2numeric_getval(opt_path, opt_write_file, numeric_val):
    try:
        opt_write_file.write(str(numeric_val).encode('utf8'))
    except IOError as e:
        if e.errno in (errno.EINVAL, errno.EPERM, errno.EACCES):
            return None, None
        else:
            e.filename = "opt[%s], numeric_val[%s]" % (opt_path, numeric_val)
            raise

    return _bond_opts_read_elements(opt_path)
