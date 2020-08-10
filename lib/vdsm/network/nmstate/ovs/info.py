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

from ..schema import Interface
from ..schema import InterfaceType
from ..schema import OvsBridgeSchema


class OvsInfo(object):
    def __init__(self, running_networks, current_ifaces_state):
        self._running_networks = running_networks
        self._current_ifaces_state = current_ifaces_state

        self._nb_by_sb = self._get_nb_by_sb()
        self._ports_by_bridge = self._get_ports_by_bridge()
        self._bridge_by_sb = self._get_bridge_by_sb()

    @property
    def nb_by_sb(self):
        return self._nb_by_sb

    @property
    def ports_by_bridge(self):
        return self._ports_by_bridge

    @property
    def bridge_by_sb(self):
        return self._bridge_by_sb

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
