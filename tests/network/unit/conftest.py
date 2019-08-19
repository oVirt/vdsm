# Copyright 2019 Red Hat, Inc.
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

import pytest

from testlib import mock

from vdsm.network import nmstate


class NMStateInterface(object):
    KEY = 'interfaces'

    NAME = 'name'
    TYPE = 'type'
    STATE = 'state'

    IPV4 = 'ipv4'
    IPV6 = 'ipv6'

    MAC = 'mac-address'
    MTU = 'mtu'


class NMStateRoute(object):
    KEY = 'routes'

    RUNNING = 'running'
    CONFIG = 'config'
    STATE = 'state'
    STATE_ABSENT = 'absent'
    TABLE_ID = 'table-id'
    DESTINATION = 'destination'
    NEXT_HOP_INTERFACE = 'next-hop-interface'
    NEXT_HOP_ADDRESS = 'next-hop-address'
    METRIC = 'metric'
    USE_DEFAULT_METRIC = -1
    USE_DEFAULT_ROUTE_TABLE = 0


class NMStateInterfaceIP(object):
    ENABLED = 'enabled'
    ADDRESS = 'address'
    ADDRESS_IP = 'ip'
    ADDRESS_PREFIX_LENGTH = 'prefix-length'
    DHCP = 'dhcp'
    AUTO_DNS = 'auto-dns'
    AUTO_GATEWAY = 'auto-gateway'
    AUTO_ROUTES = 'auto-routes'


class NMStateDns(object):
    KEY = 'dns-resolver'
    RUNNING = 'running'
    CONFIG = 'config'
    SERVER = 'server'
    SEARCH = 'search'


@pytest.fixture(scope='session', autouse=True)
def nmstate_schema():
    patch_interface = mock.patch.object(nmstate, 'Interface', NMStateInterface)
    patch_route = mock.patch.object(nmstate, 'Route', NMStateRoute)
    patch_interface_ip = mock.patch.object(nmstate, 'InterfaceIP',
                                           NMStateInterfaceIP)
    patch_dns = mock.patch.object(nmstate, 'DNS', NMStateDns)
    with patch_interface, patch_route, patch_interface_ip, patch_dns:
        yield
