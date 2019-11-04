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

import pytest

from vdsm.network.ip import address


DEVICE_NAME = 'foo'

IPV4_PREFIX = 29
IPV4_NETMASK = address.prefix2netmask(IPV4_PREFIX)
IPV4_A_ADDRESS = '192.168.99.1'
IPV4_A_WITH_PREFIXLEN = '{}/{}'.format(IPV4_A_ADDRESS, IPV4_PREFIX)
IPV4_INVALID_ADDRESS = '333.333.333.333'
IPV4_INVALID_WITH_PREFIXLEN = '{}/{}'.format(IPV4_INVALID_ADDRESS, IPV4_PREFIX)

IPV6_PREFIX = 64
IPV6_NETMASK = 'ffff:ffff:ffff:ffff::'
IPV6_A_ADDRESS = '2001:99::1'
IPV6_A_WITH_PREFIXLEN = '{}/{}'.format(IPV6_A_ADDRESS, IPV6_PREFIX)
IPV6_INVALID_ADDRESS = '2001::99::1'
IPV6_INVALID_WITH_PREFIXLEN = '{}/{}'.format(IPV6_INVALID_ADDRESS, IPV6_PREFIX)


class TestAddressIP(object):
    def test_ipv4_clean_init(self):
        ip = address.IPv4()
        assert not ip
        self._assert_ip_clean_init(ip)
        assert ip.bootproto is None
        assert ip.netmask is None

    def test_ipv6_clean_init(self):
        ip = address.IPv6()
        assert not ip
        self._assert_ip_clean_init(ip)
        assert ip.ipv6autoconf is None
        assert ip.dhcpv6 is None

    def _assert_ip_clean_init(self, ip):
        assert ip.address is None
        assert ip.gateway is None
        assert ip.defaultRoute is None


class TestIPAddressData(object):
    def test_ipv4_init(self):
        ip_data = address.IPAddressData(
            IPV4_A_WITH_PREFIXLEN, device=DEVICE_NAME
        )

        assert ip_data.device == DEVICE_NAME
        assert ip_data.family == 4
        assert ip_data.address == IPV4_A_ADDRESS
        assert ip_data.netmask == IPV4_NETMASK
        assert ip_data.prefixlen == IPV4_PREFIX
        assert ip_data.address_with_prefixlen == IPV4_A_WITH_PREFIXLEN

    def test_ipv4_init_invalid(self):
        with pytest.raises(address.IPAddressDataError):
            address.IPAddressData(
                IPV4_INVALID_WITH_PREFIXLEN, device=DEVICE_NAME
            )

    def test_ipv6_init(self):
        ip_data = address.IPAddressData(
            IPV6_A_WITH_PREFIXLEN, device=DEVICE_NAME
        )

        assert ip_data.device == DEVICE_NAME
        assert ip_data.family == 6
        assert ip_data.address == IPV6_A_ADDRESS
        assert ip_data.netmask == IPV6_NETMASK
        assert ip_data.prefixlen == IPV6_PREFIX
        assert ip_data.address_with_prefixlen == IPV6_A_WITH_PREFIXLEN

    def test_ipv6_init_invalid(self):
        with pytest.raises(address.IPAddressDataError):
            address.IPAddressData(
                IPV6_INVALID_WITH_PREFIXLEN, device=DEVICE_NAME
            )

    def test_ipv4_init_with_scope_and_flags(self):
        SCOPE = 'local'
        FLAGS = frozenset([address.Flags.SECONDARY, address.Flags.PERMANENT])

        ip_data = address.IPAddressData(
            IPV4_A_WITH_PREFIXLEN, device=DEVICE_NAME, scope=SCOPE, flags=FLAGS
        )

        assert ip_data.scope == SCOPE
        assert ip_data.flags == FLAGS
        assert not ip_data.is_primary()
        assert ip_data.is_permanent()
