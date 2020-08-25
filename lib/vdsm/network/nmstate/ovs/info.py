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

from collections import defaultdict

from ..bridge_util import NetInfoIfaceSchema
from ..bridge_util import NetInfoSchema
from ..bridge_util import SwitchType
from ..schema import Interface
from ..schema import InterfaceType
from ..schema import OvsBridgeSchema


EMPTY_INFO = {
    NetInfoIfaceSchema.IPv4.PRIMARY_ADDR: '',
    NetInfoIfaceSchema.IPv4.ADRRS: [],
    NetInfoIfaceSchema.IPv4.GATEWAY: '',
    NetInfoIfaceSchema.IPv4.DEFAULT_ROUTE: False,
    NetInfoIfaceSchema.IPv4.NETMASK: '',
    NetInfoIfaceSchema.IPv4.DHCP: False,
    NetInfoIfaceSchema.IPv6.ADDRS: [],
    NetInfoIfaceSchema.IPv6.AUTOCONF: False,
    NetInfoIfaceSchema.IPv6.GATEWAY: '::',
    NetInfoIfaceSchema.IPv6.DHCP: False,
}

SHARED_NETWORK_ATTRIBUTES = (
    NetInfoIfaceSchema.MTU,
    NetInfoIfaceSchema.IPv4.PRIMARY_ADDR,
    NetInfoIfaceSchema.IPv4.ADRRS,
    NetInfoIfaceSchema.IPv4.GATEWAY,
    NetInfoIfaceSchema.IPv4.DEFAULT_ROUTE,
    NetInfoIfaceSchema.IPv4.NETMASK,
    NetInfoIfaceSchema.IPv4.DHCP,
    NetInfoIfaceSchema.IPv6.ADDRS,
    NetInfoIfaceSchema.IPv6.AUTOCONF,
    NetInfoIfaceSchema.IPv6.GATEWAY,
    NetInfoIfaceSchema.IPv6.DHCP,
)


class OvsInfo(object):
    def __init__(self, running_networks, current_ifaces_state):
        self._running_networks = running_networks
        self._current_ifaces_state = current_ifaces_state

        self._nb_by_sb = self._get_nb_by_sb()
        self._ports_by_bridge = self._get_ports_by_bridge()
        self._bridge_by_sb = self._get_bridge_by_sb()
        self._ports_by_name = self._get_ports_by_name()

    @property
    def nb_by_sb(self):
        return self._nb_by_sb

    @property
    def ports_by_bridge(self):
        return self._ports_by_bridge

    @property
    def bridge_by_sb(self):
        return self._bridge_by_sb

    @property
    def ports_by_name(self):
        return self._ports_by_name

    def _get_nb_by_sb(self):
        nb_by_sb = defaultdict(set)
        for name, attrs in self._running_networks.items():
            nb_by_sb[attrs.base_iface].add(name)

        return nb_by_sb

    def _get_ports_by_bridge(self):
        return {
            name: state[OvsBridgeSchema.CONFIG_SUBTREE][
                OvsBridgeSchema.PORT_SUBTREE
            ]
            for name, state in self._current_ifaces_state.items()
            if state[Interface.TYPE] == InterfaceType.OVS_BRIDGE
            and self._bridge_has_ports(state)
        }

    def _get_bridge_by_sb(self):
        port_names_by_bridge = {
            name: self._get_port_names(ports)
            for name, ports in self._ports_by_bridge.items()
        }
        bridge_by_sb = {}
        for sb in self._nb_by_sb.keys():
            for bridge, ports in port_names_by_bridge.items():
                if sb in ports:
                    bridge_by_sb[sb] = bridge
                    break

        return bridge_by_sb

    def _get_ports_by_name(self):
        ports = {}
        for port_state in self._ports_by_bridge.values():
            ports.update(
                {
                    state[OvsBridgeSchema.Port.NAME]: state
                    for state in port_state
                }
            )
        return ports

    @staticmethod
    def _get_port_names(ports):
        return [port[OvsBridgeSchema.Port.NAME] for port in ports]

    @staticmethod
    def _bridge_has_ports(state):
        # The state when OvS bridge has no port section can happen only if we
        # are trying to process bridges that are not managed by nmstate.
        return (
            OvsBridgeSchema.PORT_SUBTREE
            in state[OvsBridgeSchema.CONFIG_SUBTREE]
        )


class OvsNetInfo(object):
    def __init__(
        self, ovs_info, base_netinfo, running_networks, current_ifaces_state
    ):
        self._ovs_info = ovs_info
        self._base_netinfo = base_netinfo
        self._running_networks = running_networks
        self._current_ifaces_state = current_ifaces_state

    def create_netinfo(self):
        for sb, bridge in self._ovs_info.bridge_by_sb.items():
            for nb in self._ovs_info.nb_by_sb[sb]:
                net = self._create_network_info(nb, sb)

                if net.get(NetInfoIfaceSchema.VLAN) is not None:
                    self._base_netinfo[NetInfoSchema.VLANS].update(
                        self._fake_vlan(net, sb)
                    )
                if net[NetInfoIfaceSchema.BRIDGED]:
                    self._base_netinfo[NetInfoSchema.BRIDGES].update(
                        self._fake_bridge(net, nb)
                    )
                else:
                    self._fake_bridgeless(net)

                self._base_netinfo[NetInfoSchema.NETWORKS][nb] = net

    def _create_network_info(self, nb, sb):
        vlan = (
            self._ovs_info.ports_by_name[nb]
            .get(OvsBridgeSchema.Port.VLAN_SUBTREE, {})
            .get(OvsBridgeSchema.Port.Vlan.TAG)
        )
        southbound = f'{sb}.{vlan}' if vlan is not None else sb
        bridged = self._running_networks[nb].bridged

        network = {
            NetInfoIfaceSchema.IFACE: nb if bridged else southbound,
            NetInfoIfaceSchema.BRIDGED: bridged,
            NetInfoIfaceSchema.SOUTHBOUND: sb,
            # TODO Check if we can have a scenario with multiple ports
            NetInfoIfaceSchema.PORTS: [southbound],
            # TODO Add support for STP, now it is disabled by default
            NetInfoIfaceSchema.STP: False,
            NetInfoIfaceSchema.SWITCH: SwitchType.OVS,
            NetInfoIfaceSchema.MTU: self._current_ifaces_state[nb][
                Interface.MTU
            ],
        }
        if vlan is not None:
            network[NetInfoIfaceSchema.VLAN] = vlan
        # TODO Support IP parameters
        network.update(EMPTY_INFO)

        return network

    def _fake_bridgeless(self, net):
        iface = net[NetInfoIfaceSchema.IFACE]
        type = None
        next(
            type
            for type in (
                NetInfoSchema.VLANS,
                NetInfoSchema.BONDS,
                NetInfoSchema.NICS,
            )
            if iface in self._base_netinfo[type]
        )

        if type:
            self._base_netinfo[type][iface].update(_shared_net_attrs(net))

    @staticmethod
    def _fake_vlan(net, sb):
        vlan = net[NetInfoIfaceSchema.VLAN]
        vlan_info = {
            NetInfoIfaceSchema.VLAN: vlan,
            NetInfoIfaceSchema.IFACE: sb,
            NetInfoIfaceSchema.MTU: net[NetInfoIfaceSchema.MTU],
        }
        vlan_info.update(EMPTY_INFO)
        return {f'{sb}.{vlan}': vlan_info}

    @staticmethod
    def _fake_bridge(net, nb):
        bridge_info = {
            NetInfoIfaceSchema.PORTS: net[NetInfoIfaceSchema.PORTS],
            NetInfoIfaceSchema.STP: net[NetInfoIfaceSchema.STP],
        }
        bridge_info.update(_shared_net_attrs(net))
        return {nb: bridge_info}


def _shared_net_attrs(attrs):
    return {key: attrs[key] for key in SHARED_NETWORK_ATTRIBUTES}
