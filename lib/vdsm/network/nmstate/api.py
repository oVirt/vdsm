# Copyright 2019-2020 Red Hat, Inc.
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
import itertools

from vdsm.common.config import config as vdsm_config
from vdsm.network.netconfpersistence import RunningConfig

from .bond import Bond
from .bridge_util import DEFAULT_MTU
from .bridge_util import is_iface_absent
from .linux_bridge import LinuxBridgeNetwork
from .schema import BondSchema
from .schema import DNS
from .schema import Interface
from .schema import InterfaceIP
from .schema import InterfaceIPv6
from .schema import InterfaceState
from .schema import InterfaceType
from .schema import Route

try:
    from libnmstate import apply as state_apply
    from libnmstate import show as state_show
except ImportError:  # nmstate is not available
    state_apply = None
    state_show = None


def setup(desired_state, verify_change):
    state_apply(desired_state, verify_change=verify_change)


def generate_state(networks, bondings):
    """ Generate a new nmstate state given VDSM setup state format """
    rconfig = RunningConfig()
    current_ifaces_state = show_interfaces()

    bond_ifstates = Bond.generate_state(bondings, rconfig.bonds)
    net_ifstates, routes_state, dns_state = LinuxBridgeNetwork.generate_state(
        networks, rconfig.networks, current_ifaces_state
    )

    ifstates = _merge_bond_and_net_ifaces_states(bond_ifstates, net_ifstates)

    _set_vlans_base_mtu(ifstates, current_ifaces_state)
    _set_bond_slaves_mtu(ifstates, current_ifaces_state)

    return _merge_state(ifstates, routes_state, dns_state)


def _set_vlans_base_mtu(desired_ifstates, current_ifstates):
    vlans_base_mtus = defaultdict(list)
    ifaces_to_remove = set(
        ifname
        for ifname, ifstate in desired_ifstates.items()
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
        base_vlan = ifstate['vlan']['base-iface']
        if vlan_iface in desired_ifstates:
            vlan_mtu = desired_ifstates[vlan_iface][Interface.MTU]
        else:
            vlan_mtu = ifstate[Interface.MTU]
        if base_vlan in current_remaining_ifnames:
            vlans_base_mtus[base_vlan].append(vlan_mtu)
        if base_vlan in desired_ifstates:
            vlans_base_mtus[base_vlan].append(
                desired_ifstates[base_vlan].get(Interface.MTU, 0)
            )

    for base_vlan, vlans_mtus in vlans_base_mtus.items():
        max_mtu = max(vlans_mtus)
        # No need to enforce the MTU if it didn't change
        if (
            base_vlan not in desired_ifstates
            and base_vlan in current_ifstates
            and current_ifstates[base_vlan][Interface.MTU] == max_mtu
        ):
            continue

        if base_vlan not in desired_ifstates:
            desired_ifstates[base_vlan] = {Interface.NAME: base_vlan}
        desired_ifstates[base_vlan][Interface.MTU] = max_mtu


def _set_bond_slaves_mtu(desired_ifstates, current_ifstates):
    new_ifstates = {}
    bond_desired_ifstates = (
        (ifname, ifstate)
        for ifname, ifstate in desired_ifstates.items()
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
        slaves = bond_config_state.get(BondSchema.SLAVES, ())
        for slave in slaves:
            current_slave_state = current_ifstates.get(slave)
            desired_slave_state = desired_ifstates.get(slave)
            if desired_slave_state:
                _set_slaves_mtu_based_on_bond(desired_slave_state, bond_mtu)
            elif (
                current_slave_state
                and bond_mtu != current_slave_state[Interface.MTU]
            ):
                new_ifstates[slave] = {
                    Interface.NAME: slave,
                    Interface.STATE: InterfaceState.UP,
                    Interface.MTU: bond_mtu,
                }
    desired_ifstates.update(new_ifstates)


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


def _merge_bond_and_net_ifaces_states(bond_ifstates, net_ifstates):
    for ifname, ifstate in bond_ifstates.items():
        if not is_iface_absent(ifstate):
            ifstate.update(net_ifstates.get(ifname, {}))
    net_ifstates.update(bond_ifstates)
    return net_ifstates


def show_interfaces(filter=None):
    net_info = state_show()
    filter_set = set(filter) if filter else set()
    ifaces = (
        (ifstate[Interface.NAME], ifstate)
        for ifstate in net_info[Interface.KEY]
    )
    if filter:
        return {
            ifname: ifstate
            for ifname, ifstate in ifaces
            if ifname in filter_set
        }
    else:
        return {ifname: ifstate for ifname, ifstate in ifaces}


def show_nameservers():
    state = state_show()
    return state[DNS.KEY].get(DNS.RUNNING, {}).get(DNS.SERVER, [])


def is_nmstate_backend():
    return vdsm_config.getboolean('vars', 'net_nmstate_enabled')


def is_dhcp_enabled(ifstate, family):
    family_info = ifstate[family]
    return family_info[InterfaceIP.ENABLED] and family_info[InterfaceIP.DHCP]


def is_autoconf_enabled(ifstate):
    family_info = ifstate[Interface.IPV6]
    return (
        family_info[InterfaceIP.ENABLED]
        and family_info[InterfaceIPv6.AUTOCONF]
    )


def _merge_state(interfaces_state, routes_state, dns_state):
    interfaces = [ifstate for ifstate in interfaces_state.values()]
    state = {
        Interface.KEY: sorted(interfaces, key=lambda d: d[Interface.NAME])
    }
    if routes_state:
        state.update(routes={Route.CONFIG: routes_state})
    if dns_state:
        nameservers = itertools.chain.from_iterable(
            ns for ns in dns_state.values()
        )
        state[DNS.KEY] = {DNS.CONFIG: {DNS.SERVER: list(nameservers)}}
    return state
