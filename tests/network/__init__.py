#
# Copyright 2016-2018 Red Hat, Inc.
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

import errno
import logging
import os

import six

from vdsm.network import cmd
from vdsm.network import sourceroute
from vdsm.network.ip import rule as ip_rule
from vdsm.network.link.bond import sysfs_options_mapper

from .compat import mock
from .nettestlib import has_sysfs_bond_permission


IPV4_ADDRESS1 = '192.168.99.1'    # Tracking the address used in ip_rule_test

ALTERNATIVE_BONDING_DEFAULTS = os.path.join(
    os.path.dirname(__file__), 'static', 'bonding-defaults.json')

ALTERNATIVE_BONDING_NAME2NUMERIC_PATH = os.path.join(
    os.path.dirname(__file__), 'static', 'bonding-name2numeric.json')

bonding_dump_patchers = []


def setup_package():
    bonding_dump_patchers.append(
        mock.patch('vdsm.network.link.bond.sysfs_options.BONDING_DEFAULTS',
                   ALTERNATIVE_BONDING_DEFAULTS))
    bonding_dump_patchers.append(
        mock.patch('vdsm.network.link.bond.sysfs_options_mapper.'
                   'BONDING_NAME2NUMERIC_PATH',
                   ALTERNATIVE_BONDING_NAME2NUMERIC_PATH))

    for patcher in bonding_dump_patchers:
        patcher.start()

    if has_sysfs_bond_permission():
        try:
            sysfs_options_mapper.dump_bonding_options()
        except EnvironmentError as e:
            if e.errno != errno.ENOENT:
                raise

    _pre_cleanup_stale_iprules()


def teardown_package():
    for patcher in bonding_dump_patchers:
        patcher.stop()

    # TODO: Remove condition when ip.rule becomes PY3 compatible.
    if six.PY2:
        _post_cleanup_stale_iprules()


class StaleIPRulesError(Exception):
    pass


def _pre_cleanup_stale_iprules():
    commands = [
        'bash',
        '-c',
        'while ip rule delete prio {} 2>/dev/null; do true; done'.format(
            sourceroute.RULE_PRIORITY)
    ]
    cmd.exec_sync(commands)


def _post_cleanup_stale_iprules():
    """
    Clean test ip rules that may have been left by the test run.
    They may exists on the system due to some buggy test that ran
    and has not properly cleaned after itself.
    In case any stale entries have been detected, attempt to clean everything
    and raise an error.
    """
    IPRule = ip_rule.driver(ip_rule.Drivers.IPROUTE2)
    rules = [r for r in IPRule.rules() if r.to == IPV4_ADDRESS1]
    if rules:
        for rule in rules:
            try:
                IPRule.delete(rule)
                logging.warning('Rule (%s) has been removed', rule)
            except Exception as e:
                logging.error('Error removing rule (%s): %s', rule, e)
        raise StaleIPRulesError()
