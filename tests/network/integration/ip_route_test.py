# Copyright 2017-2018 Red Hat, Inc.
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

from contextlib import contextmanager

import unittest

from vdsm.network.ip import route as ip_route
from vdsm.network.ip.route import IPRouteData
from vdsm.network.ip.route import IPRouteAddError, IPRouteDeleteError

IPV4_ADDRESS = '192.168.99.1'


class IPRouteTest(unittest.TestCase):
    IPRoute = ip_route.driver(ip_route.Drivers.IPROUTE2)

    def test_add_delete_and_read_route(self):
        route = IPRouteData(to=IPV4_ADDRESS, via=None, family=4, device='lo')
        with self.create_route(route):
            routes = [
                r
                for r in IPRouteTest.IPRoute.routes(table='main')
                if r.to == IPV4_ADDRESS
            ]
            self.assertEqual(1, len(routes))
            self.assertEqual(routes[0].device, 'lo')
            self.assertEqual(routes[0].table, 'main')

    def test_delete_non_existing_route(self):
        route = IPRouteData(to=IPV4_ADDRESS, via=None, family=4, device='lo')
        with self.assertRaises(IPRouteDeleteError):
            IPRouteTest.IPRoute.delete(route)

    def test_add_route_with_non_existing_device(self):
        route = IPRouteData(to=IPV4_ADDRESS, via=None, family=4, device='NoNe')
        with self.assertRaises(IPRouteAddError):
            IPRouteTest.IPRoute.add(route)

    @contextmanager
    def create_route(self, route_data):
        IPRouteTest.IPRoute.add(route_data)
        try:
            yield
        finally:
            IPRouteTest.IPRoute.delete(route_data)
