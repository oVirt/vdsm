#
# Copyright 2015 Red Hat, Inc.
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
import copy
import six
import string

from vdsm import constants
from vdsm.network.netinfo import bonding
from vdsm.network.netinfo import bridges
from vdsm.network.netinfo import dns
from vdsm.network.netinfo import routes
from vdsm.network.netconfpersistence import BaseConfig


class KernelConfig(BaseConfig):
    # TODO: after the netinfo API is refactored, we should decide if we need
    # TODO: the dependency of KernelConfig in a NetInfo object.
    # TODO: The only real dependency is on the products of
    # TODO: NetInfo.getNicsVlanAndBondingForNetwork and on NetInfo.Bondings
    def __init__(self, netinfo):
        super(KernelConfig, self).__init__({}, {})
        self._netinfo = netinfo
        for net, net_attr in self._analyze_netinfo_nets(netinfo):
            self.setNetwork(net, net_attr)
        for bond, bond_attr in self._analyze_netinfo_bonds(netinfo):
            self.setBonding(bond, bond_attr)

    def __eq__(self, other):
        normalized_other = normalize(other)
        return (self.networks == normalized_other.networks and
                self.bonds == normalized_other.bonds)

    def _analyze_netinfo_nets(self, netinfo):
        for net, net_attr in netinfo.networks.iteritems():
            yield net, _translate_netinfo_net(net, net_attr, netinfo)

    def _analyze_netinfo_bonds(self, netinfo):
        for bond, bond_attr in netinfo.bondings.iteritems():
            yield bond, _translate_netinfo_bond(bond_attr)


def normalize(running_config):
    config_copy = copy.deepcopy(running_config)

    _normalize_bonding_opts(config_copy)
    _normalize_address(config_copy)
    _normalize_ifcfg_keys(config_copy)

    return config_copy


def _translate_netinfo_net(net, net_attr, netinfo_):
    nics, _, vlan_id, bond = \
        netinfo_.getNicsVlanAndBondingForNetwork(net)
    attributes = {}
    _translate_bridged(attributes, net_attr)
    _translate_mtu(attributes, net_attr)
    _translate_vlan(attributes, vlan_id)
    if bond:
        _translate_bonding(attributes, bond)
    elif nics:
        _translate_nics(attributes, nics)
    _translate_ipaddr(attributes, net_attr)
    _translate_hostqos(attributes, net_attr)
    _translate_switch_type(attributes, net_attr)
    _translate_nameservers(attributes)

    return attributes


def _translate_ipaddr(attributes, net_attr):
    attributes['bootproto'] = 'dhcp' if net_attr['dhcpv4'] else 'none'
    attributes['dhcpv6'] = net_attr['dhcpv6']
    attributes['ipv6autoconf'] = net_attr['ipv6autoconf']

    attributes['defaultRoute'] = _translate_default_route(net_attr)

    # only static addresses are part of {Persistent,Running}Config.
    if attributes['bootproto'] == 'none':
        if net_attr['addr']:
            attributes['ipaddr'] = net_attr['addr']
        if net_attr['netmask']:
            attributes['netmask'] = net_attr['netmask']
        if net_attr['gateway']:
            attributes['gateway'] = net_attr['gateway']
    if not attributes['dhcpv6']:
        if net_attr['ipv6addrs']:
            # kernelconfig does not support multiple IPv6 addresses,
            # therefore a random one is chosen.
            attributes['ipv6addr'] = net_attr['ipv6addrs'][0]
        if net_attr['ipv6gateway'] != '::':
            attributes['ipv6gateway'] = net_attr['ipv6gateway']


def _translate_default_route(net_attr):
    is_default_route = net_attr.get('ipv4defaultroute')
    if is_default_route is None:
        return routes.is_default_route(net_attr['gateway'])
    else:
        return is_default_route


def _translate_nics(attributes, nics):
    nic, = nics
    attributes['nic'] = nic


def _translate_bonding(attributes, bond):
    attributes['bonding'] = bond


def _translate_vlan(attributes, vlan):
    if vlan is not None:
        attributes['vlan'] = vlan


def _translate_mtu(attributes, net_attr):
    attributes['mtu'] = net_attr['mtu']


def _translate_bridged(attributes, net_attr):
    attributes['bridged'] = net_attr['bridged']
    if net_attr['bridged']:
        attributes['stp'] = bridges.stp_booleanize(net_attr['stp'])


def _translate_netinfo_bond(bond_attr):
    return {
        'nics': sorted(bond_attr['slaves']),
        'options': bonding.bondOptsForIfcfg(bond_attr['opts']),
        'switch': bond_attr['switch']
    }


def _translate_hostqos(attributes, net_attr):
    if net_attr.get('hostQos'):
        attributes['hostQos'] = _remove_zero_values_in_net_qos(
            net_attr['hostQos'])


def _translate_switch_type(attributes, net_attr):
    attributes['switch'] = net_attr['switch']


def _translate_nameservers(attributes):
    nservers = dns.get_host_nameservers() if attributes['defaultRoute'] else []
    attributes['nameservers'] = nservers


def _remove_zero_values_in_net_qos(net_qos):
    """
    net_qos = {'out': {
            'ul': {'m1': 0, 'd': 0, 'm2': 8000000},
            'ls': {'m1': 4000000, 'd': 100000, 'm2': 3000000}}}
    stripped_qos = {'out': {
            'ul': {'m2': 8000000},
            'ls': {'m1': 4000000, 'd': 100000, 'm2': 3000000}}}"""
    stripped_qos = {}
    for part, part_config in net_qos.iteritems():
        stripped_qos[part] = dict(part_config)  # copy
        for curve, curve_config in part_config.iteritems():
            stripped_qos[part][curve] = dict((k, v) for k, v
                                             in curve_config.iteritems()
                                             if v != 0)
    return stripped_qos


def _normalize_bonding_opts(config_copy):
    for bond, bond_attr in config_copy.bonds.iteritems():
        # TODO: globalize default bond options from Bond in models.py
        normalized_opts = _parse_bond_options(
            bond_attr.get('options'))
        if 'mode' not in normalized_opts:
            normalized_opts['mode'] = '0'
        normalized_opts.pop('custom', None)
        bond_attr['options'] = bonding.bondOptsForIfcfg(normalized_opts)
    # before d18e2f10 bondingOptions were also part of networks, so in case
    # we are upgrading from an older version, they should be ignored if
    # they exist.
    # REQUIRED_FOR upgrade from vdsm<=4.16.20
    for net_attr in config_copy.networks.itervalues():
        net_attr.pop('bondingOptions', None)


def _normalize_address(config_copy):
    for net_name, net_attr in six.iteritems(config_copy.networks):
        if 'defaultRoute' not in net_attr:
            net_attr['defaultRoute'] = net_name in \
                constants.LEGACY_MANAGEMENT_NETWORKS


def _normalize_ifcfg_keys(config_copy):
    # ignore keys in persisted networks that might originate from vdsm-reg.
    # these might be a result of calling setupNetworks with ifcfg values
    # that come from the original interface that is serving the management
    # network. for 3.5, VDSM still supports passing arbitrary values
    # directly to the ifcfg files, e.g. 'IPV6_AUTOCONF=no'. we filter them
    # out here since kernelConfig will never report them.
    # TODO: remove when 3.5 is unsupported.
    def unsupported(key):
        return set(key) <= set(
            string.ascii_uppercase + string.digits + '_')

    for net_attr in config_copy.networks.itervalues():
        for k in net_attr.keys():
            if unsupported(k):
                net_attr.pop(k)


def _parse_bond_options(opts):
    if not opts:
        return {}

    opts = dict((pair.split('=', 1) for pair in opts.split()))

    # force a numeric bonding mode
    mode = opts.get('mode',
                    bonding.getAllDefaultBondingOptions()['0']['mode'][-1])
    if mode in bonding.BONDING_MODES_NUMBER_TO_NAME:
        numeric_mode = mode
    else:
        numeric_mode = bonding.BONDING_MODES_NAME_TO_NUMBER[mode]
        opts['mode'] = numeric_mode

    # Force a numeric value for an option
    for opname, opval in opts.items():
        numeric_val = bonding.get_bonding_option_numeric_val(numeric_mode,
                                                             opname, opval)
        if numeric_val is not None:
            opts[opname] = numeric_val

    defaults = bonding.getDefaultBondingOptions(numeric_mode)
    return dict(
        (k, v) for k, v in opts.iteritems() if v != defaults.get(k))
