# Copyright 2018-2019 Red Hat, Inc.
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

from __future__ import absolute_import
from __future__ import division

import logging
import os

import pytest

from vdsm.network import cmd
from vdsm.network import sourceroute
from vdsm.network.ip import rule as ip_rule

IPV4_ADDRESS1 = '192.168.99.1'  # Tracking the address used in ip_rule_test


@pytest.fixture(scope='session', autouse=True)
def requires_root():
    if os.geteuid() != 0:
        pytest.skip('Integration tests require root')


@pytest.fixture(scope='session', autouse=True)
def _bond_option_mapping(bond_option_mapping):
    return


class StaleIPRulesError(Exception):
    pass


@pytest.fixture(scope='session', autouse=True)
def cleanup_stale_iprules():
    """
    Clean test ip rules that may have been left by the test run.
    They may exists on the system due to some buggy test that ran
    and has not properly cleaned after itself.
    In case any stale entries have been detected, attempt to clean everything
    and raise an error.
    """
    commands = [
        'bash',
        '-c',
        'while ip rule delete prio {} 2>/dev/null; do true; done'.format(
            sourceroute.RULE_PRIORITY
        ),
    ]
    cmd.exec_sync(commands)

    yield

    IPRule = ip_rule.driver(ip_rule.Drivers.IPROUTE2)
    rules = [
        r
        for r in IPRule.rules()
        if r.to == IPV4_ADDRESS1 or r.prio == sourceroute.RULE_PRIORITY
    ]
    if rules:
        for rule in rules:
            try:
                IPRule.delete(rule)
                logging.warning('Rule (%s) has been removed', rule)
            except Exception as e:
                logging.error('Error removing rule (%s): %s', rule, e)
        raise StaleIPRulesError()
