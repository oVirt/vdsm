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

import abc

import six

from vdsm.network import driverloader


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

    def __repr__(self):
        return 'to={} src={} iif={} table={}'.format(
            self.to, self.src, self.iif, self.table)


class IPRuleError(Exception):
    pass


class IPRuleAddError(IPRuleError):
    pass


class IPRuleDeleteError(IPRuleError):
    pass


class Drivers(object):
    IPROUTE2 = 'iproute2'


def driver(driver_name):
    _drivers = driverloader.load_drivers('IPRule', __name__, __path__[0])
    return driverloader.get_driver(driver_name, _drivers)
