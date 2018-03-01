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
from __future__ import absolute_import

from contextlib import contextmanager
import os

from nose.plugins.attrib import attr

from testlib import VdsmTestCase as TestCaseBase
from testValidation import ValidateRunningAsRoot
from monkeypatch import MonkeyPatch

from .nettestlib import dummy_device

from vdsm.network import sourceroute
from vdsm.network.ipwrapper import addrAdd
from vdsm.network.sourceroute import DynamicSourceRoute


TABLE = '4026531841'
DEVICE = 'test-network'

IPV4_ADDRESS = '192.168.99.1'
IPV4_GW = '192.168.99.2'
IPV4_MASK = 29
IPv4_NET = '192.168.99.0/29'
IPV4_TABLE = '3232260865'


def _routeShowTableAll(table):
    dirName = os.path.dirname(os.path.realpath(__file__))
    with open(os.path.join(dirName, "ip_route_show_table_all.out")) as tabFile:
        return tabFile.readlines()


@attr(type='unit')
class TestFilters(TestCaseBase):
    @MonkeyPatch(sourceroute, 'routeShowTable', _routeShowTableAll)
    def test_source_route_retrieval(self):
        routes = sourceroute.DynamicSourceRoute._getRoutes(TABLE)
        self.assertEqual(len(routes), 2)
        for route in routes:
            self.assertEqual(route.table, TABLE)
            if route.device is not None:
                self.assertEqual(route.device, DEVICE)


@attr(type='integration')
class TestSourceRoute(TestCaseBase):

    @ValidateRunningAsRoot
    def test_sourceroute_add_remove_and_read(self):
        with dummy_device() as nic:
            addrAdd(nic, IPV4_ADDRESS, IPV4_MASK)

            with create_sourceroute(device=nic, ip=IPV4_ADDRESS,
                                    mask=IPV4_MASK, gateway=IPV4_GW):
                dsroute = DynamicSourceRoute(nic, None, None, None)
                routes, rules = dsroute.current_srconfig()

                self.assertEqual(2, len(routes), routes)
                self.assertEqual(2, len(rules), rules)

                self.assertEqual('0.0.0.0/0', routes[0].to)
                self.assertEqual(nic, routes[0].device)
                self.assertEqual(IPv4_NET, routes[1].to)
                self.assertEqual(nic, routes[1].device)

                self.assertEqual(IPv4_NET, rules[0].to)
                self.assertEqual(IPV4_TABLE, rules[0].table)
                self.assertEqual(nic, rules[0].iif)
                self.assertEqual(rules[0].prio, sourceroute.RULE_PRIORITY)
                self.assertEqual(IPv4_NET, rules[1].src)
                self.assertEqual(IPV4_TABLE, rules[1].table)
                self.assertEqual(rules[1].prio, sourceroute.RULE_PRIORITY)

    @ValidateRunningAsRoot
    def test_sourceroute_add_over_existing_route(self):
        with dummy_device() as nic:
            addrAdd(nic, IPV4_ADDRESS, IPV4_MASK)

            with create_sourceroute(device=nic, ip=IPV4_ADDRESS,
                                    mask=IPV4_MASK, gateway=IPV4_GW):
                sourceroute.add(nic, IPV4_ADDRESS, IPV4_MASK, IPV4_GW)


@contextmanager
def create_sourceroute(device, ip, mask, gateway):
    sourceroute.add(device, ip, mask, gateway)
    try:
        yield
    finally:
        sourceroute.remove(device)
