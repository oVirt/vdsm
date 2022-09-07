# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import
from __future__ import division

from testlib import VdsmTestCase, expandPermutations, permutations

from vdsm.common.network import address as ipaddress


@expandPermutations
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

    def test_literal_ipv4_addr(self):
        self.assertEqual('1.2.3.4',
                         ipaddress.normalize_literal_addr('1.2.3.4'))

    def test_literal_ipv6_addr(self):
        self.assertEqual('[2001::1]',
                         ipaddress.normalize_literal_addr('2001::1'))

    def test_literal_namedhost(self):
        self.assertEqual('namedhost',
                         ipaddress.normalize_literal_addr('namedhost'))

    def test_literal_ipv6_already_literal(self):
        self.assertEqual('[2001::1]',
                         ipaddress.normalize_literal_addr('[2001::1]'))

    @permutations([
        # Valid host
        ("server", "/", "server:/"),
        ("server", "/path", "server:/path"),
        ("12.34.56.78", "/path", "12.34.56.78:/path"),

        # IPv6
        ("2001:db8::60fe:5bf:febc:912", "/path",
         "[2001:db8::60fe:5bf:febc:912]:/path"),

        # Invalid host - concatenation still occurs
        ("ser:ver", "/path", "[ser:ver]:/path"),
        ("[2001:db8::60fe:5bf:febc:912]", "/path",
         "[[2001:db8::60fe:5bf:febc:912]]:/path"),
    ])
    def test_hosttail_join(self, host, tail, expected):
        self.assertEqual(expected, ipaddress.hosttail_join(host, tail))
