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

from nose.plugins.attrib import attr

from testlib import VdsmTestCase

from .nettestlib import dummy_device, preserve_default_route

from vdsm.network.ip import address
from vdsm.network.netinfo import routes


IPV4_ADDRESS = '192.168.99.1'
IPV4_PREFIX = 29
IPV4_NETMASK = address.prefix2netmask(IPV4_PREFIX)
IPV4_GATEWAY = '192.168.99.6'

IPV6_ADDRESS = '2001:99::1/64'
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
        ip = address.IPv4(address=IPV4_ADDRESS, netmask=IPV4_NETMASK)
        with dummy_device() as nic:
            address.add(nic, ipv4=ip, ipv6=None)
            addr, netmask, _, _ = address.addrs_info(nic)
            self.assertEqual(IPV4_ADDRESS, addr)
            self.assertEqual(IPV4_NETMASK, netmask)

    def test_add_ipv6_address(self):
        ip = address.IPv6(address=IPV6_ADDRESS)
        with dummy_device() as nic:
            address.add(nic, ipv4=None, ipv6=ip)
            _, _, _, ipv6addresses = address.addrs_info(nic)
            self.assertEqual(IPV6_ADDRESS, ipv6addresses[0])

    def test_add_ipv4_and_ipv6_address(self):
        ipv4 = address.IPv4(address=IPV4_ADDRESS, netmask=IPV4_NETMASK)
        ipv6 = address.IPv6(address=IPV6_ADDRESS)
        with dummy_device() as nic:
            address.add(nic, ipv4=ipv4, ipv6=ipv6)
            addr, netmask, _, ipv6addresses = address.addrs_info(nic)
            self.assertEqual(IPV4_ADDRESS, addr)
            self.assertEqual(IPV4_NETMASK, netmask)
            self.assertEqual(IPV6_ADDRESS, ipv6addresses[0])

    def test_add_ipv4_address_with_gateway(self):
        ip = address.IPv4(address=IPV4_ADDRESS, netmask=IPV4_NETMASK,
                          gateway=IPV4_GATEWAY, defaultRoute=True)
        with dummy_device() as nic:
            with preserve_default_route():
                address.add(nic, ipv4=ip, ipv6=None)
                self.assertTrue(routes.is_default_route(IPV4_GATEWAY))

    def test_add_ipv6_address_with_gateway(self):
        ip = address.IPv6(address=IPV6_ADDRESS, gateway=IPV6_GATEWAY,
                          defaultRoute=True)
        with dummy_device() as nic:
            with preserve_default_route():
                address.add(nic, ipv4=None, ipv6=ip)
                self.assertTrue(routes.is_ipv6_default_route(IPV6_GATEWAY))

    def test_add_ipv4_and_ipv6_address_with_gateways(self):
        ipv4 = address.IPv4(address=IPV4_ADDRESS, netmask=IPV4_NETMASK,
                            gateway=IPV4_GATEWAY, defaultRoute=True)
        ipv6 = address.IPv6(address=IPV6_ADDRESS, gateway=IPV6_GATEWAY,
                            defaultRoute=True)
        with dummy_device() as nic:
            with preserve_default_route():
                address.add(nic, ipv4=ipv4, ipv6=ipv6)
                addr, netmask, _, ipv6addresses = address.addrs_info(nic)
                self.assertTrue(routes.is_default_route(IPV4_GATEWAY))
                self.assertTrue(routes.is_ipv6_default_route(IPV6_GATEWAY))

    def test_add_ipv6_gateway_given_existing_ipv4_and_ipv6_gateways(self):
        ipv4 = address.IPv4(address=IPV4_ADDRESS, netmask=IPV4_NETMASK,
                            gateway=IPV4_GATEWAY, defaultRoute=True)
        ipv6 = address.IPv6(address=IPV6_ADDRESS, gateway=IPV6_GATEWAY,
                            defaultRoute=True)
        with dummy_device() as nic:
            with preserve_default_route():
                address.add(nic, ipv4=ipv4, ipv6=None)
                address.add(nic, ipv4=None, ipv6=ipv6)

                address.add(nic, ipv4=None, ipv6=ipv6)
                self.assertTrue(routes.is_default_route(IPV4_GATEWAY))
                self.assertTrue(routes.is_ipv6_default_route(IPV6_GATEWAY))
