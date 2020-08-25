# Copyright 2020 Red Hat, Inc.
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

from vdsm.network.link.iface import random_iface_name

from .schema import Interface
from .schema import InterfaceState

DEFAULT_MTU = 1500


class NetworkConfig(object):
    def __init__(self, name, attrs):
        if not attrs:
            name = None
        self.name = name
        self.vlan = attrs.get('vlan')
        self.nic = attrs.get('nic')
        self.bond = attrs.get('bonding')
        self.bridged = attrs.get('bridged')
        self.stp = attrs.get('stp')
        self.mtu = attrs.get('mtu', DEFAULT_MTU)

        self.ipv4addr = attrs.get('ipaddr')
        self.ipv4netmask = attrs.get('netmask')
        self.dhcpv4 = attrs.get('bootproto') == 'dhcp'

        self.ipv6addr = attrs.get('ipv6addr')
        self.dhcpv6 = attrs.get('dhcpv6', False)
        self.ipv6autoconf = attrs.get('ipv6autoconf', False)
        self.ipv6gateway = attrs.get('ipv6gateway')

        self.gateway = attrs.get('gateway')
        self.default_route = attrs.get('defaultRoute')

        self.nameservers = attrs.get('nameservers')

        self.remove = attrs.get('remove', False)

        self.base_iface = self.nic or self.bond
        if self.vlan is not None:
            self.vlan_iface = '{}.{}'.format(self.base_iface, self.vlan)
        else:
            self.vlan_iface = None


class SwitchType(object):
    LINUX_BRIDGE = 'legacy'
    OVS = 'ovs'


class NetInfoSchema(object):
    NETWORKS = 'networks'
    VLANS = 'vlans'
    BONDS = 'bondings'
    NICS = 'nics'
    BRIDGES = 'bridges'


class NetInfoIfaceSchema(object):
    IFACE = 'iface'
    BRIDGED = 'bridged'
    SOUTHBOUND = 'southbound'
    PORTS = 'ports'
    STP = 'stp'
    SWITCH = 'switch'
    MTU = 'mtu'
    VLAN = 'vlanid'

    class IPv4(object):
        PRIMARY_ADDR = 'addr'
        ADRRS = 'ipv4addrs'
        DEFAULT_ROUTE = 'ipv4defaultroute'
        NETMASK = 'netmask'
        GATEWAY = 'gateway'
        DHCP = 'dhcpv4'

    class IPv6(object):
        ADDRS = 'ipv6addrs'
        GATEWAY = 'ipv6gateway'
        AUTOCONF = 'ipv6autoconf'
        DHCP = 'dhcpv6'


def get_default_route_interface(running_networks):
    for netname, attrs in running_networks.items():
        netrun = NetworkConfig(netname, attrs)
        if netrun.default_route:
            if netrun.bridged:
                ifnet = netrun.name
            else:
                ifnet = netrun.vlan_iface or netrun.base_iface
            return ifnet
    return None


def is_iface_absent(ifstate):
    return ifstate and ifstate.get(Interface.STATE) == InterfaceState.ABSENT


def is_iface_up(ifstate):
    return ifstate and ifstate[Interface.STATE] == InterfaceState.UP


def is_default_mtu(state):
    return state.get(Interface.MTU, DEFAULT_MTU) == DEFAULT_MTU


def random_interface_name(iface_prefix):
    return random_iface_name(prefix=iface_prefix)


def translate_config(networks):
    return {
        netname: NetworkConfig(netname, netattrs)
        for netname, netattrs in networks.items()
    }
