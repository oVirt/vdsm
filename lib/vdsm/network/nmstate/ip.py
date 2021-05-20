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

from .route import DEFAULT_TABLE_ID
from .route import generate_table_id
from .schema import InterfaceIP
from .schema import InterfaceIPv6


class IpAddress(object):
    IPV4 = 4
    IPV6 = 6

    def __init__(self, netconf, auto_dns):
        self._netconf = netconf
        self._auto_dns = auto_dns

    def create(self, family, enabled=True):
        if family == IpAddress.IPV4:
            return self._create_ipv4(enabled)
        elif family == IpAddress.IPV6:
            return self._create_ipv6(enabled)
        else:
            return {}

    def _create_ipv4(self, enabled):
        ipstate = {InterfaceIP.ENABLED: enabled}
        if enabled:
            if self._netconf.ipv4addr:
                ipv4_address = self._create_static_ipv4_address()
                ipstate[InterfaceIP.ADDRESS] = ipv4_address
                ipstate[InterfaceIP.DHCP] = False
            elif self._netconf.dhcpv4:
                ipstate.update(self._create_dynamic_ipv4())
            else:
                ipstate[InterfaceIP.ENABLED] = False

        return ipstate

    def _create_static_ipv4_address(self):
        return [
            {
                InterfaceIP.ADDRESS_IP: self._netconf.ipv4addr,
                InterfaceIP.ADDRESS_PREFIX_LENGTH: _get_ipv4_prefix_from_mask(
                    self._netconf.ipv4netmask
                ),
            }
        ]

    def _create_dynamic_ipv4(self):
        return {
            InterfaceIP.DHCP: self._netconf.dhcpv4,
            InterfaceIP.AUTO_DNS: self._auto_dns,
            InterfaceIP.AUTO_GATEWAY: True,
            InterfaceIP.AUTO_ROUTES: True,
            InterfaceIP.AUTO_ROUTE_TABLE_ID: self._get_auto_route_table_id(),
        }

    def _create_ipv6(self, enabled):
        ipstate = {InterfaceIP.ENABLED: enabled}
        if enabled:
            if self._netconf.ipv6addr:
                ipv6_address = self._create_static_ipv6_address()
                ipstate[InterfaceIP.ADDRESS] = ipv6_address
                ipstate[InterfaceIP.DHCP] = False
                ipstate[InterfaceIPv6.AUTOCONF] = False
            elif self._netconf.dhcpv6 or self._netconf.ipv6autoconf:
                ipstate.update(self._create_dynamic_ipv6())
            else:
                ipstate[InterfaceIP.ENABLED] = False

        return ipstate

    def _create_static_ipv6_address(self):
        address, prefix = self._netconf.ipv6addr.split('/')
        return [
            {
                InterfaceIP.ADDRESS_IP: address,
                InterfaceIP.ADDRESS_PREFIX_LENGTH: int(prefix),
            }
        ]

    def _create_dynamic_ipv6(self):
        return {
            InterfaceIP.DHCP: self._netconf.dhcpv6,
            InterfaceIPv6.AUTOCONF: self._netconf.ipv6autoconf,
            InterfaceIP.AUTO_DNS: self._auto_dns,
            InterfaceIP.AUTO_GATEWAY: True,
            InterfaceIP.AUTO_ROUTES: True,
            InterfaceIP.AUTO_ROUTE_TABLE_ID: self._get_auto_route_table_id(),
        }

    def _get_auto_route_table_id(self):
        return (
            DEFAULT_TABLE_ID
            if self._netconf.default_route
            else generate_table_id(self._netconf.next_hop_interface)
        )


def _get_ipv4_prefix_from_mask(ipv4netmask):
    prefix = 0
    for octet in ipv4netmask.split('.'):
        onebits = str(bin(int(octet))).strip('0b').rstrip('0')
        prefix += len(onebits)
    return prefix
