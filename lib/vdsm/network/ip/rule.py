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
#
from __future__ import absolute_import

import abc
from functools import wraps
import logging
import sys

import six

from vdsm.network.ipwrapper import IPRoute2Error
from vdsm.network.ipwrapper import Rule
from vdsm.network.ipwrapper import ruleAdd
from vdsm.network.ipwrapper import ruleDel
from vdsm.network.ipwrapper import ruleList


@six.add_metaclass(abc.ABCMeta)
class IPRuleApi(object):

    @staticmethod
    def add(rule_data):
        """ Adding a rule entry described by an IPRuleData data object """
        raise NotImplementedError

    @staticmethod
    def delete(rule_data):
        """ Delete a rule entry described by an IPRuleData data object """
        raise NotImplementedError

    @staticmethod
    def rules(table='all'):
        raise NotImplementedError


class IPRuleData(object):
    """ A data structure used to keep rule information """

    def __init__(self, to=None, src=None, iif=None, table=None):
        self._to = to
        self._src = src
        self._iif = iif
        self._table = table

    @property
    def to(self):
        return self._to

    @property
    def src(self):
        return self._src

    @property
    def iif(self):
        return self._iif

    @property
    def table(self):
        return self._table


class IPRuleError(Exception):
    pass


def _translate_iproute2_exceptions(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        try:
            func(*args, **kwargs)
        except IPRoute2Error:
            tp, value, tb = sys.exc_info()
            six.reraise(IPRuleError, value, tb)

    return wrapper


class _Iproute2Rule(IPRuleApi):

    @staticmethod
    @_translate_iproute2_exceptions
    def add(rule_data):
        r = rule_data
        ruleAdd(Rule(r.table, r.src, r.to, r.iif))

    @staticmethod
    @_translate_iproute2_exceptions
    def delete(rule_data):
        r = rule_data
        ruleDel(Rule(r.table, r.src, r.to, r.iif))

    @staticmethod
    def rules():
        rules_data = ruleList()
        for rule_data in rules_data:
            try:
                r = Rule.fromText(rule_data)
                yield IPRuleData(r.destination, r.source, r.srcDevice, r.table)
            except ValueError:
                logging.warning('Could not parse rule %s', rule_data)


IPRule = _Iproute2Rule
