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

import abc

import six

from vdsm.network import driverloader


@six.add_metaclass(abc.ABCMeta)
class IPRouteApi(object):
    @staticmethod
    def add(route_data):
        """ Adding a route entry described by an IPRouteData data object """
        raise NotImplementedError

    @staticmethod
    def delete(route_data):
        """ Delete a route entry described by an IPRouteData data object """
        raise NotImplementedError

    @staticmethod
    def routes(table='all'):
        raise NotImplementedError


class IPRouteData(object):
    """ A data structure used to keep route information """

    def __init__(self, to, via, family, src=None, device=None, table=None):
        self._to = to
        self._via = via
        self._family = family
        self._src = src
        self._device = device
        self._table = table

    @property
    def to(self):
        return self._to

    @property
    def via(self):
        return self._via

    @property
    def src(self):
        return self._src

    @property
    def family(self):
        return self._family

    @property
    def device(self):
        return self._device

    @property
    def table(self):
        return self._table

    def __repr__(self):
        return (
            'IPRouteData(to={!r} via={!r} src={!r} family={!r} '
            'device={!r} table={!r})'.format(
                self.to,
                self.via,
                self.src,
                self.family,
                self.device,
                self.table,
            )
        )


class IPRouteError(Exception):
    pass


class IPRouteAddError(IPRouteError):
    pass


class IPRouteDeleteError(IPRouteError):
    pass


class Drivers(object):
    IPROUTE2 = 'iproute2'


def driver(driver_name):
    _drivers = driverloader.load_drivers('IPRoute', __name__, __path__[0])
    return driverloader.get_driver(driver_name, _drivers)
