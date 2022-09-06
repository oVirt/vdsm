# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import
from __future__ import division

from glob import iglob
import json
import os

import six

from vdsm.common import constants
from vdsm.common.cache import memoized

BONDING_DEFAULTS = constants.P_VDSM_RUN + 'bonding-defaults.json'
BONDING_OPT = '/sys/class/net/%s/bonding/%s'
ARP_IP_TARGET = 'arp_ip_target'


EXCLUDED_BONDING_ENTRIES = frozenset(
    (
        'slaves',
        'active_slave',
        'mii_status',
        'queue_id',
        'ad_aggregator',
        'ad_num_ports',
        'ad_actor_key',
        'ad_partner_key',
        'ad_partner_mac',
        'ad_actor_system',
    )
)

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
    (v, k) for k, v in six.iteritems(BONDING_MODES_NAME_TO_NUMBER)
)


def set_options(bond, requested_options):
    current_mode = properties(bond, ('mode',))['mode'][-1]
    desired_mode = requested_options.get('mode') or current_mode

    if desired_mode != current_mode:
        _set_mode(bond, desired_mode)
    current_options = get_options(properties(bond))
    _set_options(bond, requested_options, current_options)
    _set_untouched_options_to_defaults(
        bond, desired_mode, requested_options, current_options
    )


def _set_mode(bond, mode):
    _write_option(bond, 'mode', mode)


def _set_options(bond, requested_options, current_options):
    for key, value in six.iteritems(requested_options):
        if key != 'mode' and (
            key not in current_options or value != current_options[key]
        ):
            _set_option(bond, key, value, current_options.get(key))


def _set_untouched_options_to_defaults(
    bond, mode, requested_options, current_options
):
    mode = numerize_bond_mode(mode)
    for key, value in six.iteritems(getDefaultBondingOptions(mode)):
        if (
            key != 'mode'
            and key not in requested_options
            and key in current_options
        ):
            v = value[-1] if value else ''
            _set_option(bond, key, v, current_options[key])


def _set_option(bond, key, new_value, current_value):
    if key == ARP_IP_TARGET:
        _set_arp_ip_target(bond, new_value, current_value)
    else:
        _write_option(bond, key, new_value)


def _write_option(bond, key, value):
    with open(BONDING_OPT % (bond, key), 'w') as f:
        f.write(value)


def _set_arp_ip_target(bond, new_value, current_value):
    if current_value:
        current_arp_ip_target = current_value.split(',')
    else:
        current_arp_ip_target = []

    new_arp_ip_target = new_value.split(',') if new_value else []

    addrs_to_add = set(new_arp_ip_target) - set(current_arp_ip_target)
    addrs_to_del = set(current_arp_ip_target) - set(new_arp_ip_target)
    for addr in addrs_to_del:
        _write_option(bond, ARP_IP_TARGET, '-%s' % addr)
    for addr in addrs_to_add:
        _write_option(bond, ARP_IP_TARGET, '+%s' % addr)


def get_options(bond_properties, filter_out_defaults=True, filter_opts=None):
    """
    Filter bond options from given bond properties.

    If filter_out_defaults is set, exclude defaults from the report.
    Note: mode should always be reported, even if it's the default one.

    If filter_options is provided, use it as the filter.

    Options with no value are filtered out.

    Bond options are a subset of bond properties and refer to properties that
    can be set and affect bond operation.
    """
    opts_keys = (
        filter_opts or six.viewkeys(bond_properties) - EXCLUDED_BONDING_ENTRIES
    )

    if filter_out_defaults:
        opts_keys = _filter_out_default_opts(bond_properties, opts_keys)

    return {k: bond_properties[k][-1] for k in opts_keys if bond_properties[k]}


def _filter_out_default_opts(bond_properties, opts_keys):
    mode = bond_properties['mode'][-1] if 'mode' in opts_keys else None
    defaults = getDefaultBondingOptions(mode)
    non_default_opts_keys = {
        opt
        for opt in opts_keys
        if bond_properties[opt]
        and (bond_properties[opt] != defaults.get(opt) or opt == 'mode')
    }
    return non_default_opts_keys


def properties(bond_name, filter_properties=None, filter_out_properties=()):
    """
    Returns a dictionary of bond property name as key and a list as value.
    E.g. {'mode': ['balance-rr', '0'], 'xmit_hash_policy': ['layer2', '0']}

    If filter_properties is provided (as an iterable object), the returned
    properties will include only those that are explicitly specified in it,
    otherwise no restriction is applied.

    If filter_out_properties is provided, the property names it includes are
    excluded from the properties returned, otherwise no restriction is applied.
    """
    if filter_properties is None:
        paths = iglob(BONDING_OPT % (bond_name, '*'))
    else:
        paths = (BONDING_OPT % (bond_name, key) for key in filter_properties)

    properties_path = ((os.path.basename(path), path) for path in paths)

    props = {
        name: bond_opts_read_elements(path)
        for name, path in properties_path
        if name not in filter_out_properties
    }

    normalize_arp_ip_target(props)

    return props


def bond_opts_read_elements(file_path):
    with open(file_path) as f:
        return [el for el in f.read().rstrip().split(' ') if el]


def normalize_arp_ip_target(opts):
    """
    Sysfs reports multiple ip addresses in arp_ip_target separated by space,
    which are represented by multiple elements in opts[ARP_IP_TARGET].
    The bonding driver accepts multiple ip addresses in the arp_ip_target
    option separated by a comma.
    To enable an unified handling for all options, the value of arp_ip_target
    is converted in the expression, which would accepted as value for setting
    this option. This is the separation of multiple ip addresses by comma.
    """
    if ARP_IP_TARGET in opts and len(opts[ARP_IP_TARGET]) > 1:
        opts[ARP_IP_TARGET] = [','.join(opts[ARP_IP_TARGET])]


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


def numerize_bond_mode(mode):
    return (
        mode
        if mode in BONDING_MODES_NUMBER_TO_NAME
        else BONDING_MODES_NAME_TO_NUMBER[mode]
    )
