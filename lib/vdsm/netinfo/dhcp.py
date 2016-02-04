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
import six

from . import addresses

# possible names of dhclient's lease files (e.g. as NetworkManager's slave)
DHCLIENT_LEASES_GLOBS = [
    '/var/lib/dhclient/dhclient*.lease*',  # iproute2 configurator, initscripts
    '/var/lib/NetworkManager/dhclient*-*.lease',
]


def propose_updates_to_reported_dhcp(network_info, networking):
    """
    Report DHCPv4/6 of a network's topmost device based on the network's
    configuration, to fix bug #1184497 (DHCP still being reported for hours
    after a network got static IP configuration, as reporting is based on
    dhclient leases).
    """
    updated_networking = dict(bondings={}, bridges={}, nics={}, vlans={})
    network_device = network_info['iface']

    for devices in ('bridges', 'vlans', 'bondings', 'nics'):
        dev_info = networking[devices].get(network_device)
        if dev_info:
            cfg = {}
            updated_networking[devices][network_device] = {
                'dhcpv4': network_info['dhcpv4'],
                'dhcpv6': network_info['dhcpv6'],
                'cfg': cfg,
            }
            cfg['BOOTPROTO'] = 'dhcp' if network_info['dhcpv4'] else 'none'
            break

    return updated_networking


def update_reported_dhcp(replacement, networking):
    """
    For each network device (representing a network), apply updates to reported
    DHCP-related fields, as prepared by _propose_updates_to_reported_dhcp.
    """
    for device_type, devices in six.iteritems(replacement):
        for device_name, replacement_device_info in six.iteritems(devices):
            device_info = networking[device_type][device_name]
            device_info['dhcpv4'] = replacement_device_info['dhcpv4']
            device_info['dhcpv6'] = replacement_device_info['dhcpv6']
            # Remove when cluster level < 3.6 is no longer supported and thus
            # it is not necessary to report ifcfg-like BOOTPROTO field.
            if replacement_device_info['cfg']:
                device_info['cfg'].update(replacement_device_info['cfg'])


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
