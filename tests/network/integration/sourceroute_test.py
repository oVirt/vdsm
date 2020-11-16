#
# Copyright 2014-2020 Red Hat, Inc.
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

from contextlib import contextmanager
import os

import pytest

import network as network_tests
from network.nettestlib import dummy_device

from vdsm.network import sourceroute
from vdsm.network.ipwrapper import addrAdd
from vdsm.network.sourceroute import DynamicSourceRoute


TABLE = '4026531841'
DEVICE = 'test-network'

IPV4_ADDRESS = '192.168.99.1'
IPV4_GW = '192.168.99.2'
IPV4_MASK = 29
IPV4_NET = '192.168.99.0/29'
IPV4_TABLE = '3232260865'

TESTS_STATIC_PATH = os.path.join(
    os.path.dirname(network_tests.__file__), 'static'
)


@pytest.fixture
def nic0():
    with dummy_device() as nic:
        yield nic


def _route_show_table_all(table):
    f_iproute = os.path.join(TESTS_STATIC_PATH, 'ip_route_show_table_all.out')
    with open(f_iproute) as tabFile:
        return tabFile.readlines()


class TestSourceRoute(object):
    def test_sourceroute_add_remove_and_read(self, nic0):
        addrAdd(nic0, IPV4_ADDRESS, IPV4_MASK)

        with create_sourceroute(
            device=nic0, ip=IPV4_ADDRESS, mask=IPV4_MASK, gateway=IPV4_GW
        ):
            dsroute = DynamicSourceRoute(nic0, None, None, None)
            routes, rules = dsroute.current_srconfig()

            assert len(routes) == 2
            assert len(rules) == 2

            assert routes[0].to == '0.0.0.0/0'
            assert routes[0].device == nic0
            assert routes[1].to == IPV4_NET
            assert routes[1].device == nic0

            assert rules[0].to == IPV4_NET
            assert rules[0].table == IPV4_TABLE
            assert rules[0].iif == nic0
            assert rules[0].prio == sourceroute.RULE_PRIORITY
            assert rules[1].src == IPV4_NET
            assert rules[1].table == IPV4_TABLE
            assert rules[1].prio == sourceroute.RULE_PRIORITY

    def test_sourceroute_add_over_existing_route(self, nic0):
        addrAdd(nic0, IPV4_ADDRESS, IPV4_MASK)

        with create_sourceroute(
            device=nic0, ip=IPV4_ADDRESS, mask=IPV4_MASK, gateway=IPV4_GW
        ):
            sourceroute.add(nic0, IPV4_ADDRESS, IPV4_MASK, IPV4_GW)


@contextmanager
def create_sourceroute(device, ip, mask, gateway):
    sourceroute.add(device, ip, mask, gateway)
    try:
        yield
    finally:
        sourceroute.remove(device)
