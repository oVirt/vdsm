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

import itertools
import os

import pytest

from testlib import VdsmTestCase

from ..nettestlib import dummy_device, dummy_devices, preserve_default_route
from ..nettestlib import nm_is_running

from vdsm.network.ip import address
from vdsm.network.netinfo import routes


IPV4_PREFIX = 29
IPV4_NETMASK = address.prefix2netmask(IPV4_PREFIX)
IPV4_A_ADDRESS = '192.168.99.1'
IPV4_A_WITH_PREFIXLEN = '{}/{}'.format(IPV4_A_ADDRESS, IPV4_PREFIX)
IPV4_B_ADDRESS = '192.168.98.1'
IPV4_B_WITH_PREFIXLEN = '{}/{}'.format(IPV4_B_ADDRESS, IPV4_PREFIX)
IPV4_GATEWAY = '192.168.99.6'

IPV6_PREFIX = 64
IPV6_A_ADDRESS = '2001:99::1'
IPV6_A_WITH_PREFIXLEN = '{}/{}'.format(IPV6_A_ADDRESS, IPV6_PREFIX)
IPV6_B_ADDRESS = '2002:99::1'
IPV6_B_WITH_PREFIXLEN = '{}/{}'.format(IPV6_B_ADDRESS, IPV6_PREFIX)
IPV6_GATEWAY = '2001:99::99'


ipv6_broken_on_travis_ci = pytest.mark.skipif(
    'TRAVIS_CI' in os.environ, reason='IPv6 not supported on travis'
)


class TestAddressSetup(VdsmTestCase):
    def test_add_ipv4_address(self):
        ip = address.IPv4(address=IPV4_A_ADDRESS, netmask=IPV4_NETMASK)
        with dummy_device() as nic:
            address.add(nic, ipv4=ip, ipv6=None)
            addr, netmask, _, _ = address.addrs_info(nic)
            self.assertEqual(IPV4_A_ADDRESS, addr)
            self.assertEqual(IPV4_NETMASK, netmask)

    def test_add_ipv6_address(self):
        ip = address.IPv6(address=IPV6_A_WITH_PREFIXLEN)
        with dummy_device() as nic:
            address.add(nic, ipv4=None, ipv6=ip)
            _, _, _, ipv6addresses = address.addrs_info(nic)
            self.assertEqual(IPV6_A_WITH_PREFIXLEN, ipv6addresses[0])

    def test_add_ipv4_and_ipv6_address(self):
        ipv4 = address.IPv4(address=IPV4_A_ADDRESS, netmask=IPV4_NETMASK)
        ipv6 = address.IPv6(address=IPV6_A_WITH_PREFIXLEN)
        with dummy_device() as nic:
            address.add(nic, ipv4=ipv4, ipv6=ipv6)
            addr, netmask, _, ipv6addresses = address.addrs_info(nic)
            self.assertEqual(IPV4_A_ADDRESS, addr)
            self.assertEqual(IPV4_NETMASK, netmask)
            self.assertEqual(IPV6_A_WITH_PREFIXLEN, ipv6addresses[0])

    def test_add_ipv4_address_with_gateway(self):
        ip = address.IPv4(
            address=IPV4_A_ADDRESS,
            netmask=IPV4_NETMASK,
            gateway=IPV4_GATEWAY,
            defaultRoute=True,
        )
        with dummy_device() as nic:
            with preserve_default_route():
                address.add(nic, ipv4=ip, ipv6=None)
                self.assertTrue(
                    routes.is_default_route(IPV4_GATEWAY, routes.get_routes())
                )

    def test_add_ipv6_address_with_gateway(self):
        ip = address.IPv6(
            address=IPV6_A_WITH_PREFIXLEN,
            gateway=IPV6_GATEWAY,
            defaultRoute=True,
        )
        with dummy_device() as nic:
            with preserve_default_route():
                address.add(nic, ipv4=None, ipv6=ip)
                self.assertTrue(routes.is_ipv6_default_route(IPV6_GATEWAY))

    def test_add_ipv4_and_ipv6_address_with_gateways(self):
        ipv4 = address.IPv4(
            address=IPV4_A_ADDRESS,
            netmask=IPV4_NETMASK,
            gateway=IPV4_GATEWAY,
            defaultRoute=True,
        )
        ipv6 = address.IPv6(
            address=IPV6_A_WITH_PREFIXLEN,
            gateway=IPV6_GATEWAY,
            defaultRoute=True,
        )
        with dummy_device() as nic:
            with preserve_default_route():
                address.add(nic, ipv4=ipv4, ipv6=ipv6)
                addr, netmask, _, ipv6addresses = address.addrs_info(nic)
                self.assertTrue(
                    routes.is_default_route(IPV4_GATEWAY, routes.get_routes())
                )
                self.assertTrue(routes.is_ipv6_default_route(IPV6_GATEWAY))

    def test_add_ipv6_gateway_given_existing_ipv4_and_ipv6_gateways(self):
        ipv4 = address.IPv4(
            address=IPV4_A_ADDRESS,
            netmask=IPV4_NETMASK,
            gateway=IPV4_GATEWAY,
            defaultRoute=True,
        )
        ipv6 = address.IPv6(
            address=IPV6_A_WITH_PREFIXLEN,
            gateway=IPV6_GATEWAY,
            defaultRoute=True,
        )
        with dummy_device() as nic:
            with preserve_default_route():
                address.add(nic, ipv4=ipv4, ipv6=None)
                address.add(nic, ipv4=None, ipv6=ipv6)

                address.add(nic, ipv4=None, ipv6=ipv6)
                self.assertTrue(
                    routes.is_default_route(IPV4_GATEWAY, routes.get_routes())
                )
                self.assertTrue(routes.is_ipv6_default_route(IPV6_GATEWAY))


class TestIPAddress(VdsmTestCase):
    IPAddress = address.driver(address.Drivers.IPROUTE2)

    def test_add_delete_ipv4(self):
        self._test_add_delete(IPV4_A_WITH_PREFIXLEN, IPV4_B_WITH_PREFIXLEN)

    @ipv6_broken_on_travis_ci
    def test_add_delete_ipv6(self):
        self._test_add_delete(IPV6_A_WITH_PREFIXLEN, IPV6_B_WITH_PREFIXLEN)

    @ipv6_broken_on_travis_ci
    @pytest.mark.skipif(
        nm_is_running(),
        reason='Fails randomly when NM is running. See BZ#1512316',
    )
    def test_add_delete_ipv4_ipv6(self):
        self._test_add_delete(IPV4_A_WITH_PREFIXLEN, IPV6_B_WITH_PREFIXLEN)

    def _test_add_delete(self, ip_a, ip_b):
        with dummy_device() as nic:
            ip_a_data = address.IPAddressData(ip_a, device=nic)
            ip_b_data = address.IPAddressData(ip_b, device=nic)

            TestIPAddress.IPAddress.add(ip_a_data)
            self._assert_has_address(nic, ip_a)

            TestIPAddress.IPAddress.add(ip_b_data)
            self._assert_has_address(nic, ip_a)
            self._assert_has_address(nic, ip_b)

            TestIPAddress.IPAddress.delete(ip_b_data)
            self._assert_has_address(nic, ip_a)
            self._assert_has_no_address(nic, ip_b)

            TestIPAddress.IPAddress.delete(ip_a_data)
            self._assert_has_no_address(nic, ip_a)
            self._assert_has_no_address(nic, ip_b)

    def test_add_with_non_existing_device_ipv4(self):
        self._test_add_with_non_existing_device(IPV4_A_WITH_PREFIXLEN)

    def test_add_with_non_existing_device_ipv6(self):
        self._test_add_with_non_existing_device(IPV6_A_WITH_PREFIXLEN)

    def _test_add_with_non_existing_device(self, ip):
        with self.assertRaises(address.IPAddressAddError):
            TestIPAddress.IPAddress.add(
                address.IPAddressData(ip, device='tim the enchanter')
            )

    def test_delete_non_existing_ipv4(self):
        self._test_delete_non_existing_ip(IPV4_A_WITH_PREFIXLEN)

    def test_delete_non_existing_ipv6(self):
        self._test_delete_non_existing_ip(IPV6_A_WITH_PREFIXLEN)

    def _test_delete_non_existing_ip(self, ip):
        with dummy_device() as nic:
            with self.assertRaises(address.IPAddressDeleteError):
                TestIPAddress.IPAddress.delete(
                    address.IPAddressData(ip, device=nic)
                )

    def test_list_ipv4(self):
        self._test_list(
            ipv4_addresses=[IPV4_A_WITH_PREFIXLEN, IPV4_B_WITH_PREFIXLEN],
            ipv6_addresses=[],
        )

    @ipv6_broken_on_travis_ci
    def test_list_ipv6(self):
        self._test_list(
            ipv4_addresses=[],
            ipv6_addresses=[IPV6_A_WITH_PREFIXLEN, IPV6_B_WITH_PREFIXLEN],
        )

    @ipv6_broken_on_travis_ci
    def test_list_ipv4_ipv6(self):
        self._test_list(
            ipv4_addresses=[IPV4_A_WITH_PREFIXLEN],
            ipv6_addresses=[IPV6_B_WITH_PREFIXLEN],
        )

    def _test_list(self, ipv4_addresses, ipv6_addresses):
        with dummy_device() as nic:
            for addr in itertools.chain.from_iterable(
                [ipv4_addresses, ipv6_addresses]
            ):
                TestIPAddress.IPAddress.add(
                    address.IPAddressData(addr, device=nic)
                )

            all_addrs = list(TestIPAddress.IPAddress.addresses())
            ipv4_addrs = list(TestIPAddress.IPAddress.addresses(family=4))
            ipv6_addrs = list(TestIPAddress.IPAddress.addresses(family=6))

        for addr in ipv4_addresses:
            self._assert_address_in(addr, all_addrs)
            self._assert_address_in(addr, ipv4_addrs)

        for addr in ipv6_addresses:
            self._assert_address_in(addr, all_addrs)
            self._assert_address_in(addr, ipv6_addrs)

    def _test_list_by_device_ipv4_ipv4(self):
        self._test_list_by_device(IPV4_A_WITH_PREFIXLEN, IPV4_B_WITH_PREFIXLEN)

    def _test_list_by_device_ipv4_ipv6(self):
        self._test_list_by_device(IPV4_A_WITH_PREFIXLEN, IPV6_B_WITH_PREFIXLEN)

    def _test_list_by_device_ipv6_ipv4(self):
        self._test_list_by_device(IPV6_A_WITH_PREFIXLEN, IPV4_B_WITH_PREFIXLEN)

    def _test_list_by_device_ipv6_ipv6(self):
        self._test_list_by_device(IPV6_A_WITH_PREFIXLEN, IPV6_B_WITH_PREFIXLEN)

    def _test_list_by_device(self, ip_a_with_device, ip_b):
        with dummy_devices(2) as (nic1, nic2):
            TestIPAddress.IPAddress.add(
                address.IPAddressData(ip_a_with_device, device=nic1)
            )
            TestIPAddress.IPAddress.add(
                address.IPAddressData(ip_b, device=nic2)
            )

            addresses = list(TestIPAddress.IPAddress.addresses(device=nic1))
            self._assert_address_in(ip_a_with_device, addresses)
            self._assert_address_not_in(ip_b, addresses)

    def _assert_has_address(self, device, address_with_prefixlen):
        addresses = TestIPAddress.IPAddress.addresses(device)
        self._assert_address_in(address_with_prefixlen, addresses)

    def _assert_has_no_address(self, device, address_with_prefixlen):
        addresses = TestIPAddress.IPAddress.addresses(device)
        self._assert_address_not_in(address_with_prefixlen, addresses)

    def _assert_address_in(self, address_with_prefixlen, addresses):
        addresses_list = [addr.address_with_prefixlen for addr in addresses]
        self.assertIn(address_with_prefixlen, addresses_list)

    def _assert_address_not_in(self, address_with_prefixlen, addresses):
        addresses_list = [addr.address_with_prefixlen for addr in addresses]
        self.assertNotIn(address_with_prefixlen, addresses_list)
