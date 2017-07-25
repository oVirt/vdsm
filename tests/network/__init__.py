#
# Copyright 2016-2017 Red Hat, Inc.
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

from testlib import mock

from .nettestlib import bonding_default_fpath
from . ip_rule_test import IPV4_ADDRESS1, IPRuleTest


bonding_defaults_patcher = None


def setup_package():
    global bonding_defaults_patcher
    bonding_defaults_patcher = mock.patch(
        'vdsm.network.link.bond.sysfs_options.BONDING_DEFAULTS',
        bonding_default_fpath())
    bonding_defaults_patcher.start()


def teardown_package():
    bonding_defaults_patcher.stop()

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
    rules = [r for r in IPRuleTest.IPRule.rules() if r.to == IPV4_ADDRESS1]
    if rules:
        for rule in rules:
            try:
                IPRuleTest.IPRule.delete(rule)
                logging.warning('Rule (%s) has been removed', rule)
            except Exception as e:
                logging.error('Error removing rule (%s): %s', rule, e)
        raise StaleIPRulesError()
