# Copyright 2019 Red Hat, Inc.
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

import six

from vdsm.network.netconfpersistence import RunningConfig
from vdsm.network.link.bond.sysfs_options import BONDING_MODES_NUMBER_TO_NAME
from vdsm.network.link.setup import parse_bond_options

try:
    from libnmstate import netapplier
    from libnmstate.schema import Interface
    from libnmstate.schema import Route
except ImportError:  # nmstate is not available
    netapplier = None
    Interface = None
    Route = None


def setup(desired_state, verify_change):
    netapplier.apply(desired_state, verify_change)


def generate_state(networks, bondings):
    """ Generate a new nmstate state given VDSM setup state format """
    ifstates = {}
    route_states = []
    _generate_bonds_state(bondings, ifstates)
    _generate_networks_state(networks, ifstates, route_states)

    return merge_state(ifstates, route_states)


def _generate_networks_state(networks, ifstates, route_states):
    rconfig = RunningConfig()

    for netname, netattrs in six.viewitems(networks):
        if _is_remove(netattrs):
            _remove_network(netname, ifstates, route_states, rconfig)
        else:
            network_states = _create_network(netname, netattrs)
            for ifstate in network_states[Interface.KEY]:
                ifname = ifstate[Interface.NAME]
                if ifname in ifstates:
                    ifstates[ifname].update(ifstate)
                else:
                    ifstates[ifname] = ifstate
            net_routes_state = network_states[Route.KEY]
            if net_routes_state:
                route_states.append(net_routes_state)


def merge_state(interfaces_state, routes_state):
    interfaces = [ifstate for ifstate in six.viewvalues(interfaces_state)]
    state = {
        Interface.KEY: sorted(interfaces, key=lambda d: d[Interface.NAME])
    }
    if routes_state:
        state.update(routes={Route.CONFIG: routes_state})
    return state


def _create_network(netname, netattrs):
    nic = netattrs.get('nic')
    bond = netattrs.get('bonding')
    vlan = netattrs.get('vlan')
    bridged = netattrs['bridged']
    vlan_iface_state = _generate_vlan_iface_state(nic, bond, vlan)
    sb_iface_state = _generate_southbound_iface_state(nic, bond)
    bridge_iface_state = {}
    if bridged:
        bridge_port = vlan_iface_state or sb_iface_state
        stp_enabled = netattrs['stp']
        bridge_iface_state = _generate_bridge_iface_state(
            netname,
            bridge_port[Interface.NAME],
            options=_generate_bridge_options(stp_enabled)
        )

        # Bridge port IP stacks need to be disabled.
        _generate_iface_ipv4_state(bridge_port, netattrs={})
        _generate_iface_ipv6_state(bridge_port, netattrs={})
        ip_iface_state = bridge_iface_state
    else:
        ip_iface_state = vlan_iface_state or sb_iface_state
    _generate_iface_ipv4_state(ip_iface_state, netattrs)
    _generate_iface_ipv6_state(ip_iface_state, netattrs)

    return {
        Interface.KEY: [
            s for s in (sb_iface_state, vlan_iface_state, bridge_iface_state)
            if s
        ],
        Route.KEY: _generate_routes_state(netname, netattrs)
    }


def _generate_vlan_iface_state(nic, bond, vlan):
    if vlan:
        base_iface = nic or bond
        return {
            'vlan': {
                'id': vlan,
                'base-iface': base_iface,
            },
            Interface.NAME: '.'.join([base_iface, str(vlan)]),
            Interface.TYPE: 'vlan',
            Interface.STATE: 'up',
        }
    return {}


def _generate_southbound_iface_state(nic, bond):
    return {
        Interface.NAME: nic or bond,
        Interface.STATE: 'up',
    }


def _generate_bridge_iface_state(name, port, options=None):
    bridge_state = {
        Interface.NAME: name,
        'type': 'linux-bridge',
        Interface.STATE: 'up',
        'bridge': {
            'port': [
                {
                    'name': port,
                }
            ]
        }
    }
    if options:
        bridge_state['bridge']['options'] = options
    return bridge_state


def _generate_bridge_options(stp_enabled):
    return {
        'stp': {
            'enabled': stp_enabled,
        }
    }


def _remove_network(netname, ifstates, route_states, rconfig):
    netconf = rconfig.networks[netname]
    nic = netconf.get('nic')
    bond = netconf.get('bonding')
    base_iface = nic or bond
    vlan = netconf.get('vlan')
    iface_state = {}
    if vlan:
        vlan_interface = '.'.join([base_iface, str(vlan)])
        iface_state = {
            vlan_interface: {
                Interface.NAME: vlan_interface,
                Interface.STATE: 'absent',
            }
        }
    elif not ifstates.get(base_iface):
        iface_state = {
            base_iface: {
                Interface.NAME: base_iface,
                Interface.STATE: 'up',
                'ipv4': {'enabled': False},
                'ipv6': {'enabled': False}
            }
        }
    ifstates.update(iface_state)

    if netconf['bridged']:
        ifstates[netname] = {
            Interface.NAME: netname,
            Interface.STATE: 'absent'
        }


def _generate_bonds_state(bondings, ifstates):
    for bondname, bondattrs in six.viewitems(bondings):
        if bondattrs.get('remove'):
            iface_state = _remove_bond(bondname)
        else:
            iface_state = _create_bond(bondname, bondattrs)

        ifstates[bondname] = iface_state


def _remove_bond(bondname):
    iface_state = {
        Interface.NAME: bondname,
        Interface.TYPE: 'bond',
        Interface.STATE: 'absent'
    }
    return iface_state


def _create_bond(bondname, bondattrs):
    iface_state = {
        Interface.NAME: bondname,
        Interface.TYPE: 'bond',
        Interface.STATE: 'up',
        'link-aggregation': {},
        Interface.IPV4: {'enabled': False},
        Interface.IPV6: {'enabled': False}
    }
    mac = bondattrs.get('hwaddr')
    if mac:
        iface_state['mac-address'] = mac
    iface_state['link-aggregation']['slaves'] = sorted(bondattrs['nics'])
    bond_options = parse_bond_options(bondattrs.get('options'))
    bond_mode = bond_options.pop('mode', 'balance-rr')
    _set_bond_mode(iface_state, bond_mode)
    if bond_options:
        iface_state['link-aggregation']['options'] = bond_options

    return iface_state


def _set_bond_mode(iface_state, bond_mode):
    if bond_mode.isdigit():
        bond_mode = BONDING_MODES_NUMBER_TO_NAME[bond_mode]
    iface_state['link-aggregation']['mode'] = bond_mode


def _generate_iface_ipv4_state(iface_state, netattrs):
    ipv4addr = netattrs.get('ipaddr')
    dhcpv4 = netattrs.get('bootproto') == 'dhcp'
    if ipv4addr:
        _generate_iface_static_ipv4_state(iface_state, ipv4addr, netattrs)
    elif dhcpv4:
        _generate_iface_dynamic_ipv4_state(iface_state)
    else:
        iface_state['ipv4'] = {'enabled': False}


def _generate_iface_static_ipv4_state(iface_state, ipv4addr, netattrs):
    iface_ipv4_state = {'enabled': True}
    iface_ipv4_state['address'] = [{
        'ip': ipv4addr,
        'prefix-length': _get_ipv4_prefix_from_mask(netattrs['netmask'])
    }]
    iface_state['ipv4'] = iface_ipv4_state


def _generate_iface_dynamic_ipv4_state(iface_state):
    iface_ipv4_state = {
        'enabled': True,
        'dhcp': True
    }
    iface_state['ipv4'] = iface_ipv4_state


def _generate_routes_state(net_name, net_attributes):
    gateway = net_attributes.get('gateway')
    is_default_route = net_attributes['defaultRoute']
    next_hop_interface = get_next_hop_interface(net_name, net_attributes)
    if gateway:
        if is_default_route:
            return _generate_add_default_route_info(gateway,
                                                    next_hop_interface)
        else:
            return _generate_remove_default_route_state(gateway,
                                                        next_hop_interface)
    else:
        return {}


def _generate_add_default_route_info(gateway, next_hop_interface):
    return {
        Route.NEXT_HOP_ADDRESS: gateway,
        Route.NEXT_HOP_INTERFACE: next_hop_interface,
        Route.DESTINATION: '0.0.0.0/0',
        Route.TABLE_ID: Route.USE_DEFAULT_ROUTE_TABLE
    }


def _generate_remove_default_route_state(gateway, next_hop_interface):
    return {
        Route.NEXT_HOP_INTERFACE: next_hop_interface,
        Route.NEXT_HOP_ADDRESS: gateway,
        Route.DESTINATION: '0.0.0.0/0',
        Route.STATE: Route.STATE_ABSENT,
        Route.TABLE_ID: Route.USE_DEFAULT_ROUTE_TABLE
    }


def get_next_hop_interface(net_name, net_attributes):
    if net_attributes.get('bridged'):
        return net_name
    else:
        return (
            net_attributes.get('vlan') or
            net_attributes.get('nic') or
            net_attributes.get('bonding')
        )


def _generate_iface_ipv6_state(iface_state, netattrs):
    ipv6addr = netattrs.get('ipv6addr')
    dhcpv6 = netattrs.get('dhcpv6')
    autoconf = netattrs.get('ipv6autoconf')
    if ipv6addr:
        _generate_iface_static_ipv6_state(iface_state, ipv6addr)
    elif dhcpv6 or autoconf:
        _generate_iface_dynamic_ipv6_state(iface_state, dhcpv6, autoconf)
    else:
        iface_state['ipv6'] = {'enabled': False}


def _generate_iface_dynamic_ipv6_state(iface_state, dhcpv6, autoconf):
    iface_ipv6_state = {}
    if dhcpv6:
        iface_ipv6_state['enabled'] = True
        iface_ipv6_state['dhcp'] = True
    if autoconf:
        iface_ipv6_state['enabled'] = True
        iface_ipv6_state['autoconf'] = True
    if iface_ipv6_state:
        iface_state['ipv6'] = iface_ipv6_state


def _generate_iface_static_ipv6_state(iface_state, ipv6addr):
    iface_ipv6_state = {'enabled': True}
    address, prefix = ipv6addr.split('/')
    iface_ipv6_state['address'] = [{
        'ip': address,
        'prefix-length': int(prefix)
    }]
    iface_state['ipv6'] = iface_ipv6_state


def _get_ipv4_prefix_from_mask(ipv4netmask):
    prefix = 0
    for octet in ipv4netmask.split('.'):
        onebits = str(bin(int(octet))).strip('0b').rstrip('0')
        prefix += len(onebits)
    return prefix


def _is_remove(net_attributes):
    return net_attributes.get('remove', False)
