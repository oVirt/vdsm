# Copyright 2016 Red Hat, Inc.
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

from glob import iglob
import json
import os

import six

from vdsm import constants
from vdsm.utils import memoized

BONDING_DEFAULTS = constants.P_VDSM + 'bonding-defaults.json'
BONDING_OPT = '/sys/class/net/%s/bonding/%s'


EXCLUDED_BONDING_ENTRIES = frozenset((
    'slaves',
    'active_slave',
    'mii_status',
    'queue_id',
    'ad_aggregator',
    'ad_num_ports',
    'ad_actor_key',
    'ad_partner_key',
    'ad_partner_mac',
    'ad_actor_system'
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


def set_options(bond, options):
    for key, value in options:
        with open(BONDING_OPT % (bond, key), 'w') as f:
            f.write(value)


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


def _bond_opts_read_elements(file_path):
    with open(file_path) as f:
        return [el for el in f.read().rstrip().split(' ') if el]


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


@memoized
def getAllDefaultBondingOptions():
    """
    Return default options per mode, in a dictionary of dictionaries. All keys
    are numeric modes stored as strings for coherence with 'mode' option value.
    """
    with open(BONDING_DEFAULTS) as defaults:
        return json.loads(defaults.read())
