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
except ImportError:  # nmstate is not available
    netapplier = None

INTERFACES = 'interfaces'


def setup(desired_state, verify_change):
    netapplier.apply(desired_state, verify_change)


def generate_state(networks, bondings):
    """ Generate a new nmstate state given VDSM setup state format """
    ifstates = {}

    _generate_bonds_state(bondings, ifstates)
    _generate_networks_state(networks, ifstates)

    interfaces = [ifstate for ifstate in six.viewvalues(ifstates)]
    return {INTERFACES: sorted(interfaces, key=lambda d: d['name'])}


def _generate_networks_state(networks, ifstates):
    rconfig = RunningConfig()

    for netname, netattrs in six.viewitems(networks):
        if netattrs.get('remove'):
            _remove_network(netname, ifstates, rconfig)
        else:
            _create_network(ifstates, netname, netattrs)


def _create_network(ifstates, netname, netattrs):
    nic = netattrs.get('nic')
    bond = netattrs.get('bonding')
    vlan = netattrs.get('vlan')
    bridged = netattrs['bridged']
    vlan_iface_state = _generate_vlan_iface_state(nic, bond, vlan)
    sb_iface_state = _generate_southbound_iface_state(ifstates, nic, bond)
    if bridged:
        bridge_port = vlan_iface_state or sb_iface_state
        bridge_iface_state = _generate_bridge_iface_state(netname,
                                                          bridge_port['name'])

        # Bridge port IP stacks need to be disabled.
        _generate_iface_ipv4_state(bridge_port, netattrs={})
        _generate_iface_ipv6_state(bridge_port, netattrs={})
        ip_iface_state = bridge_iface_state
    else:
        ip_iface_state = vlan_iface_state or sb_iface_state
    _generate_iface_ipv4_state(ip_iface_state, netattrs)
    _generate_iface_ipv6_state(ip_iface_state, netattrs)
    ifstates[sb_iface_state['name']] = sb_iface_state
    if vlan_iface_state:
        ifstates[vlan_iface_state['name']] = vlan_iface_state
    if bridged:
        ifstates[bridge_iface_state['name']] = bridge_iface_state


def _generate_vlan_iface_state(nic, bond, vlan):
    if vlan:
        base_iface = nic or bond
        return {
            'name': '.'.join([base_iface, str(vlan)]),
            'type': 'vlan',
            'state': 'up',
            'vlan': {
                'id': vlan,
                'base-iface': base_iface
            }
        }
    return {}


def _generate_southbound_iface_state(ifstates, nic, bond):
    if nic:
        iface_state = {}
        iface_state['name'] = nic
    else:
        iface_state = ifstates[bond]
        iface_state['name'] = bond
    iface_state['state'] = 'up'
    return iface_state


def _generate_bridge_iface_state(name, port):
    return {
        'name': name,
        'type': 'linux-bridge',
        'state': 'up',
        'bridge': {
            'port': [
                {
                    'name': port,
                }
            ]
        }
    }


def _remove_network(netname, ifstates, rconfig):
    netconf = rconfig.networks[netname]
    nic = netconf.get('nic')
    bond = netconf.get('bonding')
    base_iface = nic or bond
    vlan = netconf.get('vlan')
    if vlan:
        iface_state = {
            'name': '.'.join([base_iface, str(vlan)]),
            'state': 'absent',
        }
    else:
        iface_state = {
            'name': base_iface,
            'state': 'up',
            'ipv4': {'enabled': False},
            'ipv6': {'enabled': False}
        }
    ifstates[iface_state['name']] = iface_state

    if netconf['bridged']:
        ifstates[netname] = {
            'name': netname,
            'state': 'absent'
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
        'name': bondname,
        'type': 'bond',
        'state': 'absent'
    }
    return iface_state


def _create_bond(bondname, bondattrs):
    iface_state = {
        'name': bondname,
        'type': 'bond',
        'state': 'up',
        'link-aggregation': {},
        'ipv4': {'enabled': False},
        'ipv6': {'enabled': False}
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
        'prefix-length': prefix
    }]
    iface_state['ipv6'] = iface_ipv6_state


def _get_ipv4_prefix_from_mask(ipv4netmask):
    prefix = 0
    for octet in ipv4netmask.split('.'):
        onebits = str(bin(int(octet))).strip('0b').rstrip('0')
        prefix += len(onebits)
    return prefix
