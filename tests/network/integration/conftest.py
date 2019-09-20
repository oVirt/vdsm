# Copyright 2018 Red Hat, Inc.
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
import tempfile

import pytest

from vdsm.network import cmd
from vdsm.network import sourceroute
from vdsm.network.ip import rule as ip_rule
from vdsm.network.link.bond import sysfs_options_mapper

import network as network_tests
from network.compat import mock
from network.nettestlib import has_sysfs_bond_permission


IPV4_ADDRESS1 = '192.168.99.1'  # Tracking the address used in ip_rule_test


@pytest.fixture(scope='session', autouse=True)
def requires_root():
    if os.geteuid() != 0:
        pytest.skip('Integration tests require root')


@pytest.fixture(scope='session', autouse=True)
def bond_option_mapping():
    file1 = tempfile.NamedTemporaryFile()
    file2 = tempfile.NamedTemporaryFile()
    with file1 as f_bond_defaults, file2 as f_bond_name2numeric:

        if has_sysfs_bond_permission():
            ALTERNATIVE_BONDING_DEFAULTS = f_bond_defaults.name
            ALTERNATIVE_BONDING_NAME2NUMERIC_PATH = f_bond_name2numeric.name
        else:
            ALTERNATIVE_BONDING_DEFAULTS = os.path.join(
                os.path.dirname(network_tests.__file__),
                'static',
                'bonding-defaults.json',
            )
            ALTERNATIVE_BONDING_NAME2NUMERIC_PATH = os.path.join(
                os.path.dirname(network_tests.__file__),
                'static',
                'bonding-name2numeric.json',
            )

        patch_bonding_defaults = mock.patch(
            'vdsm.network.link.bond.sysfs_options.BONDING_DEFAULTS',
            ALTERNATIVE_BONDING_DEFAULTS,
        )
        patch_bonding_name2num = mock.patch(
            'vdsm.network.link.bond.sysfs_options_mapper.'
            'BONDING_NAME2NUMERIC_PATH',
            ALTERNATIVE_BONDING_NAME2NUMERIC_PATH,
        )

        with patch_bonding_defaults, patch_bonding_name2num:
            if has_sysfs_bond_permission():
                sysfs_options_mapper.dump_bonding_options()
            yield


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
