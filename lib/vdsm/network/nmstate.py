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

from __future__ import absolute_import
from __future__ import division

from collections import defaultdict
import itertools

import six

from vdsm.common.config import config as vdsm_config
from vdsm.network.netconfpersistence import RunningConfig
from vdsm.network.link.bond.sysfs_options import BONDING_MODES_NUMBER_TO_NAME
from vdsm.network.link.setup import parse_bond_options

try:
    from libnmstate import apply as state_apply
    from libnmstate import show as state_show
    from libnmstate.schema import Bond as BondSchema
    from libnmstate.schema import DNS
    from libnmstate.schema import Interface
    from libnmstate.schema import InterfaceIP
    from libnmstate.schema import InterfaceIPv6
    from libnmstate.schema import InterfaceState
    from libnmstate.schema import InterfaceType
    from libnmstate.schema import LinuxBridge
    from libnmstate.schema import Route
except ImportError:  # nmstate is not available
    BondSchema = None
    DNS = None
    Interface = None
    InterfaceIP = None
    InterfaceIPv6 = None
    InterfaceState = None
    InterfaceType = None
    LinuxBridge = None
    Route = None
    state_apply = None
    state_show = None


DEFAULT_MTU = 1500


def setup(desired_state, verify_change):
    state_apply(desired_state, verify_change=verify_change)


def generate_state(networks, bondings):
    """ Generate a new nmstate state given VDSM setup state format """
    rconfig = RunningConfig()
    current_ifaces_state = show_interfaces()

    bond_ifstates = Bond.generate_state(bondings, rconfig.bonds)
    net_ifstates, routes_state, dns_state = Network.generate_state(
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
        for ifname, ifstate in six.viewitems(desired_ifstates)
        if ifstate.get(Interface.STATE) == InterfaceState.ABSENT
    )
    current_remaining_ifnames = set(current_ifstates) - ifaces_to_remove

    current_remaining_vlan_ifaces = (
        (ifname, ifstate)
        for ifname, ifstate in six.viewitems(current_ifstates)
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
        if base_vlan not in desired_ifstates:
            desired_ifstates[base_vlan] = {Interface.NAME: base_vlan}
        desired_ifstates[base_vlan][Interface.MTU] = max(vlans_mtus)


def _set_bond_slaves_mtu(desired_ifstates, current_ifstates):
    new_ifstates = {}
    bond_desired_ifstates = (
        (ifname, ifstate)
        for ifname, ifstate in six.viewitems(desired_ifstates)
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
            if not _is_iface_absent(bond_ifstate)
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
    if not _is_iface_absent(slave_state):
        slave_mtu = slave_state[Interface.MTU]
        slave_state[Interface.MTU] = max(bond_mtu, slave_mtu)


def _merge_bond_and_net_ifaces_states(bond_ifstates, net_ifstates):
    for ifname, ifstate in six.viewitems(bond_ifstates):
        if not _is_iface_absent(ifstate):
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


class Bond(object):
    def __init__(self, name, attrs):
        self._name = name
        self._attrs = attrs
        self._to_remove = attrs.get('remove', False)

    @property
    def name(self):
        return self._name

    @property
    def state(self):
        iface_state = {
            Interface.NAME: self._name,
            Interface.TYPE: InterfaceType.BOND,
        }

        if self._to_remove:
            extra_state = {Interface.STATE: InterfaceState.ABSENT}
        else:
            extra_state = self._create()

        iface_state.update(extra_state)
        return iface_state

    def is_new(self, running_bonds):
        return not self._to_remove and self._name not in running_bonds

    @staticmethod
    def generate_state(bondings, running_bonds):
        bonds = (
            Bond(bondname, bondattrs)
            for bondname, bondattrs in six.viewitems(bondings)
        )
        state = {}
        for bond in bonds:
            ifstate = bond.state
            if bond.is_new(running_bonds):
                ifstate[Interface.IPV4] = {InterfaceIP.ENABLED: False}
                ifstate[Interface.IPV6] = {InterfaceIP.ENABLED: False}
            state[bond.name] = ifstate
        return state

    def _create(self):
        iface_state = {Interface.STATE: InterfaceState.UP}
        mac = self._attrs.get('hwaddr')
        if mac:
            iface_state[Interface.MAC] = mac
        bond_state = iface_state[BondSchema.CONFIG_SUBTREE] = {}
        bond_state[BondSchema.SLAVES] = sorted(self._attrs['nics'])

        options = parse_bond_options(self._attrs.get('options'))
        if options:
            bond_state[BondSchema.OPTIONS_SUBTREE] = options
        mode = self._translate_mode(mode=options.pop('mode', 'balance-rr'))
        bond_state[BondSchema.MODE] = mode
        return iface_state

    def _translate_mode(self, mode):
        return BONDING_MODES_NUMBER_TO_NAME[mode] if mode.isdigit() else mode


class NetworkConfig(object):
    def __init__(self, name, attrs):
        if not attrs:
            name = None
        self.name = name
        self.vlan = attrs.get('vlan')
        self.nic = attrs.get('nic')
        self.bond = attrs.get('bonding')
        self.bridged = attrs.get('bridged')
        self.stp = attrs.get('stp')
        self.mtu = attrs.get('mtu', DEFAULT_MTU)

        self.ipv4addr = attrs.get('ipaddr')
        self.ipv4netmask = attrs.get('netmask')
        self.dhcpv4 = attrs.get('bootproto') == 'dhcp'

        self.ipv6addr = attrs.get('ipv6addr')
        self.dhcpv6 = attrs.get('dhcpv6', False)
        self.ipv6autoconf = attrs.get('ipv6autoconf', False)
        self.ipv6gateway = attrs.get('ipv6gateway')

        self.gateway = attrs.get('gateway')
        self.default_route = attrs.get('defaultRoute')

        self.nameservers = attrs.get('nameservers')

        self.remove = attrs.get('remove', False)

        self.base_iface = self.nic or self.bond
        if self.vlan is not None:
            self.vlan_iface = '{}.{}'.format(self.base_iface, self.vlan)
        else:
            self.vlan_iface = None


class Network(object):
    def __init__(self, netconf, runconf):
        """
        netconf: NetworkConfig object, representing a requested network setup.
        runconf: NetworkConfig object, representing an existing network setup.
        current_ifaces_state: Dict, representing {name: iface-state}.
        """
        self._netconf = netconf
        self._runconf = runconf
        self._name = netconf.name
        self._to_remove = netconf.remove

        self._sb_iface_state = None
        self._vlan_iface_state = None
        self._bridge_iface_state = None
        self._route_state = None
        self._dns_state = None

        self._create_interfaces_state()
        self._create_routes()
        self._create_dns()

    @property
    def name(self):
        return self._name

    @property
    def has_vlan(self):
        if self._to_remove:
            return self._runconf.vlan is not None
        else:
            return self._netconf.vlan is not None

    @property
    def base_iface(self):
        if self._to_remove:
            return self._runconf.base_iface
        else:
            return self._netconf.base_iface

    @property
    def default_route(self):
        return self._netconf.default_route

    @property
    def to_remove(self):
        return self._to_remove

    @property
    def southbound_iface_state(self):
        return self._sb_iface_state

    @property
    def vlan_iface_state(self):
        return self._vlan_iface_state

    @property
    def bridge_iface_state(self):
        return self._bridge_iface_state

    @property
    def routes_state(self):
        return self._route_state

    @property
    def dns_state(self):
        return self._dns_state

    @property
    def is_vlan_base_iface_change(self):
        return (
            self._runconf.vlan_iface
            and self._runconf.vlan_iface != self._netconf.vlan_iface
        )

    @property
    def removed_base_iface(self):
        iface_removed = (
            self._runconf.base_iface
            and self._runconf.base_iface != self._netconf.base_iface
        ) or self.to_remove
        return self._runconf.base_iface if iface_removed else None

    @property
    def purge_old_base_iface(self):
        return self._remove_vlan_iface()

    def _create_dns(self):
        """
        The DNS state may include one of the following outputs:
            - None: The network does not include any DNS info.
            - Empty list:
                - The nameservers have been explicitly cleared.
                - The network or its d.route is removed and it has nameservers.
            - The nameservers have been explicitly set for the network.
        """
        nameservers = None
        if self._netconf.default_route:
            nameservers = self._netconf.nameservers
        elif self._runconf.default_route and self._runconf.nameservers:
            nameservers = []
        self._dns_state = nameservers

    def _create_interfaces_state(self):
        if self._to_remove:
            sb_iface = {}
            vlan_iface = self._remove_vlan_iface()
            bridge_iface = self._remove_bridge_iface()
        else:
            sb_iface, vlan_iface, bridge_iface = self._create_ifaces()
            self._add_ip(sb_iface, vlan_iface, bridge_iface)

        self._sb_iface_state = sb_iface
        self._vlan_iface_state = vlan_iface
        self._bridge_iface_state = bridge_iface

    def _create_ifaces(self):
        vlan_iface_state = self._create_vlan_iface()
        sb_iface_state = self._create_southbound_iface()
        if self._netconf.bridged:
            bridge_port = vlan_iface_state or sb_iface_state
            bridge_iface_state = self._create_bridge_iface(
                bridge_port[Interface.NAME] if bridge_port else None,
                options=self._create_bridge_options(),
            )
        else:
            bridge_iface_state = self._remove_bridge_iface()

        return sb_iface_state, vlan_iface_state, bridge_iface_state

    def _create_vlan_iface(self):
        vlan = self._netconf.vlan
        if vlan is not None:
            base_iface = self._netconf.base_iface
            return {
                'vlan': {'id': vlan, 'base-iface': base_iface},
                Interface.NAME: self._netconf.vlan_iface,
                Interface.TYPE: InterfaceType.VLAN,
                Interface.STATE: InterfaceState.UP,
                Interface.MTU: self._netconf.mtu,
            }
        return {}

    def _create_southbound_iface(self):
        return (
            {
                Interface.NAME: self._netconf.base_iface,
                Interface.STATE: InterfaceState.UP,
                Interface.MTU: self._netconf.mtu,
            }
            if self._netconf.base_iface
            else {}
        )

    def _create_bridge_iface(self, port, options=None):
        port_state = [{LinuxBridge.Port.NAME: port}] if port else []
        bridge_state = {
            Interface.NAME: self._name,
            Interface.TYPE: InterfaceType.LINUX_BRIDGE,
            Interface.STATE: InterfaceState.UP,
            Interface.MTU: self._netconf.mtu,
            LinuxBridge.CONFIG_SUBTREE: {LinuxBridge.PORT_SUBTREE: port_state},
        }
        if options:
            brstate = bridge_state[LinuxBridge.CONFIG_SUBTREE]
            brstate[LinuxBridge.OPTIONS_SUBTREE] = options
        return bridge_state

    def _create_bridge_options(self):
        return {
            LinuxBridge.STP_SUBTREE: {
                LinuxBridge.STP.ENABLED: self._netconf.stp
            }
        }

    def _add_ip(self, sb_iface, vlan_iface, bridge_iface):
        ipv4_state = self._create_ipv4()
        ipv6_state = self._create_ipv6()
        port_iface = vlan_iface or sb_iface
        if self._netconf.bridged:
            # Bridge port IP stacks need to be disabled.
            if port_iface:
                port_iface[Interface.IPV4] = self._create_ipv4(enabled=False)
                port_iface[Interface.IPV6] = self._create_ipv6(enabled=False)
            bridge_iface[Interface.IPV4] = ipv4_state
            bridge_iface[Interface.IPV6] = ipv6_state
        elif port_iface:
            port_iface[Interface.IPV4] = ipv4_state
            port_iface[Interface.IPV6] = ipv6_state

    def _create_ipv4(self, enabled=True):
        ipstate = {InterfaceIP.ENABLED: enabled}
        if enabled:
            if self._netconf.ipv4addr:
                ipv4_address = self._create_static_ipv4_address()
                ipstate[InterfaceIP.ADDRESS] = ipv4_address
                ipstate[InterfaceIP.DHCP] = False
            elif self._netconf.dhcpv4:
                ipstate.update(self._create_dynamic_ipv4())
            else:
                ipstate[InterfaceIP.ENABLED] = False

        return ipstate

    def _create_static_ipv4_address(self):
        return [
            {
                InterfaceIP.ADDRESS_IP: self._netconf.ipv4addr,
                InterfaceIP.ADDRESS_PREFIX_LENGTH: _get_ipv4_prefix_from_mask(
                    self._netconf.ipv4netmask
                ),
            }
        ]

    def _create_dynamic_ipv4(self):
        return {
            InterfaceIP.DHCP: self._netconf.dhcpv4,
            InterfaceIP.AUTO_DNS: self.default_route,
            InterfaceIP.AUTO_GATEWAY: self.default_route,
            InterfaceIP.AUTO_ROUTES: self.default_route,
        }

    def _create_ipv6(self, enabled=True):
        ipstate = {InterfaceIP.ENABLED: enabled}
        if enabled:
            if self._netconf.ipv6addr:
                ipv6_address = self._create_static_ipv6_address()
                ipstate[InterfaceIP.ADDRESS] = ipv6_address
                ipstate[InterfaceIP.DHCP] = False
                ipstate[InterfaceIPv6.AUTOCONF] = False
            elif self._netconf.dhcpv6 or self._netconf.ipv6autoconf:
                ipstate.update(self._create_dynamic_ipv6())
            else:
                ipstate[InterfaceIP.ENABLED] = False

        return ipstate

    def _create_static_ipv6_address(self):
        address, prefix = self._netconf.ipv6addr.split('/')
        return [
            {
                InterfaceIP.ADDRESS_IP: address,
                InterfaceIP.ADDRESS_PREFIX_LENGTH: int(prefix),
            }
        ]

    def _create_dynamic_ipv6(self):
        return {
            InterfaceIP.DHCP: self._netconf.dhcpv6,
            InterfaceIPv6.AUTOCONF: self._netconf.ipv6autoconf,
            InterfaceIP.AUTO_DNS: self.default_route,
            InterfaceIP.AUTO_GATEWAY: self.default_route,
            InterfaceIP.AUTO_ROUTES: self.default_route,
        }

    def _create_routes(self):
        routes = Routes(self._netconf, self._runconf)
        self._route_state = routes.state

    def _remove_vlan_iface(self):
        if self._runconf.vlan_iface:
            return {
                Interface.NAME: self._runconf.vlan_iface,
                Interface.STATE: InterfaceState.ABSENT,
            }
        return {}

    def _remove_bridge_iface(self):
        if self._runconf.bridged:
            return {
                Interface.NAME: self._name,
                Interface.STATE: InterfaceState.ABSENT,
            }
        return {}

    @staticmethod
    def generate_state(networks, running_networks, current_ifaces_state):
        nets = [
            Network(
                NetworkConfig(netname, netattrs),
                NetworkConfig(netname, running_networks.get(netname, {})),
            )
            for netname, netattrs in six.viewitems(networks)
        ]
        interfaces_state = Network._merge_sb_ifaces(nets)
        routes_state = []
        dns_state = {}
        droute_net = None
        for net in nets:
            if net.vlan_iface_state:
                vlan_iface_name = net.vlan_iface_state[Interface.NAME]
                interfaces_state[vlan_iface_name] = net.vlan_iface_state
            if net.bridge_iface_state:
                bridge_iface_name = net.bridge_iface_state[Interface.NAME]
                interfaces_state[bridge_iface_name] = net.bridge_iface_state

            routes_state += net.routes_state
            net_dns_state = net.dns_state
            if net_dns_state is not None:
                dns_state[net.name] = net_dns_state
            if net.default_route:
                droute_net = net
        for net in nets:
            if _disable_base_iface_ip_stack(
                net,
                interfaces_state.get(net.base_iface),
                current_ifaces_state.get(net.base_iface),
            ):
                interfaces_state[net.base_iface].update(
                    {
                        Interface.IPV4: {InterfaceIP.ENABLED: False},
                        Interface.IPV6: {InterfaceIP.ENABLED: False},
                    }
                )

            if _init_base_iface(net, interfaces_state):
                curr_mtu = current_ifaces_state[net.base_iface][Interface.MTU]
                interfaces_state[net.base_iface] = {
                    Interface.NAME: net.base_iface,
                    Interface.STATE: InterfaceState.UP,
                    Interface.MTU: curr_mtu,
                    Interface.IPV4: {InterfaceIP.ENABLED: False},
                    Interface.IPV6: {InterfaceIP.ENABLED: False},
                }
        _purge_orphaned_base_vlan_ifaces(nets, interfaces_state)
        _reset_iface_mtus_on_network_dettach(
            nets, running_networks, interfaces_state, current_ifaces_state
        )

        # FIXME: Workaround to nmstate limitation when DNS entries are defined.
        if not droute_net:
            ifnet = _get_default_route_interface(running_networks)
            if ifnet and ifnet not in interfaces_state:
                interfaces_state[ifnet] = {Interface.NAME: ifnet}

        return interfaces_state, routes_state, dns_state

    @staticmethod
    def _merge_sb_ifaces(nets):
        sb_ifaces_by_name = Network._collect_sb_ifaces_by_name(nets)

        sb_ifaces = {}
        for ifname, ifstates in six.viewitems(sb_ifaces_by_name):
            sb_ifaces[ifname] = {}
            for ifstate in ifstates:
                # Combine southbound interface states into one.
                # These may appear as a base of a vlan on one hand and a
                # bridgeless network top interface on the other hand.
                sb_ifaces[ifname].update(ifstate)

            mtu = max(ifstate[Interface.MTU] for ifstate in ifstates)
            sb_ifaces[ifname][Interface.MTU] = mtu
        return sb_ifaces

    @staticmethod
    def _collect_sb_ifaces_by_name(nets):
        sb_ifaces_by_name = defaultdict(list)
        for net in nets:
            if net.southbound_iface_state:
                sb_name = net.southbound_iface_state[Interface.NAME]
                sb_ifaces_by_name[sb_name].append(net.southbound_iface_state)
        return sb_ifaces_by_name


class Routes(object):
    IPV4 = 4
    IPV6 = 6

    def __init__(self, netconf, runconf):
        self._netconf = netconf
        self._runconf = runconf
        self._state = self._create_routes()

    @property
    def state(self):
        return self._state

    def _create_routes(self):
        routes = []
        next_hop = self._get_next_hop_interface()
        for family in (Routes.IPV4, Routes.IPV6):
            gateway = self._get_gateway_by_ip_family(self._netconf, family)
            runconf_gateway = self._get_gateway_by_ip_family(
                self._runconf, family
            )
            if gateway:
                routes.append(self._create_route(next_hop, gateway, family))
                if self._gateway_has_changed(runconf_gateway, gateway):
                    routes.append(
                        self._create_remove_default_route(
                            next_hop, runconf_gateway, family
                        )
                    )
            elif self._should_remove_def_route(family):
                routes.append(
                    self._create_remove_default_route(
                        next_hop, runconf_gateway, family
                    )
                )

        return routes

    def _create_route(self, next_hop, gateway, family):
        if self._netconf.default_route:
            return self._create_add_default_route(next_hop, gateway, family)
        else:
            return self._create_remove_default_route(next_hop, gateway, family)

    def _get_next_hop_interface(self):
        if self._netconf.bridged:
            return self._netconf.name
        return self._netconf.vlan_iface or self._netconf.base_iface

    def _should_remove_def_route(self, family):
        dhcp = (
            self._netconf.dhcpv4
            if family == self.IPV4
            else self._netconf.dhcpv6
        )
        return (
            not self._netconf.remove
            and self._get_gateway_by_ip_family(self._runconf, family)
            and self._runconf.default_route
            and (dhcp or not self._netconf.default_route)
        )

    @staticmethod
    def _gateway_has_changed(runconf_gateway, netconf_gateway):
        return runconf_gateway and runconf_gateway != netconf_gateway

    @staticmethod
    def _get_gateway_by_ip_family(source, family):
        return source.gateway if family == Routes.IPV4 else source.ipv6gateway

    @staticmethod
    def _create_add_default_route(next_hop_interface, gateway, family):
        destination = '0.0.0.0/0' if family == Routes.IPV4 else '::/0'
        return {
            Route.NEXT_HOP_ADDRESS: gateway,
            Route.NEXT_HOP_INTERFACE: next_hop_interface,
            Route.DESTINATION: destination,
            Route.TABLE_ID: Route.USE_DEFAULT_ROUTE_TABLE,
        }

    @staticmethod
    def _create_remove_default_route(next_hop_interface, gateway, family):
        destination = '0.0.0.0/0' if family == Routes.IPV4 else '::/0'
        return {
            Route.NEXT_HOP_ADDRESS: gateway,
            Route.NEXT_HOP_INTERFACE: next_hop_interface,
            Route.DESTINATION: destination,
            Route.STATE: Route.STATE_ABSENT,
            Route.TABLE_ID: Route.USE_DEFAULT_ROUTE_TABLE,
        }


def _merge_state(interfaces_state, routes_state, dns_state):
    interfaces = [ifstate for ifstate in six.viewvalues(interfaces_state)]
    state = {
        Interface.KEY: sorted(interfaces, key=lambda d: d[Interface.NAME])
    }
    if routes_state:
        state.update(routes={Route.CONFIG: routes_state})
    if dns_state:
        nameservers = itertools.chain.from_iterable(
            ns for ns in six.viewvalues(dns_state)
        )
        state[DNS.KEY] = {DNS.CONFIG: {DNS.SERVER: list(nameservers)}}
    return state


def _get_default_route_interface(running_networks):
    for netname, attrs in six.viewitems(running_networks):
        netrun = NetworkConfig(netname, attrs)
        if netrun.default_route:
            if netrun.bridged:
                ifnet = netrun.name
            else:
                ifnet = netrun.vlan_iface or netrun.base_iface
            return ifnet
    return None


def _get_ipv4_prefix_from_mask(ipv4netmask):
    prefix = 0
    for octet in ipv4netmask.split('.'):
        onebits = str(bin(int(octet))).strip('0b').rstrip('0')
        prefix += len(onebits)
    return prefix


def _disable_base_iface_ip_stack(net, desired_base_state, current_base_state):
    return (
        not net.to_remove
        and net.has_vlan
        and not _is_iface_up(current_base_state)
        and Interface.IPV4 not in desired_base_state
        and Interface.IPV6 not in desired_base_state
    )


def _is_iface_absent(ifstate):
    return ifstate and ifstate.get(Interface.STATE) == InterfaceState.ABSENT


def _is_iface_up(ifstate):
    return ifstate and ifstate[Interface.STATE] == InterfaceState.UP


def _init_base_iface(net, interfaces_state):
    return (
        net.to_remove
        and net.base_iface
        and not (net.has_vlan or net.base_iface in interfaces_state)
    )


def _purge_orphaned_base_vlan_ifaces(nets, interfaces_state):
    base_ifaces_state_which_changed = (
        net.purge_old_base_iface
        for net in nets
        if net.is_vlan_base_iface_change
    )
    changed_ifaces_purge_state = {
        ifstate[Interface.NAME]: ifstate
        for ifstate in base_ifaces_state_which_changed
    }
    purged_ifaces = set(changed_ifaces_purge_state) - set(interfaces_state)
    for ifname in purged_ifaces:
        interfaces_state[ifname] = changed_ifaces_purge_state[ifname]


def _reset_iface_mtus_on_network_dettach(
    networks, running_networks, interfaces_state, current_interface_state
):
    running_nets_by_base_iface = _nets_by_base_iface(running_networks)
    for net in networks:
        removed_base_iface = net.removed_base_iface
        if not _is_stale_iface(removed_base_iface, running_nets_by_base_iface):
            continue

        current_state = current_interface_state.get(removed_base_iface, {})
        if _is_default_mtu(current_state):
            continue

        sb_iface_state = interfaces_state.get(removed_base_iface)
        if not sb_iface_state:
            sb_iface_state = {Interface.NAME: removed_base_iface}
            interfaces_state[removed_base_iface] = sb_iface_state

        sb_iface_state[Interface.MTU] = DEFAULT_MTU


def _nets_by_base_iface(networks):
    nets_by_base_iface = defaultdict(list)
    for name, net_attrs in networks.items():
        base_iface = NetworkConfig(name, net_attrs).base_iface
        if base_iface:
            nets_by_base_iface[base_iface].append(name)
    return nets_by_base_iface


def _is_stale_iface(removed_base_iface, nets_by_base_iface):
    return (
        removed_base_iface
        and len(nets_by_base_iface.get(removed_base_iface, ())) == 1
    )


def _is_default_mtu(state):
    return state.get(Interface.MTU, DEFAULT_MTU) == DEFAULT_MTU
