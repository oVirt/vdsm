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

from vdsm.network import errors as ne
from vdsm.network.ip import address
from vdsm.network.ip import validator


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


@attr(type='unit')
class TestIPValidator(VdsmTestCase):

    def test_ignore_remove_networks(self):
        validator.validate({'NET0': {'remove': True,
                                     'defaultRoute': False,
                                     'nameservers': ['8.8.8.8']}})

    def test_nameserver_defined_on_a_non_primary_network_fails(self):
        with self.assertRaises(ne.ConfigNetworkError) as cne:
            validator.validate({'NET0': {'defaultRoute': False,
                                         'nameservers': ['8.8.8.8']}})
        self.assertEqual(cne.exception.errCode, ne.ERR_BAD_PARAMS)

    def test_nameserver_faulty_ipv4_address(self):
        with self.assertRaises(ne.ConfigNetworkError) as cne:
            validator.validate({'NET0': {'defaultRoute': True,
                                         'nameservers': ['a.8.8.8']}})
        self.assertEqual(cne.exception.errCode, ne.ERR_BAD_ADDR)

    def test_nameserver_faulty_ipv6_address(self):
        with self.assertRaises(ne.ConfigNetworkError) as cne:
            validator.validate({'NET0': {'defaultRoute': True,
                                         'nameservers': ['2001:bla::1']}})
        self.assertEqual(cne.exception.errCode, ne.ERR_BAD_ADDR)

    def test_nameserver_valid_ipv4_address(self):
        validator.validate({'NET0': {'defaultRoute': True,
                                     'nameservers': ['8.8.8.8']}})

    def test_nameserver_valid_ipv6_address(self):
        validator.validate({'NET0': {'defaultRoute': True,
                                     'nameservers': ['2001::1']}})

    def test_nameserver_address_with_zone_identifier(self):
        validator.validate({'NET0': {'defaultRoute': True,
                                     'nameservers': ['fe80::1%eth1']}})
