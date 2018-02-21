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

import logging

import six

from vdsm.network.ip import rule as ip_rule

from .compat import mock
from .nettestlib import bonding_default_fpath


IPV4_ADDRESS1 = '192.168.99.1'    # Tracking the address used in ip_rule_test

bonding_dump_patchers = []


def setup_package():
    bonding_defaults, bonding_name2numeric = bonding_default_fpath()
    bonding_dump_patchers.append(
        mock.patch('vdsm.network.link.bond.sysfs_options.BONDING_DEFAULTS',
                   bonding_defaults))
    bonding_dump_patchers.append(
        mock.patch('vdsm.network.link.bond.sysfs_options_mapper.'
                   'BONDING_NAME2NUMERIC_PATH',
                   bonding_name2numeric))

    for patcher in bonding_dump_patchers:
        patcher.start()


def teardown_package():
    for patcher in bonding_dump_patchers:
        patcher.stop()

    # TODO: Remove condition when ip.rule becomes PY3 compatible.
    if six.PY2:
        _cleanup_stale_iprules()


class StaleIPRulesError(Exception):
    pass


def _cleanup_stale_iprules():
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
