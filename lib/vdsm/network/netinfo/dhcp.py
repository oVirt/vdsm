#
# Copyright 2015 Hat, Inc.
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
from __future__ import absolute_import

from . import addresses


def set_netdev_dhcp_info(network_info, networking):
    dev_info = _device_lookup(network_info['iface'], networking)
    if dev_info:
        dev_info['dhcpv4'] = network_info['dhcpv4']
        dev_info['dhcpv6'] = network_info['dhcpv6']


def dhcp_status(iface, ipaddrs):
    is_dhcpv4 = False
    is_dhcpv6 = False
    for ipaddr in ipaddrs[iface]:
        if addresses.is_ipv4(ipaddr):
            is_dhcpv4 |= addresses.is_dynamic(ipaddr)
        elif addresses.is_ipv6(ipaddr):
            is_dhcpv6 |= addresses.is_dynamic(ipaddr)
    return is_dhcpv4, is_dhcpv6


def dhcp_faked_status(iface, ipaddrs, net_attrs):
    # If running config exists for this network, fake dhcp status
    # and report the request (not the actual)
    # Engine expects this behaviour.
    if net_attrs is None:
        is_dhcpv4, is_dhcpv6 = dhcp_status(iface, ipaddrs)
    else:
        is_dhcpv4 = (net_attrs.get('bootproto', None) == 'dhcp')
        is_dhcpv6 = net_attrs.get('dhcpv6', False)
    return is_dhcpv4, is_dhcpv6


def _device_lookup(netdev, networking):
    for dev_type in ('bridges', 'vlans', 'bondings', 'nics'):
        dev_info = networking[dev_type].get(netdev)
        if dev_info:
            return dev_info
    return None
