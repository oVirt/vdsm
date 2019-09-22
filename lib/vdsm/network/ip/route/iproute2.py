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
from vdsm.network.ipwrapper import Route
from vdsm.network.ipwrapper import routeAdd
from vdsm.network.ipwrapper import routeDel
from vdsm.network.ipwrapper import routeShowTable

from . import IPRouteAddError, IPRouteDeleteError, IPRouteData, IPRouteApi


class IPRoute(IPRouteApi):
    @staticmethod
    def add(route_data):
        r = route_data
        with _translate_iproute2_exception(IPRouteAddError, route_data):
            routeAdd(Route(r.to, r.via, r.src, r.device, r.table), r.family)

    @staticmethod
    def delete(route_data):
        r = route_data
        with _translate_iproute2_exception(IPRouteDeleteError, route_data):
            routeDel(Route(r.to, r.via, r.src, r.device, r.table), r.family)

    @staticmethod
    def routes(table='all'):
        routes_data = routeShowTable(table)
        for route_data in routes_data:
            try:
                r = Route.fromText(route_data)
                family = 6 if _is_ipv6_addr_soft_check(r.network) else 4
                rtable = r.table if table == 'all' else table
                yield IPRouteData(
                    r.network, r.via, family, r.src, r.device, rtable
                )
            except ValueError:
                logging.warning('Could not parse route %s', route_data)


@contextlib.contextmanager
def _translate_iproute2_exception(new_exception, route_data):
    try:
        yield
    except IPRoute2Error:
        _, value, tb = sys.exc_info()
        error_message = value.args[1][0]
        six.reraise(
            new_exception, new_exception(str(route_data), error_message), tb
        )


def _is_ipv6_addr_soft_check(addr):
    return addr.count(':') > 1
