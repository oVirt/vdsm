#
# Copyright 2017-2020 Red Hat, Inc.
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

from contextlib import contextmanager

import pytest

from vdsm.network.ip import rule as ip_rule
from vdsm.network.ip.rule import IPRuleAddError
from vdsm.network.ip.rule import IPRuleData
from vdsm.network.ip.rule import IPRuleDeleteError


IPV4_ADDRESS1 = '192.168.99.1'


@pytest.fixture(scope='module')
def ip_rule_driver():
    return ip_rule.driver(ip_rule.Drivers.IPROUTE2)


class TestIPRule(object):
    def test_add_delete_and_read_rule(self, ip_rule_driver):
        rule = IPRuleData(to=IPV4_ADDRESS1, iif='lo', table='main', prio=999)
        with self._create_rule(ip_rule_driver, rule):
            rules = [
                r for r in ip_rule_driver.rules() if r.to == IPV4_ADDRESS1
            ]
            assert len(rules) == 1
            assert rules[0].iif == 'lo'
            assert rules[0].table == 'main'
            assert rules[0].prio == 999

    def test_delete_non_existing_rule(self, ip_rule_driver):
        rule = IPRuleData(to=IPV4_ADDRESS1, iif='lo', table='main')
        with pytest.raises(IPRuleDeleteError):
            ip_rule_driver.delete(rule)

    def test_add_rule_with_invalid_address(self, ip_rule_driver):
        rule = IPRuleData(
            to=IPV4_ADDRESS1, iif='shrubbery_shruberry', table='main'
        )
        with pytest.raises(IPRuleAddError):
            with self._create_rule(ip_rule_driver, rule):
                pass

    @contextmanager
    def _create_rule(self, rule_driver, rule_data):
        rule_driver.add(rule_data)
        try:
            yield
        finally:
            rule_driver.delete(rule_data)
