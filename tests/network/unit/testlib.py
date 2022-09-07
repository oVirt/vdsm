# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later


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
