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
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA
#
# Refer to the README and COPYING files for full details of the license

from __future__ import absolute_import
from __future__ import division

import contextlib
import logging
import sys

import six

from vdsm.network.ipwrapper import IPRoute2Error
from vdsm.network.ipwrapper import Rule
from vdsm.network.ipwrapper import ruleAdd
from vdsm.network.ipwrapper import ruleDel
from vdsm.network.ipwrapper import ruleList

from . import IPRuleApi, IPRuleData, IPRuleAddError, IPRuleDeleteError


class IPRule(IPRuleApi):
    @staticmethod
    def add(rule_data):
        r = rule_data
        with _translate_iproute2_exception(IPRuleAddError, rule_data):
            ruleAdd(Rule(r.table, r.src, r.to, r.iif, prio=r.prio))

    @staticmethod
    def delete(rule_data):
        r = rule_data
        with _translate_iproute2_exception(IPRuleDeleteError, rule_data):
            ruleDel(Rule(r.table, r.src, r.to, r.iif, prio=r.prio))

    @staticmethod
    def rules():
        rules_data = ruleList()
        for rule_data in rules_data:
            try:
                r = Rule.fromText(rule_data)
                yield IPRuleData(
                    r.destination, r.source, r.srcDevice, r.table, r.prio
                )
            except ValueError:
                logging.warning('Could not parse rule %s', rule_data)


@contextlib.contextmanager
def _translate_iproute2_exception(new_exception, rule_data):
    try:
        yield
    except IPRoute2Error:
        _, value, tb = sys.exc_info()
        error_message = value.args[1][0]
        six.reraise(
            new_exception, new_exception(str(rule_data), error_message), tb
        )
