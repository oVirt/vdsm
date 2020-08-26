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
from vdsm.network.nmstate import api
from vdsm.network.nmstate import bond
from vdsm.network.nmstate import bridge_util
from vdsm.network.nmstate import ip
from vdsm.network.nmstate import linux_bridge
from vdsm.network.nmstate import route
from vdsm.network.nmstate.ovs import network as ovs_network
from vdsm.network.nmstate.ovs import info as ovs_info


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


class OvsBridgeType(object):
    TYPE = "ovs-bridge"
    CONFIG_SUBTREE = "bridge"
    PORT_SUBTREE = "port"

    class Port:
        NAME = "name"
        VLAN_SUBTREE = "vlan"

        class Vlan:
            TAG = "tag"
            MODE = "mode"

            class Mode:
                ACCESS = "access"


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
    p_ovs = mock.patch.object(nmstate, 'OvsBridgeSchema', OvsBridgeType)
    with p_iface, p_ifstate, p_iftype, p_bridge, p_ovs:
        with p_bond, p_route, p_iface_ip, p_iface_ipv6, p_dns:
            yield


@pytest.fixture(scope='session', autouse=True)
def nmstate_api_schema():
    p_iface = mock.patch.object(api, 'Interface', NMStateInterface)
    p_ifstate = mock.patch.object(api, 'InterfaceState', NMStateInterfaceState)
    p_iftype = mock.patch.object(api, 'InterfaceType', NMStateInterfaceType)
    p_bond = mock.patch.object(api, 'BondSchema', NMStateBond)
    p_route = mock.patch.object(api, 'Route', NMStateRoute)
    p_dns = mock.patch.object(api, 'DNS', NMStateDns)
    with p_iface, p_ifstate, p_iftype, p_bond, p_route, p_dns:
        yield


@pytest.fixture(scope='session', autouse=True)
def nmstate_bond_module_schema():
    p_bond = mock.patch.object(bond, 'BondSchema', NMStateBond)
    p_iface = mock.patch.object(bond, 'Interface', NMStateInterface)
    p_iface_ip = mock.patch.object(bond, 'InterfaceIP', NMStateInterfaceIP)
    p_ifstate = mock.patch.object(
        bond, 'InterfaceState', NMStateInterfaceState
    )
    p_iftype = mock.patch.object(bond, 'InterfaceType', NMStateInterfaceType)
    with p_iface, p_ifstate, p_iftype, p_bond, p_iface_ip:
        yield


@pytest.fixture(scope='session', autouse=True)
def nmstate_util_module_schema():
    p_iface = mock.patch.object(bridge_util, 'Interface', NMStateInterface)
    p_ifstate = mock.patch.object(
        bridge_util, 'InterfaceState', NMStateInterfaceState
    )
    p_iface_ip = mock.patch.object(
        bridge_util, 'InterfaceIP', NMStateInterfaceIP
    )
    p_iface_ipv6 = mock.patch.object(
        bridge_util, 'InterfaceIPv6', NMStateInterfaceIPv6
    )
    with p_iface, p_ifstate, p_iface_ip, p_iface_ipv6:
        yield


@pytest.fixture(scope='session', autouse=True)
def nmstate_route_module_schema():
    p_route = mock.patch.object(route, 'Route', NMStateRoute)
    with p_route:
        yield


@pytest.fixture(scope='session', autouse=True)
def nmstate_ip_module_schema():
    p_iface_ip = mock.patch.object(ip, 'InterfaceIP', NMStateInterfaceIP)
    p_iface_ipv6 = mock.patch.object(ip, 'InterfaceIPv6', NMStateInterfaceIPv6)
    with p_iface_ip, p_iface_ipv6:
        yield


@pytest.fixture(scope='session', autouse=True)
def nmstate_linux_bridge_network_module_schema():
    p_iface = mock.patch.object(linux_bridge, 'Interface', NMStateInterface)
    p_iface_ip = mock.patch.object(
        linux_bridge, 'InterfaceIP', NMStateInterfaceIP
    )
    p_ifstate = mock.patch.object(
        linux_bridge, 'InterfaceState', NMStateInterfaceState
    )
    p_iftype = mock.patch.object(
        linux_bridge, 'InterfaceType', NMStateInterfaceType
    )
    p_bridge = mock.patch.object(
        linux_bridge, 'LinuxBridge', NMStateLinuxBridge
    )
    with p_iface, p_ifstate, p_iftype, p_bridge, p_iface_ip:
        yield


@pytest.fixture(scope='session', autouse=True)
def nmstate_ovs_network_schema():
    p_iface = mock.patch.object(ovs_network, 'Interface', NMStateInterface)
    p_ifstate = mock.patch.object(
        ovs_network, 'InterfaceState', NMStateInterfaceState
    )
    p_iftype = mock.patch.object(
        ovs_network, 'InterfaceType', NMStateInterfaceType
    )
    p_ovs = mock.patch.object(ovs_network, 'OvsBridgeSchema', OvsBridgeType)
    with p_iface, p_ifstate, p_iftype, p_ovs:
        yield


@pytest.fixture(scope='session', autouse=True)
def nmstate_ovs_info_schema():
    p_iface = mock.patch.object(ovs_info, 'Interface', NMStateInterface)
    p_iftype = mock.patch.object(
        ovs_info, 'InterfaceType', NMStateInterfaceType
    )
    p_ovs = mock.patch.object(ovs_info, 'OvsBridgeSchema', OvsBridgeType)
    with p_iface, p_iftype, p_ovs:
        yield
