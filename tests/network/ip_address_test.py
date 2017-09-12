# Copyright 2016 Red Hat, Inc.
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

import itertools

from nose.plugins.attrib import attr

from testValidation import broken_on_ci
from testlib import VdsmTestCase

from .nettestlib import dummy_device, dummy_devices, preserve_default_route

from vdsm.network.ip import address
from vdsm.network.netinfo import routes


DEVICE_NAME = 'foo'

IPV4_PREFIX = 29
IPV4_NETMASK = address.prefix2netmask(IPV4_PREFIX)
IPV4_A_ADDRESS = '192.168.99.1'
IPV4_A_WITH_PREFIXLEN = '{}/{}'.format(IPV4_A_ADDRESS, IPV4_PREFIX)
IPV4_B_ADDRESS = '192.168.98.1'
IPV4_B_WITH_PREFIXLEN = '{}/{}'.format(IPV4_B_ADDRESS, IPV4_PREFIX)
IPV4_INVALID_ADDRESS = '333.333.333.333'
IPV4_INVALID_WITH_PREFIXLEN = '{}/{}'.format(IPV4_INVALID_ADDRESS, IPV4_PREFIX)
IPV4_GATEWAY = '192.168.99.6'

IPV6_PREFIX = 64
IPV6_NETMASK = 'ffff:ffff:ffff:ffff::'
IPV6_A_ADDRESS = '2001:99::1'
IPV6_A_WITH_PREFIXLEN = '{}/{}'.format(IPV6_A_ADDRESS, IPV6_PREFIX)
IPV6_B_ADDRESS = '2002:99::1'
IPV6_B_WITH_PREFIXLEN = '{}/{}'.format(IPV6_B_ADDRESS, IPV6_PREFIX)
IPV6_INVALID_ADDRESS = '2001::99::1'
IPV6_INVALID_WITH_PREFIXLEN = '{}/{}'.format(IPV6_INVALID_ADDRESS, IPV6_PREFIX)
IPV6_GATEWAY = '2001:99::99'


@attr(type='unit')
class TestAddressIP(VdsmTestCase):

    def test_ipv4_clean_init(self):
        ip = address.IPv4()
        self._assert_ip_clean_init(ip)
        self.assertEqual(None, ip.bootproto)
        self.assertEqual(None, ip.netmask)

    def test_ipv6_clean_init(self):
        ip = address.IPv6()
        self._assert_ip_clean_init(ip)
        self.assertEqual(None, ip.ipv6autoconf)
        self.assertEqual(None, ip.dhcpv6)

    def _assert_ip_clean_init(self, ip):
        self.assertEqual(None, ip.address)
        self.assertEqual(None, ip.gateway)
        self.assertEqual(None, ip.defaultRoute)


@attr(type='integration')
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
        ip = address.IPv4(address=IPV4_A_ADDRESS, netmask=IPV4_NETMASK,
                          gateway=IPV4_GATEWAY, defaultRoute=True)
        with dummy_device() as nic:
            with preserve_default_route():
                address.add(nic, ipv4=ip, ipv6=None)
                self.assertTrue(routes.is_default_route(IPV4_GATEWAY))

    def test_add_ipv6_address_with_gateway(self):
        ip = address.IPv6(address=IPV6_A_WITH_PREFIXLEN, gateway=IPV6_GATEWAY,
                          defaultRoute=True)
        with dummy_device() as nic:
            with preserve_default_route():
                address.add(nic, ipv4=None, ipv6=ip)
                self.assertTrue(routes.is_ipv6_default_route(IPV6_GATEWAY))

    def test_add_ipv4_and_ipv6_address_with_gateways(self):
        ipv4 = address.IPv4(address=IPV4_A_ADDRESS, netmask=IPV4_NETMASK,
                            gateway=IPV4_GATEWAY, defaultRoute=True)
        ipv6 = address.IPv6(address=IPV6_A_WITH_PREFIXLEN,
                            gateway=IPV6_GATEWAY, defaultRoute=True)
        with dummy_device() as nic:
            with preserve_default_route():
                address.add(nic, ipv4=ipv4, ipv6=ipv6)
                addr, netmask, _, ipv6addresses = address.addrs_info(nic)
                self.assertTrue(routes.is_default_route(IPV4_GATEWAY))
                self.assertTrue(routes.is_ipv6_default_route(IPV6_GATEWAY))

    def test_add_ipv6_gateway_given_existing_ipv4_and_ipv6_gateways(self):
        ipv4 = address.IPv4(address=IPV4_A_ADDRESS, netmask=IPV4_NETMASK,
                            gateway=IPV4_GATEWAY, defaultRoute=True)
        ipv6 = address.IPv6(address=IPV6_A_WITH_PREFIXLEN,
                            gateway=IPV6_GATEWAY, defaultRoute=True)
        with dummy_device() as nic:
            with preserve_default_route():
                address.add(nic, ipv4=ipv4, ipv6=None)
                address.add(nic, ipv4=None, ipv6=ipv6)

                address.add(nic, ipv4=None, ipv6=ipv6)
                self.assertTrue(routes.is_default_route(IPV4_GATEWAY))
                self.assertTrue(routes.is_ipv6_default_route(IPV6_GATEWAY))


@attr(type='unit')
class IPAddressDataTest(VdsmTestCase):

    def test_ipv4_init(self):
        ip_data = address.IPAddressData(
            IPV4_A_WITH_PREFIXLEN,
            device=DEVICE_NAME
        )

        self.assertEqual(ip_data.device, DEVICE_NAME)
        self.assertEqual(ip_data.family, 4)
        self.assertEqual(ip_data.address, IPV4_A_ADDRESS)
        self.assertEqual(ip_data.netmask, IPV4_NETMASK)
        self.assertEqual(ip_data.prefixlen, IPV4_PREFIX)
        self.assertEqual(ip_data.address_with_prefixlen, IPV4_A_WITH_PREFIXLEN)

    def test_ipv4_init_invalid(self):
        with self.assertRaises(address.IPAddressDataError):
            address.IPAddressData(
                IPV4_INVALID_WITH_PREFIXLEN,
                device=DEVICE_NAME
            )

    def test_ipv6_init(self):
        ip_data = address.IPAddressData(
            IPV6_A_WITH_PREFIXLEN,
            device=DEVICE_NAME
        )

        self.assertEqual(ip_data.device, DEVICE_NAME)
        self.assertEqual(ip_data.family, 6)
        self.assertEqual(ip_data.address, IPV6_A_ADDRESS)
        self.assertEqual(ip_data.netmask, IPV6_NETMASK)
        self.assertEqual(ip_data.prefixlen, IPV6_PREFIX)
        self.assertEqual(ip_data.address_with_prefixlen, IPV6_A_WITH_PREFIXLEN)

    def test_ipv6_init_invalid(self):
        with self.assertRaises(address.IPAddressDataError):
            address.IPAddressData(
                IPV6_INVALID_WITH_PREFIXLEN,
                device=DEVICE_NAME
            )

    def test_ipv4_init_with_scope_and_flags(self):
        SCOPE = 'local'
        FLAGS = frozenset([address.Flags.SECONDARY, address.Flags.PERMANENT])

        ip_data = address.IPAddressData(
            IPV4_A_WITH_PREFIXLEN,
            device=DEVICE_NAME,
            scope=SCOPE,
            flags=FLAGS
        )

        self.assertEqual(ip_data.scope, SCOPE)
        self.assertEqual(ip_data.flags, FLAGS)
        self.assertFalse(ip_data.is_primary())
        self.assertTrue(ip_data.is_permanent())


@attr(type='integration')
class IPAddressTest(VdsmTestCase):
    IPAddress = address.driver(address.Drivers.IPROUTE2)

    def test_add_delete_ipv4(self):
        self._test_add_delete(IPV4_A_WITH_PREFIXLEN, IPV4_B_WITH_PREFIXLEN)

    @broken_on_ci("IPv6 not supported on travis", name="TRAVIS_CI")
    def test_add_delete_ipv6(self):
        self._test_add_delete(IPV6_A_WITH_PREFIXLEN, IPV6_B_WITH_PREFIXLEN)

    @broken_on_ci("IPv6 not supported on travis", name="TRAVIS_CI")
    def test_add_delete_ipv4_ipv6(self):
        self._test_add_delete(IPV4_A_WITH_PREFIXLEN, IPV6_B_WITH_PREFIXLEN)

    def _test_add_delete(self, ip_a, ip_b):
        with dummy_device() as nic:
            ip_a_data = address.IPAddressData(ip_a, device=nic)
            ip_b_data = address.IPAddressData(ip_b, device=nic)

            IPAddressTest.IPAddress.add(ip_a_data)
            self._assert_has_address(nic, ip_a)

            IPAddressTest.IPAddress.add(ip_b_data)
            self._assert_has_address(nic, ip_a)
            self._assert_has_address(nic, ip_b)

            IPAddressTest.IPAddress.delete(ip_b_data)
            self._assert_has_address(nic, ip_a)
            self._assert_has_no_address(nic, ip_b)

            IPAddressTest.IPAddress.delete(ip_a_data)
            self._assert_has_no_address(nic, ip_a)
            self._assert_has_no_address(nic, ip_b)

    def test_add_with_non_existing_device_ipv4(self):
        self._test_add_with_non_existing_device(IPV4_A_WITH_PREFIXLEN)

    def test_add_with_non_existing_device_ipv6(self):
        self._test_add_with_non_existing_device(IPV6_A_WITH_PREFIXLEN)

    def _test_add_with_non_existing_device(self, ip):
        with self.assertRaises(address.IPAddressAddError):
            IPAddressTest.IPAddress.add(
                address.IPAddressData(ip, device='tim the enchanter'))

    def test_delete_non_existing_ipv4(self):
        self._test_delete_non_existing_ip(IPV4_A_WITH_PREFIXLEN)

    def test_delete_non_existing_ipv6(self):
        self._test_delete_non_existing_ip(IPV6_A_WITH_PREFIXLEN)

    def _test_delete_non_existing_ip(self, ip):
        with dummy_device() as nic:
            with self.assertRaises(address.IPAddressDeleteError):
                IPAddressTest.IPAddress.delete(
                    address.IPAddressData(ip, device=nic))

    def test_list_ipv4(self):
        self._test_list(
            ipv4_addresses=[IPV4_A_WITH_PREFIXLEN, IPV4_B_WITH_PREFIXLEN],
            ipv6_addresses=[]
        )

    @broken_on_ci("IPv6 not supported on travis", name="TRAVIS_CI")
    def test_list_ipv6(self):
        self._test_list(
            ipv4_addresses=[],
            ipv6_addresses=[IPV6_A_WITH_PREFIXLEN, IPV6_B_WITH_PREFIXLEN]
        )

    @broken_on_ci("IPv6 not supported on travis", name="TRAVIS_CI")
    def test_list_ipv4_ipv6(self):
        self._test_list(
            ipv4_addresses=[IPV4_A_WITH_PREFIXLEN],
            ipv6_addresses=[IPV6_B_WITH_PREFIXLEN]
        )

    def _test_list(self, ipv4_addresses, ipv6_addresses):
        with dummy_device() as nic:
            for addr in itertools.chain.from_iterable(
                    [ipv4_addresses, ipv6_addresses]):
                IPAddressTest.IPAddress.add(
                    address.IPAddressData(addr, device=nic))

            all_addrs = list(IPAddressTest.IPAddress.addresses())
            ipv4_addrs = list(IPAddressTest.IPAddress.addresses(family=4))
            ipv6_addrs = list(IPAddressTest.IPAddress.addresses(family=6))

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
            IPAddressTest.IPAddress.add(
                address.IPAddressData(ip_a_with_device, device=nic1))
            IPAddressTest.IPAddress.add(
                address.IPAddressData(ip_b, device=nic2))

            addresses = list(IPAddressTest.IPAddress.addresses(device=nic1))
            self._assert_address_in(ip_a_with_device, addresses)
            self._assert_address_not_in(ip_b, addresses)

    def _assert_has_address(self, device, address_with_prefixlen):
        addresses = IPAddressTest.IPAddress.addresses(device)
        self._assert_address_in(address_with_prefixlen, addresses)

    def _assert_has_no_address(self, device, address_with_prefixlen):
        addresses = IPAddressTest.IPAddress.addresses(device)
        self._assert_address_not_in(address_with_prefixlen, addresses)

    def _assert_address_in(self, address_with_prefixlen, addresses):
        addresses_list = [addr.address_with_prefixlen for addr in addresses]
        self.assertIn(address_with_prefixlen, addresses_list)

    def _assert_address_not_in(self, address_with_prefixlen, addresses):
        addresses_list = [addr.address_with_prefixlen for addr in addresses]
        self.assertNotIn(address_with_prefixlen, addresses_list)
