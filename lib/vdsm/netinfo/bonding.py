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
from functools import partial
from glob import iglob
import json
import logging
import six
import os

from .. import constants
from ..utils import memoized

from ..ipwrapper import Link
from .misc import _visible_devs
from .import nics

BONDING_ACTIVE_SLAVE = '/sys/class/net/%s/bonding/active_slave'
BONDING_DEFAULTS = constants.P_VDSM_LIB + 'bonding-defaults.json'
BONDING_FAILOVER_MODES = frozenset(('1', '3'))
BONDING_LOADBALANCE_MODES = frozenset(('0', '2', '4', '5', '6'))
BONDING_MASTERS = '/sys/class/net/bonding_masters'
BONDING_OPT = '/sys/class/net/%s/bonding/%s'
BONDING_SLAVES = '/sys/class/net/%s/bonding/slaves'
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

bondings = partial(_visible_devs, Link.isBOND)


def slaves(bonding):
    with open(BONDING_SLAVES % bonding) as f:
        return f.readline().split()


def bondinfo(link):
    return {'hwaddr': link.address, 'slaves': slaves(link.name),
            'active_slave': _active_slave(link.name),
            'opts': _getBondingOptions(link.name)}


def _active_slave(bonding):
    """
    :param bonding:
    :return: active slave when one exists or '' otherwise
    """
    with open(BONDING_ACTIVE_SLAVE % bonding) as f:
        return f.readline().rstrip()


def _getBondingOptions(bond):
    """
    Return non-empty options differing from defaults, excluding not actual or
    not applicable options, e.g. 'ad_num_ports' or 'slaves'.
    """
    opts = bondOpts(bond)
    mode = opts['mode'][-1] if 'mode' in opts else None
    defaults = getDefaultBondingOptions(mode)

    return dict(((opt, val[-1]) for (opt, val) in opts.iteritems()
                 if val and val != defaults.get(opt)))


def bondOpts(bond, keys=None):
    """
    Return a dictionary in the same format as _bondOpts(). Exclude entries that
    are not bonding options, e.g. 'ad_num_ports' or 'slaves'.
    """
    return dict(((opt, val) for (opt, val) in _bondOpts(bond, keys).iteritems()
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


def _bondOpts(bond, keys=None):
    """ Returns a dictionary of bond option name and a values iterable. E.g.,
    {'mode': ('balance-rr', '0'), 'xmit_hash_policy': ('layer2', '0')}
    """
    if keys is None:
        paths = iglob(BONDING_OPT % (bond, '*'))
    else:
        paths = (BONDING_OPT % (bond, key) for key in keys)
    opts = {}
    for path in paths:
        with open(path) as optFile:
            opts[os.path.basename(path)] = [
                el for el in optFile.read().rstrip().split(' ') if el]
    return opts


@memoized
def getAllDefaultBondingOptions():
    """
    Return default options per mode, in a dictionary of dictionaries. All keys
    are numeric modes stored as strings for coherence with 'mode' option value.
    """
    with open(BONDING_DEFAULTS) as defaults:
        return json.loads(defaults.read())


def speed(bondName):
    """Returns the bond speed if bondName refers to a bond, 0 otherwise."""
    opts = _bondOpts(bondName, keys=['slaves', 'active_slave', 'mode'])
    try:
        if opts['slaves']:
            if opts['mode'][1] in BONDING_FAILOVER_MODES:
                active_slave = opts['active_slave']
                s = nics.speed(active_slave[0]) if active_slave else 0
            elif opts['mode'][1] in BONDING_LOADBALANCE_MODES:
                s = sum(nics.speed(slave) for slave in opts['slaves'])
            return s
    except Exception:
        logging.exception('cannot read %s speed', bondName)
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
