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
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA
#
# Refer to the README and COPYING files for full details of the license
#

from __future__ import absolute_import
from __future__ import division

import itertools

import six

from vdsm.common.config import config as vdsm_config
from vdsm.network.netconfpersistence import RunningConfig
from vdsm.network.link.bond.sysfs_options import BONDING_MODES_NUMBER_TO_NAME
from vdsm.network.link.setup import parse_bond_options

try:
    from libnmstate import netapplier
    from libnmstate import netinfo
    from libnmstate import schema
    from libnmstate.schema import DNS
    from libnmstate.schema import Interface
    from libnmstate.schema import InterfaceIP
    from libnmstate.schema import InterfaceIPv6
    from libnmstate.schema import InterfaceState
    from libnmstate.schema import InterfaceType
    from libnmstate.schema import LinuxBridge
    from libnmstate.schema import Route
    from libnmstate.schema import VLAN
except ImportError:  # nmstate is not available
    netapplier = None
    DNS = None
    Interface = None
    InterfaceIP = None
    InterfaceIPv6 = None
    InterfaceState = None
    InterfaceType = None
    LinuxBridge = None
    Route = None
    VLAN = None
    schema = None


def setup(desired_state, verify_change):
    netapplier.apply(desired_state, verify_change)


def generate_state(networks, bondings):
    """ Generate a new nmstate state given VDSM setup state format """
    rconfig = RunningConfig()

    bond_ifstates = Bond.generate_state(bondings, rconfig.bonds)
    net_ifstates, routes_state, dns_state = Network.generate_state(
        networks, rconfig.networks)

    for ifname, ifstate in six.viewitems(bond_ifstates):
        if ifstate.get(Interface.STATE) != InterfaceState.ABSENT:
            ifstate.update(net_ifstates.get(ifname, {}))
    net_ifstates.update(bond_ifstates)

    return _merge_state(net_ifstates, routes_state, dns_state)


def show_interfaces(filter=None):
    net_info = netinfo.show()
    filter_set = set(filter) if filter else set()
    return {
        ifstate[Interface.NAME]: ifstate
        for ifstate in net_info[Interface.KEY]
        if ifstate[Interface.NAME] in filter_set
    }


def is_nmstate_backend():
    return vdsm_config.getboolean('vars', 'net_nmstate_enabled')


def is_dhcp_enabled(ifstate, family):
    family_info = ifstate[family]
    return family_info[InterfaceIP.ENABLED] and family_info[InterfaceIP.DHCP]


def is_autoconf_enabled(ifstate):
    family_info = ifstate[Interface.IPV6]
    return (family_info[InterfaceIP.ENABLED] and
            family_info[InterfaceIPv6.AUTOCONF])


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
        bond_state = iface_state[schema.Bond.CONFIG_SUBTREE] = {}
        bond_state[schema.Bond.SLAVES] = sorted(self._attrs['nics'])

        options = parse_bond_options(self._attrs.get('options'))
        if options:
            bond_state[schema.Bond.OPTIONS_SUBTREE] = options
        mode = self._translate_mode(mode=options.pop('mode', 'balance-rr'))
        bond_state[schema.Bond.MODE] = mode
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

        self.ipv4addr = attrs.get('ipaddr')
        self.ipv4netmask = attrs.get('netmask')
        self.dhcpv4 = attrs.get('bootproto') == 'dhcp'

        self.ipv6addr = attrs.get('ipv6addr')
        self.dhcpv6 = attrs.get('dhcpv6', False)
        self.ipv6autoconf = attrs.get('ipv6autoconf', False)

        self.gateway = attrs.get('gateway')
        self.default_route = attrs.get('defaultRoute')

        self.nameservers = attrs.get('nameservers')

        self.remove = attrs.get('remove', False)

        self.base_iface = self.nic or self.bond
        if self.vlan:
            self.vlan_iface = '{}.{}'.format(self.base_iface, self.vlan)
        else:
            self.vlan_iface = None


class Network(object):
    def __init__(self, netconf, runconf):
        """
        netconf: NetworkConfig object, representing a requested network setup.
        runconf: NetworkConfig object, representing an existing network setup.
        """
        self._netconf = netconf
        self._runconf = runconf
        self._name = netconf.name
        self._to_remove = netconf.remove

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
    def interfaces_state(self):
        if self._to_remove:
            return {}, self._remove_vlan_iface(), self._remove_bridge_iface()

        sb_iface, vlan_iface, bridge_iface = ifaces = self._create_ifaces()
        self._add_ip(sb_iface, vlan_iface, bridge_iface)
        return ifaces

    @property
    def routes_state(self):
        return self._create_routes()

    @property
    def dns_state(self):
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
        return nameservers

    def _create_ifaces(self):
        vlan_iface_state = self._create_vlan_iface()
        sb_iface_state = self._create_southbound_iface()
        bridge_iface_state = {}
        if self._netconf.bridged:
            bridge_port = vlan_iface_state or sb_iface_state
            bridge_iface_state = self._create_bridge_iface(
                bridge_port[Interface.NAME],
                options=self._create_bridge_options()
            )

        return sb_iface_state, vlan_iface_state, bridge_iface_state

    def _create_vlan_iface(self):
        vlan = self._netconf.vlan
        if vlan:
            base_iface = self._netconf.base_iface
            return {
                VLAN.CONFIG_SUBTREE: {
                    VLAN.ID: vlan,
                    VLAN.BASE_IFACE: base_iface,
                },
                Interface.NAME: self._netconf.vlan_iface,
                Interface.TYPE: InterfaceType.VLAN,
                Interface.STATE: InterfaceState.UP,
            }
        return {}

    def _create_southbound_iface(self):
        return {
            Interface.NAME: self._netconf.base_iface,
            Interface.STATE: InterfaceState.UP,
        }

    def _create_bridge_iface(self, port, options=None):
        bridge_state = {
            Interface.NAME: self._name,
            Interface.TYPE: InterfaceType.LINUX_BRIDGE,
            Interface.STATE: InterfaceState.UP,
            LinuxBridge.CONFIG_SUBTREE:
                {LinuxBridge.PORT_SUBTREE: [{LinuxBridge.PORT_NAME: port}]}
        }
        if options:
            brstate = bridge_state[LinuxBridge.CONFIG_SUBTREE]
            brstate[LinuxBridge.OPTIONS_SUBTREE] = options
        return bridge_state

    def _create_bridge_options(self):
        return {
            LinuxBridge.STP_SUBTREE:
                {LinuxBridge.STP_ENABLED: self._netconf.stp}
        }

    def _add_ip(self, sb_iface, vlan_iface, bridge_iface):
        ipv4_state = self._create_ipv4()
        ipv6_state = self._create_ipv6()
        port_iface = vlan_iface or sb_iface
        if self._netconf.bridged:
            # Bridge port IP stacks need to be disabled.
            port_iface[Interface.IPV4] = self._create_ipv4(enabled=False)
            port_iface[Interface.IPV6] = self._create_ipv6(enabled=False)
            bridge_iface[Interface.IPV4] = ipv4_state
            bridge_iface[Interface.IPV6] = ipv6_state
        else:
            port_iface[Interface.IPV4] = ipv4_state
            port_iface[Interface.IPV6] = ipv6_state

    def _create_ipv4(self, enabled=True):
        ipstate = {InterfaceIP.ENABLED: enabled}
        if enabled:
            if self._netconf.ipv4addr:
                ipv4_address = self._create_static_ipv4_address()
                ipstate[InterfaceIP.ADDRESS] = ipv4_address
            elif self._netconf.dhcpv4:
                ipstate.update(self._create_dynamic_ipv4())
            else:
                ipstate[InterfaceIP.ENABLED] = False

        return ipstate

    def _create_static_ipv4_address(self):
        return [
            {
                InterfaceIP.ADDRESS_IP: self._netconf.ipv4addr,
                InterfaceIP.ADDRESS_PREFIX_LENGTH:
                    _get_ipv4_prefix_from_mask(self._netconf.ipv4netmask),
            }
        ]

    def _create_dynamic_ipv4(self):
        return {
            InterfaceIP.DHCP: self._netconf.dhcpv4,
            InterfaceIP.AUTO_DNS: self.default_route,
            InterfaceIP.AUTO_GATEWAY: self.default_route,
            InterfaceIP.AUTO_ROUTES: self.default_route
        }

    def _create_ipv6(self, enabled=True):
        ipstate = {InterfaceIP.ENABLED: enabled}
        if enabled:
            if self._netconf.ipv6addr:
                ipv6_address = self._create_static_ipv6_address()
                ipstate[InterfaceIP.ADDRESS] = ipv6_address
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
                InterfaceIP.ADDRESS_PREFIX_LENGTH: int(prefix)
            }
        ]

    def _create_dynamic_ipv6(self):
        return {
            InterfaceIP.DHCP: self._netconf.dhcpv6,
            InterfaceIPv6.AUTOCONF: self._netconf.ipv6autoconf,
            InterfaceIP.AUTO_DNS: self.default_route,
            InterfaceIP.AUTO_GATEWAY: self.default_route,
            InterfaceIP.AUTO_ROUTES: self.default_route
        }

    def _create_routes(self):
        routes = []
        if self._netconf.gateway:
            next_hop = self.get_next_hop_interface()
            if self._netconf.default_route:
                routes.append(self._create_add_default_route(next_hop))
            else:
                routes.append(self._create_remove_default_route(next_hop))
        return routes

    def _create_add_default_route(self, next_hop_interface):
        return {
            Route.NEXT_HOP_ADDRESS: self._netconf.gateway,
            Route.NEXT_HOP_INTERFACE: next_hop_interface,
            Route.DESTINATION: '0.0.0.0/0',
            Route.TABLE_ID: Route.USE_DEFAULT_ROUTE_TABLE
        }

    def _create_remove_default_route(self, next_hop_interface):
        return {
            Route.NEXT_HOP_ADDRESS: self._netconf.gateway,
            Route.NEXT_HOP_INTERFACE: next_hop_interface,
            Route.DESTINATION: '0.0.0.0/0',
            Route.STATE: Route.STATE_ABSENT,
            Route.TABLE_ID: Route.USE_DEFAULT_ROUTE_TABLE
        }

    def get_next_hop_interface(self):
        if self._netconf.bridged:
            return self._name
        return self._netconf.vlan_iface or self._netconf.base_iface

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
    def generate_state(networks, running_networks):
        nets = [
            Network(
                NetworkConfig(netname, netattrs),
                NetworkConfig(netname, running_networks.get(netname, {}))
            )
            for netname, netattrs in six.viewitems(networks)
        ]
        interfaces_state = {}
        routes_state = []
        dns_state = {}
        droute_net = None
        for net in nets:
            interfaces_state.update(
                {
                    iface[Interface.NAME]: iface
                    for iface in net.interfaces_state
                    if iface
                }
            )
            routes_state += net.routes_state
            net_dns_state = net.dns_state
            if net_dns_state is not None:
                dns_state[net.name] = net_dns_state
            if net.default_route:
                droute_net = net
        for net in nets:
            init_base_iface = (
                net.to_remove and
                not (net.has_vlan or net.base_iface in interfaces_state)
            )
            if init_base_iface:
                interfaces_state[net.base_iface] = {
                    Interface.NAME: net.base_iface,
                    Interface.STATE: InterfaceState.UP,
                    Interface.IPV4: {InterfaceIP.ENABLED: False},
                    Interface.IPV6: {InterfaceIP.ENABLED: False}
                }
        # FIXME: Workaround to nmstate limitation when DNS entries are defined.
        if not droute_net:
            ifnet = _get_default_route_interface(running_networks)
            if ifnet and ifnet not in interfaces_state:
                interfaces_state[ifnet] = {Interface.NAME: ifnet}

        return interfaces_state, routes_state, dns_state


def _merge_state(interfaces_state, routes_state, dns_state):
    interfaces = [ifstate for ifstate in six.viewvalues(interfaces_state)]
    state = {
        Interface.KEY: sorted(interfaces, key=lambda d: d[Interface.NAME])
    }
    if routes_state:
        state.update(routes={Route.CONFIG: routes_state})
    if dns_state:
        nameservers = itertools.chain.from_iterable(
            ns for ns in six.viewvalues(dns_state))
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
