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


class NetInfo(object):
    DEFAULT_STP = 'off'
    DEFAULT_IPV6_GATEWAY = '::'
    DEFAULT_MTU = 1500
    DEFAULT_SWITCH = 'legacy'
    DEFAULT_BOND_OPTS = {'mode': '0'}

    @staticmethod
    def create_network(**kwargs):
        common_net_attrs = {
            'iface': '',
            'southbound': '',
            'ports': [],
            'stp': NetInfo.DEFAULT_STP,
            'bridged': True,
            'addr': '',
            'netmask': '',
            'ipv4addrs': [],
            'ipv6addrs': [],
            'ipv6autoconf': False,
            'gateway': '',
            'ipv6gateway': NetInfo.DEFAULT_IPV6_GATEWAY,
            'ipv4defaultroute': False,
            'mtu': NetInfo.DEFAULT_MTU,
            'switch': NetInfo.DEFAULT_SWITCH,
            'dhcpv4': False,
            'dhcpv6': False,
        }
        common_net_attrs.update(kwargs)
        return common_net_attrs

    @staticmethod
    def create_bond(**kwargs):
        common_bond_attrs = {
            'slaves': [],
            'opts': NetInfo.DEFAULT_BOND_OPTS,
            'switch': NetInfo.DEFAULT_SWITCH,
        }
        common_bond_attrs.update(kwargs)
        return common_bond_attrs

    @staticmethod
    def create_vlan(**kwargs):
        common_vlan_attrs = {
            'iface': '',
            'vlanid': '',
            'mtu': NetInfo.DEFAULT_MTU,
        }
        common_vlan_attrs.update(kwargs)
        return common_vlan_attrs

    @staticmethod
    def create_bridge(**kwargs):
        common_bridge_attrs = {'ports': []}
        common_bridge_attrs.update(kwargs)
        return common_bridge_attrs

    @staticmethod
    def create(**kwargs):
        common_net_info = {
            'networks': {},
            'vlans': {},
            'nics': [],
            'bridges': {},
            'bondings': {},
            'nameservers': [],
        }
        common_net_info.update(kwargs)
        return common_net_info
