# Copyright 2013 Red Hat, Inc.
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

from vdsm.ipwrapper import Route
from vdsm.ipwrapper import Rule

from testrunner import VdsmTestCase as TestCaseBase


class TestIpwrapper(TestCaseBase):
    def testRouteFromText(self):
        _getRouteAttrs = lambda x: (x.network, x.ipaddr, x.device, x.table)
        good_routes = {
            'default via 192.168.99.254 dev eth0':
            ('0.0.0.0/0', '192.168.99.254', 'eth0', None),
            'default via 192.168.99.254 dev eth0 table foo':
            ('0.0.0.0/0', '192.168.99.254', 'eth0', 'foo'),
            '200.100.50.0/16 via 11.11.11.11 dev eth2 table foo':
            ('200.100.50.0/16', '11.11.11.11', 'eth2', 'foo')}
        for text, attributes in good_routes.iteritems():
            route = Route.fromText(text)
            self.assertEqual(_getRouteAttrs(route), attributes)

        bad_routes = ['default via 192.168.99.257 dev eth0 table foo',
                      '200.100.50.0/16 dev eth2 table foo',
                      '288.100.23.9/43 via 192.168.99.254 dev eth0 table foo',
                      '200.100.50.0/16 via 192.168.99.254 table foo']
        for text in bad_routes:
            self.assertRaises(ValueError, Route.fromText, text)

    def testRuleFromText(self):
        _getRuleAttrs = lambda x: (x.table, x.source, x.destination)
        good_rules = {
            '32766:    from all lookup main':
            ('main', None, None),
            '32767:    from 10.0.0.0/8 to 20.0.0.0/8 lookup table_100':
            ('table_100', '10.0.0.0/8', '20.0.0.0/8'),
            '32768:    from all to 8.8.8.8 lookup table_200':
            ('table_200', None, '8.8.8.8')}
        for text, attributes in good_rules.iteritems():
            rule = Rule.fromText(text)
            self.assertEqual(_getRuleAttrs(rule), attributes)

        bad_rules = ['32766:    from all lookup main foo',
                     '2766:    lookup main',
                     '276:    from 8.8.8.8'
                     '32:    from 10.0.0.0/8 to 264.0.0.0/8 lookup table_100']
        for text in bad_rules:
            self.assertRaises(ValueError, Rule.fromText, text)
