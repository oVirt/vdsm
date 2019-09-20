#
# Copyright 2017-2018 Red Hat, Inc.
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
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA  02110-1301 USA
#
# Refer to the README and COPYING files for full details of the license
#

from __future__ import absolute_import
from __future__ import division

from contextlib import contextmanager

import unittest

from vdsm.network.ip import rule as ip_rule
from vdsm.network.ip.rule import IPRuleData
from vdsm.network.ip.rule import IPRuleAddError, IPRuleDeleteError

IPV4_ADDRESS1 = '192.168.99.1'


class IPRuleTest(unittest.TestCase):
    IPRule = ip_rule.driver(ip_rule.Drivers.IPROUTE2)

    def test_add_delete_and_read_rule(self):
        rule = IPRuleData(to=IPV4_ADDRESS1, iif='lo', table='main', prio=999)
        with self.create_rule(rule):
            rules = [
                r for r in IPRuleTest.IPRule.rules() if r.to == IPV4_ADDRESS1
            ]
            self.assertEqual(1, len(rules))
            self.assertEqual(rules[0].iif, 'lo')
            self.assertEqual(rules[0].table, 'main')
            self.assertEqual(rules[0].prio, 999)

    def test_delete_non_existing_rule(self):
        rule = IPRuleData(to=IPV4_ADDRESS1, iif='lo', table='main')
        with self.assertRaises(IPRuleDeleteError):
            IPRuleTest.IPRule.delete(rule)

    def test_add_rule_with_invalid_address(self):
        rule = IPRuleData(
            to=IPV4_ADDRESS1, iif='shrubbery_shruberry', table='main'
        )
        with self.assertRaises(IPRuleAddError):
            with self.create_rule(rule):
                pass

    @contextmanager
    def create_rule(self, rule_data):
        IPRuleTest.IPRule.add(rule_data)
        try:
            yield
        finally:
            IPRuleTest.IPRule.delete(rule_data)
