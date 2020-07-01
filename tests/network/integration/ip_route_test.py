# Copyright 2017-2020 Red Hat, Inc.
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

from contextlib import contextmanager

import pytest

from vdsm.network.ip import route as ip_route
from vdsm.network.ip.route import IPRouteAddError
from vdsm.network.ip.route import IPRouteData
from vdsm.network.ip.route import IPRouteDeleteError


IPV4_ADDRESS = '192.168.99.1'


@pytest.fixture(scope='module')
def ip_route_driver():
    return ip_route.driver(ip_route.Drivers.IPROUTE2)


class TestIPRoute(object):
    def test_add_delete_and_read_route(self, ip_route_driver):
        route = IPRouteData(to=IPV4_ADDRESS, via=None, family=4, device='lo')
        with self._create_route(ip_route_driver, route):
            routes = [
                r
                for r in ip_route_driver.routes(table='main')
                if r.to == IPV4_ADDRESS
            ]
            assert len(routes) == 1
            assert routes[0].device == 'lo'
            assert routes[0].table == 'main'

    def test_delete_non_existing_route(self, ip_route_driver):
        route = IPRouteData(to=IPV4_ADDRESS, via=None, family=4, device='lo')
        with pytest.raises(IPRouteDeleteError):
            ip_route_driver.delete(route)

    def test_add_route_with_non_existing_device(self, ip_route_driver):
        route = IPRouteData(to=IPV4_ADDRESS, via=None, family=4, device='NoNe')
        with pytest.raises(IPRouteAddError):
            ip_route_driver.add(route)

    @contextmanager
    def _create_route(self, route_driver, route_data):
        route_driver.add(route_data)
        try:
            yield
        finally:
            route_driver.delete(route_data)
