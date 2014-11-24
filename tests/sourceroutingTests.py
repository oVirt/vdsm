#
# Copyright 2014 Red Hat, Inc.
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

import os

from testlib import VdsmTestCase as TestCaseBase
from monkeypatch import MonkeyPatch

from network import sourceroute

TABLE = '4026531841'
DEVICE = 'test-network'


def _routeShowTableAll(table):
    dirName = os.path.dirname(os.path.realpath(__file__))
    with open(os.path.join(dirName, "ip_route_show_table_all.out")) as tabFile:
        return tabFile.readlines()


class TestFilters(TestCaseBase):
    @MonkeyPatch(sourceroute, 'routeShowTable', _routeShowTableAll)
    def test_source_route_retrieval(self):
        routes = sourceroute.DynamicSourceRoute._getRoutes(TABLE)
        self.assertEqual(len(routes), 2)
        for route in routes:
            self.assertEqual(route.table, TABLE)
            if route.device is not None:
                self.assertEqual(route.device, DEVICE)
