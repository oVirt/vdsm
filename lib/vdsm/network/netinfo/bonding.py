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
import errno
from functools import partial
from glob import iglob
import json
import logging
import six
import os

from vdsm import constants
from vdsm.network.ipwrapper import Link
from vdsm.utils import memoized

from .misc import visible_devs
from . import nics

BONDING_ACTIVE_SLAVE = '/sys/class/net/%s/bonding/active_slave'
BONDING_DEFAULTS = constants.P_VDSM + 'bonding-defaults.json'
BONDING_FAILOVER_MODES = frozenset(('1', '3'))
BONDING_LOADBALANCE_MODES = frozenset(('0', '2', '4', '5', '6'))
BONDING_NAME2NUMERIC_PATH = constants.P_VDSM + 'bonding-name2numeric.json'
BONDING_MASTERS = '/sys/class/net/bonding_masters'
BONDING_OPT = '/sys/class/net/%s/bonding/%s'
BONDING_SLAVES = '/sys/class/net/%s/bonding/slaves'
BONDING_SLAVE_OPT = '/sys/class/net/%s/bonding_slave/%s'
EXCLUDED_BONDING_ENTRIES = frozenset((
    'slaves', 'active_slave', 'mii_status', 'queue_id', 'ad_aggregator',
    'ad_num_ports', 'ad_actor_key', 'ad_partner_key', 'ad_partner_mac'
))

BONDING_MODES_NAME_TO_NUMBER = {
    'balance-rr': '0',
    'active-backup': '1',
    'balance-xor': '2',
    'broadcast': '3',
    '802.3ad': '4',
    'balance-tlb': '5',
    'balance-alb': '6',
}
BONDING_MODES_NUMBER_TO_NAME = dict(
    (v, k) for k, v in six.iteritems(BONDING_MODES_NAME_TO_NUMBER))

bondings = partial(visible_devs, Link.isBOND)


def slaves(bond_name):
    with open(BONDING_SLAVES % bond_name) as f:
        return f.readline().split()


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
    return {'hwaddr': link.address, 'slaves': slaves(link.name),
            'active_slave': _active_slave(link.name),
            'opts': _getBondingOptions(link.name)}


def _active_slave(bond_name):
    """
    :param bond_name:
    :return: active slave when one exists or '' otherwise
    """
    with open(BONDING_ACTIVE_SLAVE % bond_name) as f:
        return f.readline().rstrip()


def _getBondingOptions(bond_name):
    """
    Return non-empty options differing from defaults, excluding not actual or
    not applicable options, e.g. 'ad_num_ports' or 'slaves' and always return
    bonding mode even if it's default, e.g. 'mode=0'
    """
    opts = bondOpts(bond_name)
    mode = opts['mode'][-1] if 'mode' in opts else None
    defaults = getDefaultBondingOptions(mode)

    return dict(((opt, val[-1]) for (opt, val) in opts.iteritems()
                 if val and (val != defaults.get(opt) or opt == "mode")))


def bondOpts(bond_name, keys=None):
    """
    Return a dictionary in the same format as _bondOpts(). Exclude entries that
    are not bonding options, e.g. 'ad_num_ports' or 'slaves'.
    """
    return dict(((opt, val) for
                 (opt, val) in _bondOpts(bond_name, keys).iteritems()
                 if opt not in EXCLUDED_BONDING_ENTRIES))


@memoized
def getDefaultBondingOptions(mode=None):
    """
    Return default options for the given mode. If it is None, return options
    for the default mode (usually '0').
    """
    defaults = getAllDefaultBondingOptions()

    if mode is None:
        mode = defaults['0']['mode'][-1]

    return defaults[mode]


def _bond_opts_read_elements(file_path):
    with open(file_path) as f:
        return [el for el in f.read().rstrip().split(' ') if el]


def _bondOpts(bond_name, keys=None):
    """ Returns a dictionary of bond option name and a values iterable. E.g.,
    {'mode': ('balance-rr', '0'), 'xmit_hash_policy': ('layer2', '0')}
    """
    if keys is None:
        paths = iglob(BONDING_OPT % (bond_name, '*'))
    else:
        paths = (BONDING_OPT % (bond_name, key) for key in keys)
    opts = {}
    for path in paths:
        opts[os.path.basename(path)] = _bond_opts_read_elements(path)

    return opts


@memoized
def getAllDefaultBondingOptions():
    """
    Return default options per mode, in a dictionary of dictionaries. All keys
    are numeric modes stored as strings for coherence with 'mode' option value.
    """
    with open(BONDING_DEFAULTS) as defaults:
        return json.loads(defaults.read())


def speed(bond_name):
    """Returns the bond speed if bondName refers to a bond, 0 otherwise."""
    opts = _bondOpts(bond_name, keys=['slaves', 'active_slave', 'mode'])
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


def bondOptsCompat(info):
    """Add legacy ifcfg option if missing."""
    if info['opts'] and 'BONDING_OPTS' not in info['cfg']:
        info['cfg']['BONDING_OPTS'] = bondOptsForIfcfg(info['opts'])


def permanent_address():
    paddr = {}
    for b in bondings():
        slave = ''
        with open('/proc/net/bonding/' + b) as f:
            for line in f:
                if line.startswith('Slave Interface: '):
                    slave = line[len('Slave Interface: '):-1]
                if line.startswith('Permanent HW addr: '):
                    paddr[slave] = line[len('Permanent HW addr: '):-1]
    return paddr


def bond_opts_name2numeric_filtered(bond):
    """
    Return a dictionary in the same format as _bond_opts_name2numeric().
    Exclude entries that are not bonding options,
    e.g. 'ad_num_ports' or 'slaves'.
    """
    return dict(((opt, val) for (opt, val)
                 in _bond_opts_name2numeric(bond).iteritems()
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
    with open(opt_path, 'w') as opt_file:
        for numeric_val in range(32):
            name, numeric = _bond_opts_name2numeric_getval(opt_path, opt_file,
                                                           numeric_val)
            if name is None:
                break

            vals[name] = numeric

    return vals


def _bond_opts_name2numeric_getval(opt_path, opt_write_file, numeric_val):
    try:
        opt_write_file.write(str(numeric_val))
        opt_write_file.flush()
    except IOError as e:
        if e.errno in (errno.EINVAL, errno.EPERM, errno.EACCES):
            return None, None
        else:
            e.filename = "opt[%s], numeric_val[%s]" % (opt_path, numeric_val)
            raise

    return _bond_opts_read_elements(opt_path)


def parse_bond_options(options, keep_custom=False):
    """
    Parse bonding options into dictionary, if keep_custom is set to True,
    custom option will not be recursively parsed.

    >>> parse_bond_options('mode=4 custom=foo:yes,bar:no')
    {'custom': {'bar': 'no', 'foo': 'yes'}, 'mode': '4'}
    """
    def _string_to_dict(str, div, eq):
        if options == '':
            return {}
        return dict(option.split(eq, 1)
                    for option in str.strip(div).split(div))
    if options:
        d_options = _string_to_dict(options, ' ', '=')
        if d_options.get('custom') and not keep_custom:
            d_options['custom'] = _string_to_dict(d_options['custom'], ',',
                                                  ':')
        return d_options
    else:
        return {}
