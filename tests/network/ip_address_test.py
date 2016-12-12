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

from vdsm.network.ip import address


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
