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

from testlib import VdsmTestCase

from vdsm.common.network import address as ipaddress


class TestIpAddressHostTail(VdsmTestCase):

    def test_hosttail_ipv4(self):
        self.assertEqual(('1.2.3.4', '4321'),
                         ipaddress.hosttail_split('1.2.3.4:4321'))

    def test_hosttail_ipv6(self):
        self.assertEqual(('2001::1', '4321'),
                         ipaddress.hosttail_split('[2001::1]:4321'))

    def test_hosttail_namedhost(self):
        self.assertEqual(('TestHost', '4321'),
                         ipaddress.hosttail_split('TestHost:4321'))

    def test_hosttail_hostpath(self):
        self.assertEqual(('FQDN.host', '/ovirt/rules/the/world'),
                         ipaddress.hosttail_split(
                             'FQDN.host:/ovirt/rules/the/world'))

    def test_hosttail_hostpath_with_colon_in_path(self):
        self.assertEqual(('FQDN.host', '/path/a:b:c'),
                         ipaddress.hosttail_split(
                             'FQDN.host:/path/a:b:c'))

    def test_hosttail_ipv6_with_colon_in_path(self):
        self.assertEqual(('2001::1', '/a:b:c/path'),
                         ipaddress.hosttail_split('[2001::1]:/a:b:c/path'))

    def test_hosttail_no_colon(self):
        with self.assertRaises(ipaddress.HosttailError):
            ipaddress.hosttail_split('bad hostname')

    def test_hosttail_only_host(self):
        with self.assertRaises(ipaddress.HosttailError):
            ipaddress.hosttail_split('hostname:')

    def test_hosttail_only_port(self):
        with self.assertRaises(ipaddress.HosttailError):
            ipaddress.hosttail_split(':123')

    def test_hosttail_ipv6_no_brackets_returns_garbage(self):
        self.assertNotEqual(('2001::1', '4321'),
                            ipaddress.hosttail_split('2001::1:4321'))
