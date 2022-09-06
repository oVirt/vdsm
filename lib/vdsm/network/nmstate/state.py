# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from collections import defaultdict
import itertools

from .bridge_util import DEFAULT_MTU
from .bridge_util import is_iface_absent
from .bridge_util import OVN_BRIDGE_MAPPINGS_KEY
from .schema import BondSchema
from .schema import DNS
from .schema import Interface
from .schema import InterfaceState
from .schema import InterfaceType
from .schema import OvsDB
from .schema import Route
from .schema import RouteRule
from .schema import Vlan


class NetworkingState(object):
    def __init__(
        self,
        net_ifstate=None,
        routes_state=None,
        route_rules_state=None,
        dns_state=None,
        bridge_mappings=None,
    ):
        self._ifaces_state = net_ifstate
        self._routes_state = routes_state
        self._route_rules_state = route_rules_state
        self._dns_state = dns_state
        self._bridge_mappings = bridge_mappings

    def add_bond_state(self, bond_ifstates):
        for ifname, ifstate in bond_ifstates.items():
            if not is_iface_absent(ifstate):
                ifstate.update(self._ifaces_state.get(ifname, {}))
        self._ifaces_state.update(bond_ifstates)

    def update_mtu(self, linux_br_requested, current_ifaces_state):
        if linux_br_requested:
            self._set_vlans_base_mtu(current_ifaces_state)
        self._set_bond_slaves_mtu(current_ifaces_state)

    def state(self):
        state = {}
        if self._ifaces_state:
            interfaces = [ifstate for ifstate in self._ifaces_state.values()]
            state[Interface.KEY] = sorted(
                interfaces, key=lambda d: d[Interface.NAME]
            )
        if self._routes_state:
            state[Route.KEY] = {Route.CONFIG: self._routes_state}
        if self._route_rules_state:
            state[RouteRule.KEY] = {RouteRule.CONFIG: self._route_rules_state}
        if self._dns_state:
            nameservers = itertools.chain.from_iterable(
                ns for ns in self._dns_state.values()
            )
            state[DNS.KEY] = {DNS.CONFIG: {DNS.SERVER: list(nameservers)}}
        # Empty string ('') is valid mapping
        if self._bridge_mappings is not None:
            state[OvsDB.KEY] = {
                OvsDB.EXTERNAL_IDS: {
                    OVN_BRIDGE_MAPPINGS_KEY: self._bridge_mappings
                }
            }
        return state

    def _set_vlans_base_mtu(self, current_ifstates):
        vlans_base_mtus = defaultdict(list)
        ifaces_to_remove = set(
            ifname
            for ifname, ifstate in self._ifaces_state.items()
            if ifstate.get(Interface.STATE) == InterfaceState.ABSENT
        )
        current_remaining_ifnames = set(current_ifstates) - ifaces_to_remove

        current_remaining_vlan_ifaces = (
            (ifname, ifstate)
            for ifname, ifstate in current_ifstates.items()
            if ifname not in ifaces_to_remove
            and ifstate[Interface.TYPE] == InterfaceType.VLAN
        )
        for vlan_iface, ifstate in current_remaining_vlan_ifaces:
            base_vlan = ifstate[Vlan.CONFIG_SUBTREE][Vlan.BASE_IFACE]
            if vlan_iface in self._ifaces_state:
                vlan_mtu = self._ifaces_state[vlan_iface][Interface.MTU]
            else:
                vlan_mtu = ifstate[Interface.MTU]
            if base_vlan in current_remaining_ifnames:
                vlans_base_mtus[base_vlan].append(vlan_mtu)
            if base_vlan in self._ifaces_state:
                vlans_base_mtus[base_vlan].append(
                    self._ifaces_state[base_vlan].get(Interface.MTU, 0)
                )

        for base_vlan, vlans_mtus in vlans_base_mtus.items():
            max_mtu = max(vlans_mtus)
            # No need to enforce the MTU if it didn't change
            if (
                base_vlan not in self._ifaces_state
                and base_vlan in current_ifstates
                and current_ifstates[base_vlan][Interface.MTU] == max_mtu
            ):
                continue

            if base_vlan not in self._ifaces_state:
                self._ifaces_state[base_vlan] = {Interface.NAME: base_vlan}
            self._ifaces_state[base_vlan][Interface.MTU] = max_mtu

    def _set_bond_slaves_mtu(self, current_ifstates):
        new_ifstates = {}
        bond_desired_ifstates = (
            (ifname, ifstate)
            for ifname, ifstate in self._ifaces_state.items()
            if (
                ifstate.get(Interface.TYPE) == InterfaceType.BOND
                or (
                    current_ifstates.get(ifname, {}).get(Interface.TYPE)
                    == InterfaceType.BOND
                )
            )
        )
        for bond_ifname, bond_ifstate in bond_desired_ifstates:
            # The mtu is not defined when the bond is not part of a network.
            bond_mtu = (
                bond_ifstate.get(
                    Interface.MTU,
                    current_ifstates.get(bond_ifname, {}).get(Interface.MTU)
                    or DEFAULT_MTU,
                )
                if not is_iface_absent(bond_ifstate)
                else DEFAULT_MTU
            )
            bond_config_state = bond_ifstate.get(
                BondSchema.CONFIG_SUBTREE
            ) or current_ifstates.get(bond_ifname, {}).get(
                BondSchema.CONFIG_SUBTREE, {}
            )
            slaves = bond_config_state.get(BondSchema.PORT, ())
            for slave in slaves:
                current_slave_state = current_ifstates.get(slave)
                desired_slave_state = self._ifaces_state.get(slave)
                if desired_slave_state:
                    self._set_slaves_mtu_based_on_bond(
                        desired_slave_state, bond_mtu
                    )
                elif (
                    current_slave_state
                    and bond_mtu != current_slave_state[Interface.MTU]
                ):
                    new_ifstates[slave] = {
                        Interface.NAME: slave,
                        Interface.STATE: InterfaceState.UP,
                        Interface.MTU: bond_mtu,
                    }
        self._ifaces_state.update(new_ifstates)

    @staticmethod
    def _set_slaves_mtu_based_on_bond(slave_state, bond_mtu):
        """
        In rare cases, a bond slave may be also a base of a VLAN.
        For such cases, choose the highest MTU between the bond one and the one
        that is already specified on the slave.
        Note: It assumes that the slave has been assigned a mtu based on the
        VLAN/s defined over it.
        Note2: oVirt is not formally supporting such setups (VLAN/s over bond
        slaves), the scenario is handled here for completeness.
        """
        if not is_iface_absent(slave_state):
            slave_mtu = slave_state[Interface.MTU]
            slave_state[Interface.MTU] = max(bond_mtu, slave_mtu)


class CurrentState(object):
    def __init__(self, state):
        self._interfaces_state = self._get_interfaces_state(state)
        self._dns_state = self._get_dns_state(state)
        self._routes_state = self._get_routes_state(state)
        self._rules_state = self._get_rules_state(state)

    @property
    def interfaces_state(self):
        return self._interfaces_state

    @property
    def dns_state(self):
        return self._dns_state

    @property
    def routes_state(self):
        return self._routes_state

    @property
    def rules_state(self):
        return self._rules_state

    def filtered_interfaces(self, filter=None):
        """
        Get filtered interfaces specified by filter.

        If the filter is None or empty list the return value contains all
        available interfaces.

        :param filter: List of interface names to filter
        :type filter: list
        :returns: Dict in format {IFNAME: IFSTATE}
        :rtype: dict
        """
        filter_set = set(filter) if filter else set()
        if filter:
            return {
                ifname: ifstate
                for ifname, ifstate in self._interfaces_state.items()
                if ifname in filter_set
            }
        return self._interfaces_state

    def get_mac_address(self, interface):
        return self._interfaces_state.get(interface, {}).get(Interface.MAC)

    @staticmethod
    def _get_interfaces_state(state):
        return {
            ifstate[Interface.NAME]: ifstate
            for ifstate in state[Interface.KEY]
        }

    @staticmethod
    def _get_dns_state(state):
        return state[DNS.KEY].get(DNS.RUNNING, {}).get(DNS.SERVER, [])

    @staticmethod
    def _get_routes_state(state):
        return state[Route.KEY].get(Route.RUNNING, {})

    @staticmethod
    def _get_rules_state(state):
        return state[RouteRule.KEY].get(RouteRule.CONFIG, {})
