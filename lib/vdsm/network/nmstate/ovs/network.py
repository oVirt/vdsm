# Copyright 2020 Red Hat, Inc.
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

from copy import deepcopy

from .info import OvsInfo
from ..bridge_util import NetworkConfig
from ..bridge_util import random_interface_name
from ..bridge_util import translate_config
from ..dns import Dns
from ..ip import IpAddress
from ..route import Routes
from ..schema import Interface
from ..schema import InterfaceState
from ..schema import InterfaceType
from ..schema import OvsBridgeSchema

BRIDGE_PREFIX = 'vdsmbr_'


class OvsNetwork(object):
    def __init__(self, netconf, runconf):
        """
        netconf: NetworkConfig object, representing a requested network setup.
        runconf: NetworkConfig object, representing an existing network setup
        if any.
        """
        self._netconf = netconf
        self._runconf = runconf
        self._name = netconf.name
        self._to_remove = netconf.remove

        self._nb_iface_state = None
        self._port_state = None
        self._route_state = None
        self._dns_state = None
        self._auto_dns = None

        self._create_dns()
        self._create_interface_state()
        self._create_routes()

    @property
    def name(self):
        return self._name

    @property
    def iface_state(self):
        return self._nb_iface_state

    @property
    def port_state(self):
        return self._port_state

    @property
    def sb_iface(self):
        return self._netconf.base_iface

    @property
    def remove(self):
        return self._to_remove

    @property
    def routes_state(self):
        return self._route_state

    @property
    def dns_state(self):
        return self._dns_state

    def _create_interface_state(self):
        if self._to_remove:
            nb_state = _remove_iface_state(self._name)
            port_state = None
        else:
            nb_state = self._create_nb_iface()
            port_state = self._create_port_state()
            self._add_ip(nb_state)

        self._nb_iface_state = nb_state
        self._port_state = port_state

    def _create_nb_iface(self):
        return {
            Interface.NAME: self._name,
            Interface.TYPE: InterfaceType.OVS_INTERFACE,
            Interface.STATE: InterfaceState.UP,
        }

    def _create_port_state(self):
        port_state = _create_basic_port_state(self._name)
        if self._netconf.vlan is not None:
            access_mode = OvsBridgeSchema.Port.Vlan.Mode.ACCESS
            port_state[OvsBridgeSchema.Port.VLAN_SUBTREE] = {
                OvsBridgeSchema.Port.Vlan.MODE: access_mode,
                OvsBridgeSchema.Port.Vlan.TAG: self._netconf.vlan,
            }
        return port_state

    def _create_routes(self):
        routes = Routes(self._netconf, self._runconf)
        self._route_state = routes.state

    def _create_dns(self):
        dns = Dns(self._netconf, self._runconf)
        self._dns_state = dns.state
        self._auto_dns = dns.auto_dns

    def _add_ip(self, nb_state):
        ip_addr = IpAddress(self._netconf, self._auto_dns)
        nb_state[Interface.IPV4] = ip_addr.create(IpAddress.IPV4)
        nb_state[Interface.IPV6] = ip_addr.create(IpAddress.IPV6)


class OvsBridge(object):
    def __init__(self, networks, running_networks, ovs_info):
        """
        networks: List of NetworkConfig objects, representing a requested
        network setup.
        running_networks: List of NetworkConfig object, representing
        an existing network setup.
        ovs_info: OvsInfo, representing information about ovs netowrks.
        """
        self._networks = networks
        self._running_networks = running_networks
        self._ovs_info = ovs_info

        self._desired_nb_by_sb = self._create_desired_nb_by_sb()
        self._persisted_ports_by_bridge = self._get_persisted_ports_by_bridge()
        self._bridge_by_sb = deepcopy(ovs_info.bridge_by_sb)

        self._sb_ifaces_state = {}
        self._bridge_ifaces_state = {}

        self._create_iface_state()

    @property
    def bridge_by_sb(self):
        return self._bridge_by_sb

    @property
    def bridge_ifaces_state(self):
        return self._bridge_ifaces_state

    @property
    def sb_ifaces_state(self):
        return self._sb_ifaces_state

    def _create_desired_nb_by_sb(self):
        nb_by_sb = deepcopy(self._ovs_info.nb_by_sb)

        for name, attrs in self._networks.items():
            nb_moved = self._nb_has_moved(name, attrs, self._running_networks)
            if attrs.remove or nb_moved:
                base_iface = self._running_networks[name].base_iface
                nb_by_sb[base_iface].remove(name)
            if not attrs.remove:
                nb_by_sb[attrs.base_iface].add(name)

        return nb_by_sb

    def _get_persisted_ports_by_bridge(self):
        net_names = list(self._networks.keys())
        persisted_ports_by_bridge = {}
        for bridge, ports in self._ovs_info.ports_by_bridge.items():
            persisted_ports_by_bridge[bridge] = [
                port
                for port in ports
                if not port[OvsBridgeSchema.Port.NAME] in net_names
            ]
        return persisted_ports_by_bridge

    def _create_iface_state(self):
        desired_sbs = [attrs.base_iface for attrs in self._networks.values()]
        for sb in self._desired_nb_by_sb.keys():
            if self._should_remove_bridge(sb):
                self._remove_bridge(sb)
            elif sb in desired_sbs:
                self._manage_bridge(sb)

    def _should_remove_bridge(self, sb):
        return self._bridge_exists(sb) and len(self._desired_nb_by_sb[sb]) == 0

    def _remove_bridge(self, sb):
        bridge = self._bridge_by_sb.pop(sb)
        self._bridge_ifaces_state[bridge] = _remove_iface_state(bridge)
        self._sb_ifaces_state[sb] = self._create_sb_iface_state(sb)

    def _manage_bridge(self, sb):
        bridge = (
            self._bridge_by_sb[sb]
            if self._bridge_exists(sb)
            else random_interface_name(BRIDGE_PREFIX)
        )
        self._bridge_ifaces_state[bridge] = self._create_bridge_state(
            bridge, sb
        )
        if not self._bridge_exists(sb):
            sb_state = self._create_sb_iface_state(sb)
            self._add_sb_ip(sb_state)
            self._sb_ifaces_state[sb] = sb_state
            self._bridge_by_sb[sb] = bridge

    def _create_bridge_state(self, name, sb):
        return {
            Interface.NAME: name,
            Interface.STATE: InterfaceState.UP,
            Interface.TYPE: InterfaceType.OVS_BRIDGE,
            OvsBridgeSchema.CONFIG_SUBTREE: self._create_bridge_ports(
                name, sb
            ),
        }

    def _create_bridge_ports(self, name, sb):
        ports = (
            self._persisted_ports_by_bridge[name]
            if self._bridge_exists(sb)
            else [_create_basic_port_state(sb)]
        )
        return {OvsBridgeSchema.PORT_SUBTREE: ports}

    def _bridge_exists(self, sb):
        return sb in self._ovs_info.bridge_by_sb

    @staticmethod
    def _create_sb_iface_state(name):
        return {Interface.NAME: name, Interface.STATE: InterfaceState.UP}

    @staticmethod
    def _nb_has_moved(name, attrs, rnets):
        base_iface = attrs.base_iface
        return name in rnets and rnets[name].base_iface != base_iface

    @staticmethod
    def _add_sb_ip(sb_state):
        # Because SB IP stack should be disabled we don't need any
        # netconf info
        ip_addr = IpAddress(netconf=None, auto_dns=False)
        sb_state[Interface.IPV4] = ip_addr.create(
            IpAddress.IPV4, enabled=False
        )
        sb_state[Interface.IPV6] = ip_addr.create(
            IpAddress.IPV6, enabled=False
        )


def generate_state(networks, running_networks, current_iface_state):
    nets_config = translate_config(networks)
    rnets_config = translate_config(running_networks)
    empty_config = NetworkConfig(name=None, attrs={})

    ovs_info = OvsInfo(rnets_config, current_iface_state)
    bridges = OvsBridge(nets_config, rnets_config, ovs_info)
    nets = [
        OvsNetwork(
            nets_config[netname], rnets_config.get(netname, empty_config)
        )
        for netname in networks.keys()
    ]

    routes_state = []
    dns_state = {}
    net_ifstates = bridges.bridge_ifaces_state
    net_ifstates.update(bridges.sb_ifaces_state)

    for net in nets:
        net_ifstates[net.name] = net.iface_state
        routes_state += net.routes_state
        net_dns_state = net.dns_state
        if net_dns_state is not None:
            dns_state[net.name] = net_dns_state

        if net.remove:
            continue

        _enforce_network_mac_address(
            nets_config[net.name], net_ifstates, current_iface_state
        )
        bridge = bridges.bridge_by_sb[net.sb_iface]
        # Add port state to the bridge
        if net.port_state:
            net_ifstates[bridge][OvsBridgeSchema.CONFIG_SUBTREE][
                OvsBridgeSchema.PORT_SUBTREE
            ].append(net.port_state)

    for bridge in bridges.bridge_by_sb.values():
        if bridge in net_ifstates:
            _sort_ports_by_name(net_ifstates[bridge])

    return net_ifstates, routes_state, dns_state


def _enforce_network_mac_address(net, net_ifstates, current_iface_state):
    if net.base_iface not in current_iface_state:
        # This scenario happens when we are trying to create a network and
        # bond in the same transaction. In this case we cannot reliably
        # determine the MAC address of future bond.
        return

    net_ifstates[net.name].update(
        {Interface.MAC: current_iface_state[net.base_iface][Interface.MAC]}
    )


def _create_basic_port_state(name):
    return {OvsBridgeSchema.Port.NAME: name}


def _remove_iface_state(name):
    return {Interface.NAME: name, Interface.STATE: InterfaceState.ABSENT}


def _sort_ports_by_name(bridge_state):
    bridge_state[OvsBridgeSchema.CONFIG_SUBTREE][
        OvsBridgeSchema.PORT_SUBTREE
    ].sort(key=lambda d: d[OvsBridgeSchema.Port.NAME])
