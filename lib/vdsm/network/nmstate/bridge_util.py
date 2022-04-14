# Copyright 2020-2021 Red Hat, Inc.
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

from vdsm.network.common.switch_util import SwitchType
from vdsm.network.link.iface import random_iface_name

from .schema import Interface
from .schema import InterfaceIP
from .schema import InterfaceIPv6
from .schema import InterfaceState

DEFAULT_MTU = 1500
OVN_BRIDGE_MAPPINGS_KEY = 'ovn-bridge-mappings'


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
        self.bridge_opts = attrs.get('custom', {}).get('bridge_opts')

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

        self.switch = attrs.get('switch', SwitchType.LINUX_BRIDGE)

        self.base_iface = self.nic or self.bond
        if self.vlan is not None:
            self.vlan_iface = '{}.{}'.format(self.base_iface, self.vlan)
        else:
            self.vlan_iface = None
        self.next_hop_interface = self._get_next_hop_interface()

    def _get_next_hop_interface(self):
        if self.switch == SwitchType.OVS or self.bridged:
            return self.name

        return self.vlan_iface or self.base_iface


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


def is_dhcp_enabled(family_info):
    return family_info[InterfaceIP.ENABLED] and family_info.get(
        InterfaceIP.DHCP, False
    )


def is_autoconf_enabled(family_info):
    return family_info[InterfaceIP.ENABLED] and family_info.get(
        InterfaceIPv6.AUTOCONF, False
    )


def get_auto_route_table_id(family_info):
    if family_info[InterfaceIP.ENABLED]:
        return family_info.get(InterfaceIP.AUTO_ROUTE_TABLE_ID)
    return None
