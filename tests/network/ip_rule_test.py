#
# Copyright 2017 Red Hat, Inc.
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

from contextlib import contextmanager

from nose.plugins.attrib import attr

from testlib import VdsmTestCase

from vdsm.network.ip.rule import IPRule, IPRuleData


IPV4_ADDRESS1 = '192.168.99.1'


@attr(type='integration')
class TestIpRule(VdsmTestCase):

    def test_add_delete_and_read_rule(self):
        rule = IPRuleData(to=IPV4_ADDRESS1, iif='lo', table='main')
        with create_rule(rule):
            rules = [r for r in IPRule.rules() if r.to == IPV4_ADDRESS1]
            self.assertEqual(1, len(rules))
            self.assertEqual(rules[0].iif, 'lo')
            self.assertEqual(rules[0].table, 'main')


@contextmanager
def create_rule(rule_data):
    IPRule.add(rule_data)
    try:
        yield
    finally:
        IPRule.delete(rule_data)
