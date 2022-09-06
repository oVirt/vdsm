# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import
from __future__ import division
import copy
import six

from vdsm.network.link.bond import sysfs_options
from vdsm.network.link.bond import sysfs_options_mapper as bond_opts_mapper
from vdsm.network.netinfo import bonding
from vdsm.network.netinfo import bridges
from vdsm.network.netinfo import routes
from vdsm.network.netconfpersistence import BaseConfig, RunningConfig


class MultipleSouthBoundNicsPerNetworkError(Exception):
    pass


class KernelConfig(BaseConfig):
    # TODO: after the netinfo API is refactored, we should decide if we need
    # TODO: the dependency of KernelConfig in a NetInfo object.
    # TODO: The only real dependency is on the products of
    # TODO: NetInfo.getNicsVlanAndBondingForNetwork and on NetInfo.Bondings
    def __init__(self, netinfo):
        super(KernelConfig, self).__init__({}, {}, {})
        self._netinfo = netinfo
        for net, net_attr in self._analyze_netinfo_nets(netinfo):
            self.setNetwork(net, net_attr)
        for bond, bond_attr in self._analyze_netinfo_bonds(netinfo):
            self.setBonding(bond, bond_attr)

    def __eq__(self, other):
        normalized_other = normalize(other)
        return (
            self.networks == normalized_other.networks
            and self.bonds == normalized_other.bonds
        )

    def __hash__(self):
        return hash((self.networks, self.bonds))

    def _analyze_netinfo_nets(self, netinfo):
        _routes = routes.get_routes()
        for net, net_attr in six.viewitems(netinfo.networks):
            attrs = _translate_netinfo_net(net, net_attr, netinfo, _routes)
            yield net, attrs

    def _analyze_netinfo_bonds(self, netinfo):
        rconfig = RunningConfig()
        for bond, bond_attr in six.viewitems(netinfo.bondings):
            bond_rconf = rconfig.bonds.get(bond)
            yield bond, _translate_netinfo_bond(bond_attr, bond_rconf)


def normalize(running_config):
    config_copy = copy.deepcopy(running_config)

    _normalize_bonding_opts(config_copy)

    return config_copy


def _translate_netinfo_net(net, net_attr, netinfo_, _routes):
    nics, _, vlan_id, bond = netinfo_.getNicsVlanAndBondingForNetwork(net)
    attributes = {}
    _translate_bridged(attributes, net_attr)
    _translate_mtu(attributes, net_attr)
    _translate_vlan(attributes, vlan_id)
    if bond:
        _translate_bonding(attributes, bond)
    elif nics:
        if len(nics) > 1:
            raise MultipleSouthBoundNicsPerNetworkError(net, nics)
        _translate_nics(attributes, nics)
    attributes['defaultRoute'] = _translate_default_route(net_attr, _routes)
    _translate_ipaddr(attributes, net_attr)
    _translate_hostqos(attributes, net_attr)
    _translate_switch_type(attributes, net_attr)
    _translate_nameservers(attributes, netinfo_)

    return attributes


def _translate_ipaddr(attributes, net_attr):
    attributes['bootproto'] = 'dhcp' if net_attr['dhcpv4'] else 'none'
    attributes['dhcpv6'] = net_attr['dhcpv6']
    attributes['ipv6autoconf'] = net_attr['ipv6autoconf']

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


def _translate_default_route(net_attr, _routes):
    is_default_route = net_attr.get('ipv4defaultroute')
    if is_default_route is None:
        return routes.is_default_route(net_attr['gateway'], _routes)
    else:
        return is_default_route


def _translate_nics(attributes, nics):
    (nic,) = nics
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


def _translate_netinfo_bond(bond_attr, bond_running_config):
    bond_conf = {
        'nics': sorted(bond_attr['slaves']),
        'options': bonding.bondOptsForIfcfg(bond_attr['opts']),
        'switch': bond_attr['switch'],
    }
    hwaddr_explicitly_set = (
        bond_running_config and 'hwaddr' in bond_running_config
    )
    if hwaddr_explicitly_set:
        bond_conf['hwaddr'] = bond_attr['hwaddr']
    return bond_conf


def _translate_hostqos(attributes, net_attr):
    if net_attr.get('hostQos'):
        attributes['hostQos'] = _remove_zero_values_in_net_qos(
            net_attr['hostQos']
        )


def _translate_switch_type(attributes, net_attr):
    attributes['switch'] = net_attr['switch']


def _translate_nameservers(attributes, netinfo):
    nservers = netinfo.nameservers if attributes['defaultRoute'] else []
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
    for part, part_config in six.viewitems(net_qos):
        stripped_qos[part] = dict(part_config)  # copy
        for curve, curve_config in six.viewitems(part_config):
            stripped_qos[part][curve] = dict(
                (k, v) for k, v in six.viewitems(curve_config) if v != 0
            )
    return stripped_qos


def _normalize_bonding_opts(config_copy):
    for bond, bond_attr in six.viewitems(config_copy.bonds):
        # TODO: globalize default bond options from Bond in models.py
        normalized_opts = _parse_bond_options(bond_attr.get('options'))
        if 'mode' not in normalized_opts:
            normalized_opts['mode'] = '0'
        normalized_opts.pop('custom', None)
        bond_attr['options'] = bonding.bondOptsForIfcfg(normalized_opts)
    # before d18e2f10 bondingOptions were also part of networks, so in case
    # we are upgrading from an older version, they should be ignored if
    # they exist.
    # REQUIRED_FOR upgrade from vdsm<=4.16.20
    for net_attr in six.viewvalues(config_copy.networks):
        net_attr.pop('bondingOptions', None)


def _parse_bond_options(opts):
    if not opts:
        return {}

    opts = dict((pair.split('=', 1) for pair in opts.split()))

    mode = opts.get(
        'mode', sysfs_options.getAllDefaultBondingOptions()['0']['mode'][-1]
    )
    opts['mode'] = numeric_mode = sysfs_options.numerize_bond_mode(mode)

    # Force a numeric value for an option
    for opname, opval in opts.items():
        numeric_val = bond_opts_mapper.get_bonding_option_numeric_val(
            numeric_mode, opname, opval
        )
        if numeric_val is not None:
            opts[opname] = numeric_val

    defaults = sysfs_options.getDefaultBondingOptions(numeric_mode)
    return dict((k, v) for k, v in six.viewitems(opts) if v != defaults.get(k))
