# Copyright 2013-2019 Red Hat, Inc.
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
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA
# 02110-1301  USA
#
# Refer to the README and COPYING files for full details of the license
#

from __future__ import absolute_import
from __future__ import division

import six

from vdsm.network.ipwrapper import Route
from vdsm.network.ipwrapper import Rule

from testlib import VdsmTestCase as TestCaseBase


class TestIpwrapper(TestCaseBase):
    def testRouteFromText(self):
        _getRouteAttrs = lambda x: (x.network, x.via, x.device, x.table)
        good_routes = {
            'default via 192.168.99.254 dev eth0': (
                '0.0.0.0/0',
                '192.168.99.254',
                'eth0',
                None,
            ),
            'default via 192.168.99.254 dev eth0 table foo': (
                '0.0.0.0/0',
                '192.168.99.254',
                'eth0',
                'foo',
            ),
            '200.100.50.0/16 via 11.11.11.11 dev eth2 table foo': (
                '200.100.50.0/16',
                '11.11.11.11',
                'eth2',
                'foo',
            ),
            'local 127.0.0.1 dev lo  src 127.0.0.1': (
                '127.0.0.1',
                None,
                'lo',
                None,
            ),
            'unreachable ::ffff:0.0.0.0/96 dev lo  metric 1024  error -101': (
                '::ffff:0.0.0.0/96',
                None,
                'lo',
                None,
            ),
            'broadcast 240.0.0.255 dev veth_23  table local  '
            'proto kernel  scope link  src 240.0.0.1': (
                '240.0.0.255',
                None,
                'veth_23',
                'local',
            ),
            'ff02::2 dev veth_23  metric 0 \    cache': (
                'ff02::2',
                None,
                'veth_23',
                None,
            ),
        }

        for text, attributes in six.viewitems(good_routes):
            route = Route.fromText(text)
            self.assertEqual(_getRouteAttrs(route), attributes)

        bad_routes = [
            'default via 192.168.99.257 dev eth0 table foo',  # Misformed via
            '200.100.50.0/16 dev eth2 table foo extra',  # Key without value
            '288.1.2.9/43 via 1.1.9.4 dev em1 table foo',  # Misformed network
            '200.100.50.0/16 via 192.168.99.254 table foo',  # No device
            'local dev eth0 table bar',
        ]  # local with no address
        for text in bad_routes:
            self.assertRaises(ValueError, Route.fromText, text)

    def testRuleFromText(self):
        _getRuleAttrs = lambda x: (
            x.table,
            x.source,
            x.destination,
            x.srcDevice,
            x.detached,
            x.prio,
        )
        good_rules = {
            '1:    from all lookup main': ('main', None, None, None, False, 1),
            '2:    from 10.0.0.0/8 to 20.0.0.0/8 lookup table_100': (
                'table_100',
                '10.0.0.0/8',
                '20.0.0.0/8',
                None,
                False,
                2,
            ),
            '3:    from all to 8.8.8.8 lookup table_200': (
                'table_200',
                None,
                '8.8.8.8',
                None,
                False,
                3,
            ),
            '4:    from all to 5.0.0.0/8 iif dummy0 [detached] lookup 500': (
                '500',
                None,
                '5.0.0.0/8',
                'dummy0',
                True,
                4,
            ),
            '5:    from all to 5.0.0.0/8 dev dummy0 lookup 500': (
                '500',
                None,
                '5.0.0.0/8',
                'dummy0',
                False,
                5,
            ),
        }
        for text, attributes in six.viewitems(good_rules):
            rule = Rule.fromText(text)
            self.assertEqual(_getRuleAttrs(rule), attributes)

        bad_rules = [
            '32766:    from all lookup main foo',
            '2766:    lookup main',
            '276:    from 8.8.8.8'
            '32:    from 10.0.0.0/8 to 264.0.0.0/8 lookup table_100',
        ]
        for text in bad_rules:
            self.assertRaises(ValueError, Rule.fromText, text)
