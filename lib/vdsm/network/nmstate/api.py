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

from vdsm.network.netconfpersistence import RunningConfig

from .bond import Bond
from .bridge_util import DEFAULT_MTU
from .bridge_util import is_autoconf_enabled as util_is_autoconf_enabled
from .bridge_util import is_dhcp_enabled as util_is_dhcp_enabled
from .bridge_util import is_iface_absent
from .bridge_util import SwitchType
from .bridge_util import translate_config
from .linux_bridge import LinuxBridgeNetwork as LinuxBrNet
from .ovs.info import OvsInfo
from .ovs.info import OvsNetInfo
from .ovs.network import generate_state as ovs_generate_state
from .schema import BondSchema
from .schema import DNS
from .schema import Interface
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
    current_ifaces_state = get_interfaces(state_show())

    ovs_nets, linux_br_nets = _split_switch_type(networks, rconfig.networks)
    ovs_bonds, linux_br_bonds = _split_switch_type(bondings, rconfig.bonds)
    ovs_requested = ovs_nets or ovs_bonds
    linux_br_requested = linux_br_nets or linux_br_bonds

    bond_ifstates = Bond.generate_state(bondings, rconfig.bonds)

    if ovs_requested:
        routes_state = None
        dns_state = None
        net_ifstates = ovs_generate_state(
            networks, rconfig.networks, current_ifaces_state
        )
    else:
        net_ifstates, routes_state, dns_state = LinuxBrNet.generate_state(
            networks, rconfig.networks, current_ifaces_state
        )

    ifstates = _merge_bond_and_net_ifaces_states(bond_ifstates, net_ifstates)

    if linux_br_requested:
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


def get_interfaces(state, filter=None):
    filter_set = set(filter) if filter else set()
    ifaces = (
        (ifstate[Interface.NAME], ifstate) for ifstate in state[Interface.KEY]
    )
    if filter:
        return {
            ifname: ifstate
            for ifname, ifstate in ifaces
            if ifname in filter_set
        }
    else:
        return {ifname: ifstate for ifname, ifstate in ifaces}


def get_nameservers(state):
    return state[DNS.KEY].get(DNS.RUNNING, {}).get(DNS.SERVER, [])


def get_routes(state):
    return state[Route.KEY].get(Route.RUNNING, {})


# TODO remove this once every reference is resolved
def is_nmstate_backend():
    return True


def is_dhcp_enabled(ifstate, family):
    family_info = ifstate[family]
    return util_is_dhcp_enabled(family_info)


def is_autoconf_enabled(ifstate):
    family_info = ifstate[Interface.IPV6]
    return util_is_autoconf_enabled(family_info)


def ovs_netinfo(base_netinfo, running_networks, state):
    rnets_config = translate_config(running_networks)
    current_iface_state = get_interfaces(state)
    current_routes_state = get_routes(state)
    ovs_info = OvsInfo(rnets_config, current_iface_state)
    netinfo = OvsNetInfo(
        ovs_info,
        base_netinfo,
        rnets_config,
        current_iface_state,
        current_routes_state,
    )
    netinfo.create_netinfo()


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


def _split_switch_type(desired_config, running_config):
    ovs = []
    linux_bridge = []
    for name, attrs in desired_config.items():
        if _to_remove(attrs):
            switch = _get_switch_type(running_config[name])
        else:
            switch = _get_switch_type(attrs)

        if switch == SwitchType.LINUX_BRIDGE:
            linux_bridge.append(name)
        elif switch == SwitchType.OVS:
            ovs.append(name)

    return ovs, linux_bridge


def _to_remove(attrs):
    return attrs.get('remove', False)


def _get_switch_type(attrs):
    return attrs.get('switch')
