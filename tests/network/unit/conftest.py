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

from network.compat import mock

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


class NMStateInterfaceState(object):
    KEY = NMStateInterface.STATE

    DOWN = 'down'
    UP = 'up'
    ABSENT = 'absent'


class NMStateInterfaceType(object):
    KEY = NMStateInterface.TYPE

    BOND = 'bond'
    DUMMY = 'dummy'
    ETHERNET = 'ethernet'
    LINUX_BRIDGE = 'linux-bridge'
    OVS_BRIDGE = 'ovs-bridge'
    OVS_INTERFACE = 'ovs-interface'
    OVS_PORT = 'ovs-port'
    UNKNOWN = 'unknown'
    VLAN = 'vlan'


class NMStateBond(object):
    KEY = NMStateInterfaceType.BOND
    CONFIG_SUBTREE = 'link-aggregation'

    MODE = 'mode'
    SLAVES = 'slaves'
    OPTIONS_SUBTREE = 'options'


class NMStateLinuxBridge(object):
    TYPE = 'linux-bridge'
    CONFIG_SUBTREE = 'bridge'

    OPTIONS_SUBTREE = 'options'
    MAC_AGEING_TIME = 'mac-ageing-time'
    GROUP_FORWARD_MASK = 'group-forward-mask'
    MULTICAST_SNOOPING = 'multicast-snooping'

    STP_SUBTREE = 'stp'

    class STP:
        ENABLED = 'enabled'
        FORWARD_DELAY = 'forward-delay'
        HELLO_TIME = 'hello-time'
        MAX_AGE = 'max-age'
        PRIORITY = 'priority'

    PORT_SUBTREE = 'port'

    class Port:
        NAME = 'name'
        STP_HAIRPIN_MODE = 'stp-hairpin-mode'
        STP_PATH_COST = 'stp-path-cost'
        STP_PRIORITY = 'stp-priority'


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


class NMStateInterfaceIPv6(NMStateInterfaceIP):
    AUTOCONF = 'autoconf'


class NMStateDns(object):
    KEY = 'dns-resolver'
    RUNNING = 'running'
    CONFIG = 'config'
    SERVER = 'server'
    SEARCH = 'search'


@pytest.fixture(scope='session', autouse=True)
def nmstate_schema():
    p_iface = mock.patch.object(nmstate, 'Interface', NMStateInterface)
    p_ifstate = mock.patch.object(
        nmstate, 'InterfaceState', NMStateInterfaceState
    )
    p_iftype = mock.patch.object(
        nmstate, 'InterfaceType', NMStateInterfaceType
    )
    p_bridge = mock.patch.object(nmstate, 'LinuxBridge', NMStateLinuxBridge)
    p_bond = mock.patch.object(nmstate, 'BondSchema', NMStateBond)
    p_route = mock.patch.object(nmstate, 'Route', NMStateRoute)
    p_iface_ip = mock.patch.object(nmstate, 'InterfaceIP', NMStateInterfaceIP)
    p_iface_ipv6 = mock.patch.object(
        nmstate, 'InterfaceIPv6', NMStateInterfaceIPv6
    )
    p_dns = mock.patch.object(nmstate, 'DNS', NMStateDns)
    with p_iface, p_ifstate, p_iftype, p_bridge:
        with p_bond, p_route, p_iface_ip, p_iface_ipv6, p_dns:
            yield
