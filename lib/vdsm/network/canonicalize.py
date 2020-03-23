# Copyright 2016-2019 Red Hat, Inc.
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

import six

from .netinfo import bonding, bridges
from vdsm.common.conv import tobool
from vdsm.network import dns
from vdsm.network import nmstate
from vdsm.network.ip.address import IPAddressData
from vdsm.network.ip.address import prefix2netmask
from vdsm.network.link import bond
from vdsm.network.link import iface
from vdsm.network.netconfpersistence import RunningConfig

from .errors import ConfigNetworkError
from . import errors as ne


def canonicalize_networks(nets):
    """
    Given networks configuration, explicitly add missing defaults.
    :param nets: The network configuration
    """
    _canonicalize_ip_default_route(nets)

    for _, attrs in _entities_to_canonicalize(nets):
        _canonicalize_link(attrs)
        _canonicalize_bridge(attrs)
        _canonicalize_ipv4(attrs)
        _canonicalize_ipv6(attrs)
        _canonicalize_nameservers(attrs)
        _canonicalize_switch_type_net(attrs)


def canonicalize_bondings(bonds):
    """
    Given bondings configuration, explicitly add missing defaults.
    :param bonds: The bonding configuration
    """
    for bondname, attrs in _entities_to_canonicalize(bonds):
        _canonicalize_bond_slaves(attrs)
        _canonicalize_switch_type_bond(attrs)
        _canonicalize_bond_hwaddress(bondname, attrs)


def _entities_to_canonicalize(entities):
    """
    Returns an interator of all entities which should be canonicalized.

    If net/bond is marked for removal, normalize the mark to boolean and
    do not return it for further processing.

    :param entities: Network or Bond requested configuration dicts
    """
    return (
        (name, attrs)
        for name, attrs in six.viewitems(entities)
        if not _canonicalize_remove(attrs)
    )


def canonicalize_external_bonds_used_by_nets(nets, bonds):
    for netattrs in six.viewvalues(nets):
        if 'remove' in netattrs:
            continue
        bondname = netattrs.get('bonding')
        if bondname and bondname not in bonds:
            bond_dev = bond.Bond(bondname)
            if bond_dev.exists():
                bonds[bondname] = {
                    'nics': list(bond_dev.slaves),
                    'options': bonding.bondOptsForIfcfg(bond_dev.options),
                    'switch': netattrs['switch'],
                }


def _canonicalize_remove(data):
    if 'remove' in data:
        data['remove'] = tobool(data['remove'])
        return data['remove']
    return False


def _canonicalize_link(data):
    _canonicalize_mtu(data)
    _canonicalize_vlan(data)


def _canonicalize_mtu(data):
    data['mtu'] = int(data['mtu']) if 'mtu' in data else iface.DEFAULT_MTU


def _canonicalize_vlan(data):
    vlan = data.get('vlan', None)
    if vlan in (None, ''):
        data.pop('vlan', None)
    else:
        data['vlan'] = int(vlan)


def _canonicalize_bridge(data):
    _canonicalize_bridged(data)
    _canonicalize_bridge_opts(data)
    _canonicalize_stp(data)


def _canonicalize_bridged(data):
    if 'bridged' in data:
        data['bridged'] = tobool(data['bridged'])
    else:
        data['bridged'] = True


def _canonicalize_stp(data):
    if data['bridged']:
        stp = False
        if 'stp' in data:
            stp = data['stp']
        elif 'STP' in data:
            stp = data.pop('STP')
        try:
            data['stp'] = bridges.stp_booleanize(stp)
        except ValueError:
            raise ConfigNetworkError(
                ne.ERR_BAD_PARAMS,
                '"%s" is not ' 'a valid bridge STP value.' % stp,
            )


def _canonicalize_bridge_opts(data):
    opts_str = data.get('custom', {}).get('bridge_opts')
    if not opts_str:
        return
    opts_dict = bridge_opts_str_to_dict(opts_str)
    data['custom']['bridge_opts'] = bridge_opts_dict_to_sorted_str(opts_dict)


def bridge_opts_str_to_dict(opts_str):
    return dict([pair.split('=', 1) for pair in opts_str.split()])


def bridge_opts_dict_to_sorted_str(opts_dict):
    opts_pairs = [
        '{}={}'.format(key, val) for key, val in six.viewitems(opts_dict)
    ]
    opts_pairs.sort()
    return ' '.join(opts_pairs)


def _canonicalize_ipv4(data):
    _canonicalize_dhcpv4(data)
    _canonicalize_ipv4_netmask(data)


def _canonicalize_dhcpv4(data):
    if 'bootproto' not in data:
        data['bootproto'] = 'none'


def _canonicalize_ipv4_netmask(data):
    prefix = data.pop('prefix', None)
    if prefix:
        if 'netmask' in data:
            raise ConfigNetworkError(
                ne.ERR_BAD_PARAMS, 'Both PREFIX and NETMASK supplied'
            )
        try:
            data['netmask'] = prefix2netmask(int(prefix))
        except ValueError as ve:
            raise ConfigNetworkError(ne.ERR_BAD_ADDR, 'Bad prefix: %s' % ve)


def _canonicalize_ipv6(data):
    if 'dhcpv6' not in data:
        data['dhcpv6'] = False
    if 'ipv6autoconf' not in data:
        data['ipv6autoconf'] = False
    ipv6_addr = data.get('ipv6addr')
    if ipv6_addr:
        data['ipv6addr'] = _compress_ipv6_address(ipv6_addr)


def _compress_ipv6_address(ipv6_addr):
    wrapped_ipv6_address = IPAddressData(ipv6_addr, device=None)
    return wrapped_ipv6_address.address_with_prefixlen


def _canonicalize_switch_type_net(data):
    if tobool(_rget(data, ('custom', 'ovs'))):
        data['switch'] = 'ovs'
    elif 'switch' not in data:
        data['switch'] = 'legacy'


def _canonicalize_switch_type_bond(data):
    if 'switch' not in data:
        data['switch'] = 'legacy'


def _canonicalize_bond_slaves(data):
    if 'nics' in data:
        data['nics'].sort()


def _canonicalize_ip_default_route(nets):
    default_route_nets = []
    for net, data in _entities_to_canonicalize(nets):
        if 'defaultRoute' not in data:
            data['defaultRoute'] = False

        custom_default_route = _rget(data, ('custom', 'default_route'))
        if custom_default_route is not None:
            logging.warning(
                'Custom property default_route is deprecated. '
                'please use default route role.'
            )
            data['defaultRoute'] = tobool(custom_default_route)

        if data['defaultRoute']:
            default_route_nets.append(net)

    if len(default_route_nets) > 1:
        raise ne.ConfigNetworkError(
            ne.ERR_BAD_PARAMS,
            'Only a single default route network is allowed.',
        )
    elif default_route_nets:
        existing_net_with_default_route = _net_with_default_route_from_config()
        if existing_net_with_default_route:
            netname, attrs = existing_net_with_default_route
            if netname not in nets:
                # Copy config from running and add to setup
                attrs.pop('nameservers', None)
                nets[netname] = attrs
                nets[netname]['defaultRoute'] = False


def _net_with_default_route_from_config():
    for net, attrs in six.iteritems(RunningConfig().networks):
        if attrs.get('defaultRoute', False):
            return net, attrs
    return None


def _canonicalize_nameservers(data):
    if 'nameservers' not in data:
        # Nameservers are relevant only for the default route network (usually
        # the management network)
        if data['defaultRoute'] and data['bootproto'] != 'dhcp':
            data['nameservers'] = dns.get_host_nameservers()
            # FIXME https://bugzilla.redhat.com/1816043
            if nmstate.is_nmstate_backend():
                data['nameservers'] = data['nameservers'][:2]
        else:
            data['nameservers'] = []


def _rget(dict, keys, default=None):
    """Recursive dictionary.get()

    >>> _rget({'a': {'b': 'hello'}}, ('a', 'b'))
    'hello'
    """
    if dict is None:
        return default
    elif len(keys) == 0:
        return dict
    return _rget(dict.get(keys[0]), keys[1:], default)


def _canonicalize_bond_hwaddress(bondname, bondattrs):
    if 'hwaddr' not in bondattrs:
        if _bond_hwaddr_should_be_enforced(bondname):
            bondattrs['hwaddr'] = iface.iface(bondname).address()


def _bond_hwaddr_should_be_enforced(bondname):
    """
    Bond MAC address is to be enforced under these conditions:
        - Bond device exists already.
        - One of these conditions exists (OR):
            - Unowned by VDSM (not in running config).
            - Owned by VDSM and HWADDR is specified in the config.
    """
    bond_dev = bond.Bond(bondname)
    if bond_dev.exists():
        running_bonds = RunningConfig().bonds
        bondattr = running_bonds.get(bondname)
        return not bondattr or bondattr.get('hwaddr')
    return False
