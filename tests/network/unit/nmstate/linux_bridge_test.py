#
# Copyright 2020-2021 Red Hat, Inc.
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
#

from vdsm.network import nmstate

from .testlib import (
    DEFAULT_MTU,
    DNS_SERVERS1,
    DNS_SERVERS2,
    IFACE0,
    IFACE1,
    IFACE2,
    IPv4_ADDRESS1,
    IPv4_ADDRESS2,
    IPv4_GATEWAY1,
    IPv4_GATEWAY2,
    IPv4_NETMASK1,
    IPv4_NETMASK2,
    IPv4_PREFIX1,
    IPv4_PREFIX2,
    IPv6_ADDRESS1,
    IPv6_ADDRESS2,
    IPv6_GATEWAY1,
    IPv6_GATEWAY2,
    IPv6_PREFIX1,
    IPv6_PREFIX2,
    TESTBOND0,
    TESTNET1,
    TESTNET2,
    VLAN101,
    VLAN102,
    create_bond_iface_state,
    create_bonding_config,
    create_bridge_iface_state,
    create_dynamic_ip_configuration,
    create_ethernet_iface_state,
    create_ipv4_state,
    create_ipv6_state,
    create_network_config,
    create_static_ip_configuration,
    create_vlan_iface_state,
    disable_iface_ip,
    generate_bridge_options,
    get_routes_config,
    parametrize_bridged,
    parametrize_vlanned,
    sort_by_name,
)


@parametrize_bridged
def test_translate_nets_without_ip(bridged):
    networks = {
        TESTNET1: create_network_config('nic', IFACE0, bridged),
        TESTNET2: create_network_config('nic', IFACE1, bridged),
    }
    state = nmstate.generate_state(networks=networks, bondings={})

    eth0_state = create_ethernet_iface_state(IFACE0)
    eth1_state = create_ethernet_iface_state(IFACE1)

    disable_iface_ip(eth0_state, eth1_state)

    expected_state = {nmstate.Interface.KEY: [eth0_state, eth1_state]}
    if bridged:
        bridge1_state = create_bridge_iface_state(
            TESTNET1,
            IFACE0,
            options=generate_bridge_options(stp_enabled=False),
        )
        bridge2_state = create_bridge_iface_state(
            TESTNET2,
            IFACE1,
            options=generate_bridge_options(stp_enabled=False),
        )
        disable_iface_ip(bridge1_state, bridge2_state)
        expected_state[nmstate.Interface.KEY].extend(
            [bridge1_state, bridge2_state]
        )
    sort_by_name(expected_state[nmstate.Interface.KEY])
    assert expected_state == state


@parametrize_bridged
def test_translate_nets_with_ip(bridged):
    networks = {
        TESTNET1: create_network_config(
            'nic',
            IFACE0,
            bridged,
            static_ip_configuration=create_static_ip_configuration(
                IPv4_ADDRESS1, IPv4_NETMASK1, IPv6_ADDRESS1, IPv6_PREFIX1
            ),
        ),
        TESTNET2: create_network_config(
            'nic',
            IFACE1,
            bridged,
            static_ip_configuration=create_static_ip_configuration(
                IPv4_ADDRESS2, IPv4_NETMASK2, IPv6_ADDRESS2, IPv6_PREFIX2
            ),
        ),
    }
    state = nmstate.generate_state(networks=networks, bondings={})

    eth0_state = create_ethernet_iface_state(IFACE0)
    eth1_state = create_ethernet_iface_state(IFACE1)

    ip0_state = create_ipv4_state(IPv4_ADDRESS1, IPv4_PREFIX1)
    ip0_state.update(create_ipv6_state(IPv6_ADDRESS1, IPv6_PREFIX1))
    ip1_state = create_ipv4_state(IPv4_ADDRESS2, IPv4_PREFIX2)
    ip1_state.update(create_ipv6_state(IPv6_ADDRESS2, IPv6_PREFIX2))

    expected_state = {nmstate.Interface.KEY: [eth0_state, eth1_state]}
    if bridged:
        disable_iface_ip(eth0_state, eth1_state)
        bridge1_state = create_bridge_iface_state(
            TESTNET1,
            IFACE0,
            options=generate_bridge_options(stp_enabled=False),
        )
        bridge2_state = create_bridge_iface_state(
            TESTNET2,
            IFACE1,
            options=generate_bridge_options(stp_enabled=False),
        )
        bridge1_state.update(ip0_state)
        bridge2_state.update(ip1_state)
        expected_state[nmstate.Interface.KEY].extend(
            [bridge1_state, bridge2_state]
        )
    else:
        eth0_state.update(ip0_state)
        eth1_state.update(ip1_state)

    sort_by_name(expected_state[nmstate.Interface.KEY])
    assert expected_state == state


class TestBondedNetwork(object):
    def test_translate_empty_networks_and_bonds(self):
        state = nmstate.generate_state(networks={}, bondings={})

        assert {nmstate.Interface.KEY: []} == state

    @parametrize_bridged
    def test_translate_net_with_ip_on_bond(self, bridged):
        networks = {
            TESTNET1: create_network_config(
                'bonding',
                TESTBOND0,
                bridged,
                static_ip_configuration=create_static_ip_configuration(
                    IPv4_ADDRESS1, IPv4_NETMASK1, IPv6_ADDRESS1, IPv6_PREFIX1
                ),
            )
        }
        bondings = {TESTBOND0: {'nics': [IFACE0, IFACE1], 'switch': 'legacy'}}
        state = nmstate.generate_state(networks=networks, bondings=bondings)

        bond0_state = create_bond_iface_state(
            TESTBOND0, 'balance-rr', [IFACE0, IFACE1]
        )

        ip_state = create_ipv4_state(IPv4_ADDRESS1, IPv4_PREFIX1)
        ip_state.update(create_ipv6_state(IPv6_ADDRESS1, IPv6_PREFIX1))

        expected_state = {nmstate.Interface.KEY: [bond0_state]}
        if bridged:
            disable_iface_ip(bond0_state)
            bridge1_state = create_bridge_iface_state(
                TESTNET1,
                TESTBOND0,
                options=generate_bridge_options(stp_enabled=False),
            )
            bridge1_state.update(ip_state)
            expected_state[nmstate.Interface.KEY].extend([bridge1_state])
        else:
            bond0_state.update(ip_state)

        assert expected_state == state

    @parametrize_bridged
    def test_translate_net_with_dynamic_ip(self, bridged):
        networks = {
            TESTNET1: create_network_config(
                'bonding',
                TESTBOND0,
                bridged,
                dynamic_ip_configuration=create_dynamic_ip_configuration(
                    dhcpv4=True, dhcpv6=True, ipv6autoconf=True
                ),
            )
        }
        bondings = {TESTBOND0: {'nics': [IFACE0, IFACE1], 'switch': 'legacy'}}
        state = nmstate.generate_state(networks=networks, bondings=bondings)

        bond0_state = create_bond_iface_state(
            TESTBOND0, 'balance-rr', [IFACE0, IFACE1]
        )

        ip_state = create_ipv4_state(dynamic=True)
        ip_state.update(create_ipv6_state(dynamic=True))

        expected_state = {nmstate.Interface.KEY: [bond0_state]}
        if bridged:
            disable_iface_ip(bond0_state)
            bridge1_state = create_bridge_iface_state(
                TESTNET1,
                TESTBOND0,
                options=generate_bridge_options(stp_enabled=False),
            )
            bridge1_state.update(ip_state)
            expected_state[nmstate.Interface.KEY].extend([bridge1_state])
        else:
            bond0_state.update(ip_state)

        assert expected_state == state

    @parametrize_bridged
    def test_translate_net_with_ip_on_vlan_on_bond(self, bridged):
        networks = {
            TESTNET1: create_network_config(
                'bonding',
                TESTBOND0,
                bridged,
                static_ip_configuration=create_static_ip_configuration(
                    IPv4_ADDRESS1, IPv4_NETMASK1, IPv6_ADDRESS1, IPv6_PREFIX1
                ),
                vlan=VLAN101,
            )
        }
        bondings = {TESTBOND0: {'nics': [IFACE0, IFACE1], 'switch': 'legacy'}}
        state = nmstate.generate_state(networks=networks, bondings=bondings)

        bond0_state = create_bond_iface_state(
            TESTBOND0, 'balance-rr', [IFACE0, IFACE1]
        )

        disable_iface_ip(bond0_state)

        vlan101_state = create_vlan_iface_state(TESTBOND0, VLAN101)
        ip1_state = create_ipv4_state(IPv4_ADDRESS1, IPv4_PREFIX1)
        ip1_state.update(create_ipv6_state(IPv6_ADDRESS1, IPv6_PREFIX1))

        expected_state = {nmstate.Interface.KEY: [bond0_state, vlan101_state]}
        if bridged:
            disable_iface_ip(vlan101_state)
            bridge1_state = create_bridge_iface_state(
                TESTNET1,
                vlan101_state[nmstate.Interface.NAME],
                options=generate_bridge_options(stp_enabled=False),
            )
            bridge1_state.update(ip1_state)
            expected_state[nmstate.Interface.KEY].extend([bridge1_state])
        else:
            vlan101_state.update(ip1_state)
        assert expected_state == state

    @parametrize_bridged
    def test_translate_remove_net_on_bond(
        self, rconfig_mock, bridged, current_state_mock
    ):
        current_ifaces_states = current_state_mock[nmstate.Interface.KEY]
        current_ifaces_states.append(
            {
                nmstate.Interface.NAME: TESTBOND0,
                nmstate.Interface.TYPE: nmstate.InterfaceType.BOND,
                nmstate.Interface.STATE: nmstate.InterfaceState.UP,
                nmstate.Interface.MTU: DEFAULT_MTU,
                nmstate.Interface.IPV4: {nmstate.InterfaceIP.ENABLED: False},
                nmstate.Interface.IPV6: {nmstate.InterfaceIP.ENABLED: False},
            }
        )
        rconfig_mock.networks = {
            TESTNET1: {
                'bonding': TESTBOND0,
                'bridged': bridged,
                'switch': 'legacy',
                'defaultRoute': False,
            }
        }
        networks = {TESTNET1: {'remove': True}}
        state = nmstate.generate_state(networks=networks, bondings={})

        expected_state = {
            nmstate.Interface.KEY: [
                {
                    nmstate.Interface.NAME: TESTBOND0,
                    nmstate.Interface.STATE: nmstate.InterfaceState.UP,
                    nmstate.Interface.MTU: DEFAULT_MTU,
                    nmstate.Interface.IPV4: {
                        nmstate.InterfaceIP.ENABLED: False
                    },
                    nmstate.Interface.IPV6: {
                        nmstate.InterfaceIP.ENABLED: False
                    },
                }
            ]
        }
        if bridged:
            expected_state[nmstate.Interface.KEY].append(
                {
                    nmstate.Interface.NAME: TESTNET1,
                    nmstate.Interface.STATE: nmstate.InterfaceState.ABSENT,
                }
            )
        sort_by_name(expected_state[nmstate.Interface.KEY])
        assert expected_state == state

    @parametrize_bridged
    def test_translate_remove_vlan_net_on_bond(self, rconfig_mock, bridged):
        rconfig_mock.networks = {
            TESTNET1: {
                'bonding': TESTBOND0,
                'bridged': bridged,
                'vlan': VLAN101,
                'switch': 'legacy',
                'defaultRoute': False,
            }
        }
        networks = {TESTNET1: {'remove': True}}
        state = nmstate.generate_state(networks=networks, bondings={})

        expected_state = {
            nmstate.Interface.KEY: [
                {
                    nmstate.Interface.NAME: TESTBOND0 + '.' + str(VLAN101),
                    nmstate.Interface.STATE: nmstate.InterfaceState.ABSENT,
                }
            ]
        }
        if bridged:
            expected_state[nmstate.Interface.KEY].extend(
                [
                    {
                        nmstate.Interface.NAME: TESTNET1,
                        nmstate.Interface.STATE: nmstate.InterfaceState.ABSENT,
                    }
                ]
            )
        sort_by_name(expected_state[nmstate.Interface.KEY])
        assert expected_state == state

    @parametrize_bridged
    def test_translate_remove_bridged_net_and_bond(
        self, rconfig_mock, bridged, current_state_mock
    ):
        current_ifaces_states = current_state_mock[nmstate.Interface.KEY]
        current_ifaces_states.append(
            {
                nmstate.Interface.NAME: TESTBOND0,
                nmstate.Interface.TYPE: nmstate.InterfaceType.BOND,
                nmstate.Interface.STATE: nmstate.InterfaceState.UP,
                nmstate.Interface.MTU: DEFAULT_MTU,
                nmstate.Interface.IPV4: {nmstate.InterfaceIP.ENABLED: False},
                nmstate.Interface.IPV6: {nmstate.InterfaceIP.ENABLED: False},
            }
        )
        rconfig_mock.networks = {
            TESTNET1: {
                'bonding': TESTBOND0,
                'bridged': bridged,
                'switch': 'legacy',
                'defaultRoute': False,
            }
        }
        rconfig_mock.bonds = {TESTBOND0: {'nics': [], 'switch': 'legacy'}}

        networks = {TESTNET1: {'remove': True}}
        bondings = {TESTBOND0: {'remove': True}}

        state = nmstate.generate_state(networks=networks, bondings=bondings)

        expected_state = {
            nmstate.Interface.KEY: [
                {
                    nmstate.Interface.NAME: TESTBOND0,
                    nmstate.Interface.TYPE: nmstate.InterfaceType.BOND,
                    nmstate.Interface.STATE: nmstate.InterfaceState.ABSENT,
                }
            ]
        }
        if bridged:
            expected_state[nmstate.Interface.KEY].append(
                {
                    nmstate.Interface.NAME: TESTNET1,
                    nmstate.Interface.STATE: nmstate.InterfaceState.ABSENT,
                }
            )
        assert expected_state == state


@parametrize_bridged
def test_translate_remove_nets(rconfig_mock, bridged, current_state_mock):
    current_ifaces_states = current_state_mock[nmstate.Interface.KEY]
    current_ifaces_states += [
        {
            nmstate.Interface.NAME: IFACE0,
            nmstate.Interface.TYPE: nmstate.InterfaceType.ETHERNET,
            nmstate.Interface.STATE: nmstate.InterfaceState.UP,
            nmstate.Interface.MTU: DEFAULT_MTU,
            nmstate.Interface.IPV4: {nmstate.InterfaceIP.ENABLED: False},
            nmstate.Interface.IPV6: {nmstate.InterfaceIP.ENABLED: False},
        },
        {
            nmstate.Interface.NAME: IFACE1,
            nmstate.Interface.TYPE: nmstate.InterfaceType.ETHERNET,
            nmstate.Interface.STATE: nmstate.InterfaceState.UP,
            nmstate.Interface.MTU: DEFAULT_MTU,
            nmstate.Interface.IPV4: {nmstate.InterfaceIP.ENABLED: False},
            nmstate.Interface.IPV6: {nmstate.InterfaceIP.ENABLED: False},
        },
    ]
    rconfig_mock.networks = {
        TESTNET1: {
            'nic': IFACE0,
            'bridged': bridged,
            'switch': 'legacy',
            'defaultRoute': False,
        },
        TESTNET2: {
            'nic': IFACE1,
            'bridged': bridged,
            'switch': 'legacy',
            'defaultRoute': False,
        },
    }
    networks = {TESTNET1: {'remove': True}, TESTNET2: {'remove': True}}
    state = nmstate.generate_state(networks=networks, bondings={})

    eth0_state = create_ethernet_iface_state(IFACE0)
    eth1_state = create_ethernet_iface_state(IFACE1)

    disable_iface_ip(eth0_state, eth1_state)

    expected_state = {nmstate.Interface.KEY: [eth0_state, eth1_state]}
    if bridged:
        expected_state[nmstate.Interface.KEY].extend(
            [
                {
                    nmstate.Interface.NAME: TESTNET1,
                    nmstate.Interface.STATE: nmstate.InterfaceState.ABSENT,
                },
                {
                    nmstate.Interface.NAME: TESTNET2,
                    nmstate.Interface.STATE: nmstate.InterfaceState.ABSENT,
                },
            ]
        )
    sort_by_name(expected_state[nmstate.Interface.KEY])
    assert expected_state == state


@parametrize_bridged
def test_translate_add_network_with_default_route(bridged):
    networks = {
        TESTNET1: create_network_config(
            'nic',
            IFACE0,
            bridged=bridged,
            static_ip_configuration=create_static_ip_configuration(
                IPv4_ADDRESS1, IPv4_NETMASK1, IPv6_ADDRESS1, IPv6_PREFIX1
            ),
            default_route=True,
            gateway=IPv4_GATEWAY1,
            ipv6gateway=IPv6_GATEWAY1,
        )
    }
    state = nmstate.generate_state(networks=networks, bondings={})

    eth0_state = create_ethernet_iface_state(IFACE0)
    ip0_state = create_ipv4_state(IPv4_ADDRESS1, IPv4_PREFIX1)
    ip0_state.update(create_ipv6_state(IPv6_ADDRESS1, IPv6_PREFIX1))

    expected_state = {nmstate.Interface.KEY: [eth0_state]}

    if bridged:
        disable_iface_ip(eth0_state)
        bridge1_state = create_bridge_iface_state(
            TESTNET1,
            IFACE0,
            options=generate_bridge_options(stp_enabled=False),
        )
        bridge1_state.update(ip0_state)
        expected_state[nmstate.Interface.KEY].append(bridge1_state)
        if_with_default_route = TESTNET1
    else:
        eth0_state.update(ip0_state)
        if_with_default_route = IFACE0

    expected_state[nmstate.Route.KEY] = {
        nmstate.Route.CONFIG: get_routes_config(
            IPv4_GATEWAY1, if_with_default_route, IPv6_GATEWAY1
        )
    }
    assert state == expected_state


@parametrize_bridged
def test_translate_remove_network_with_default_route(
    rconfig_mock, bridged, current_state_mock
):
    current_ifaces_states = current_state_mock[nmstate.Interface.KEY]
    current_ifaces_states.append(
        {
            nmstate.Interface.NAME: IFACE0,
            nmstate.Interface.TYPE: nmstate.InterfaceType.ETHERNET,
            nmstate.Interface.STATE: nmstate.InterfaceState.UP,
            nmstate.Interface.MTU: DEFAULT_MTU,
            nmstate.Interface.IPV4: {nmstate.InterfaceIP.ENABLED: False},
            nmstate.Interface.IPV6: {nmstate.InterfaceIP.ENABLED: False},
        }
    )
    rconfig_mock.networks = {
        TESTNET1: {
            'nic': IFACE0,
            'bridged': bridged,
            'switch': 'legacy',
            'defaultRoute': True,
            'gateway': IPv4_GATEWAY1,
        }
    }
    networks = {TESTNET1: {'remove': True}}
    state = nmstate.generate_state(networks=networks, bondings={})

    eth0_state = create_ethernet_iface_state(IFACE0)
    disable_iface_ip(eth0_state)

    expected_state = {nmstate.Interface.KEY: [eth0_state]}

    if bridged:
        expected_state[nmstate.Interface.KEY].append(
            {
                nmstate.Interface.NAME: TESTNET1,
                nmstate.Interface.STATE: nmstate.InterfaceState.ABSENT,
            }
        )

    assert expected_state == state


@parametrize_bridged
def test_update_gateway_with_default_route(rconfig_mock, bridged):
    rconfig_mock.networks = {
        TESTNET1: {
            'nic': IFACE0,
            'ipaddr': IPv4_ADDRESS1,
            'netmask': IPv4_NETMASK1,
            'gateway': IPv4_GATEWAY1,
            'ipv6addr': IPv6_ADDRESS1 + '/' + str(IPv6_PREFIX1),
            'ipv6gateway': IPv6_GATEWAY1,
            'defaultRoute': True,
            'switch': 'legacy',
        }
    }
    networks = {
        TESTNET1: create_network_config(
            'nic',
            IFACE0,
            bridged=bridged,
            static_ip_configuration=create_static_ip_configuration(
                IPv4_ADDRESS1, IPv4_NETMASK1, IPv6_ADDRESS1, IPv6_PREFIX1
            ),
            default_route=True,
            gateway=IPv4_GATEWAY2,
            ipv6gateway=IPv6_GATEWAY2,
        )
    }
    state = nmstate.generate_state(networks=networks, bondings={})

    eth0_state = create_ethernet_iface_state(IFACE0)
    ip0_state = create_ipv4_state(IPv4_ADDRESS1, IPv4_PREFIX1)
    ip0_state.update(create_ipv6_state(IPv6_ADDRESS1, IPv6_PREFIX1))

    expected_state = {nmstate.Interface.KEY: [eth0_state]}

    if bridged:
        disable_iface_ip(eth0_state)
        bridge1_state = create_bridge_iface_state(
            TESTNET1,
            IFACE0,
            options=generate_bridge_options(stp_enabled=False),
        )
        bridge1_state.update(ip0_state)
        expected_state[nmstate.Interface.KEY].append(bridge1_state)
        if_with_default_route = TESTNET1
    else:
        eth0_state.update(ip0_state)
        if_with_default_route = IFACE0
    routes = get_routes_config(
        IPv4_GATEWAY2, if_with_default_route, IPv6_GATEWAY2
    )

    routes.extend(
        get_routes_config(
            IPv4_GATEWAY1,
            if_with_default_route,
            IPv6_GATEWAY1,
            state=nmstate.Route.STATE_ABSENT,
        )
    )
    routes.sort(key=lambda route: len(route[nmstate.Route.NEXT_HOP_ADDRESS]))
    expected_state[nmstate.Route.KEY] = {nmstate.Route.CONFIG: routes}

    assert state == expected_state


@parametrize_bridged
def test_translate_default_route_network_static_to_dhcp(rconfig_mock, bridged):
    rconfig_mock.networks = {
        TESTNET1: {
            'nic': IFACE0,
            'bridged': bridged,
            'switch': 'legacy',
            'defaultRoute': True,
            'gateway': IPv4_GATEWAY1,
            'ipaddr': IPv4_ADDRESS1,
            'netmask': IPv4_NETMASK1,
            'ipv6addr': IPv6_ADDRESS1 + '/' + str(IPv6_PREFIX1),
            'ipv6gateway': IPv6_GATEWAY1,
        }
    }
    networks = {
        TESTNET1: create_network_config(
            'nic',
            IFACE0,
            bridged,
            dynamic_ip_configuration=create_dynamic_ip_configuration(
                dhcpv4=True, dhcpv6=True, ipv6autoconf=True
            ),
            default_route=True,
        )
    }
    state = nmstate.generate_state(networks=networks, bondings={})

    eth0_state = create_ethernet_iface_state(IFACE0)
    ip0_state = create_ipv4_state(dynamic=True, default_route=True)
    ip0_state.update(create_ipv6_state(dynamic=True, default_route=True))

    expected_state = {nmstate.Interface.KEY: [eth0_state]}

    if bridged:
        disable_iface_ip(eth0_state)
        bridge1_state = create_bridge_iface_state(
            TESTNET1,
            IFACE0,
            options=generate_bridge_options(stp_enabled=False),
        )
        bridge1_state.update(ip0_state)
        expected_state[nmstate.Interface.KEY].append(bridge1_state)
        if_with_default_route = TESTNET1
    else:
        eth0_state.update(ip0_state)
        if_with_default_route = IFACE0

    expected_state[nmstate.Route.KEY] = {
        nmstate.Route.CONFIG: get_routes_config(
            IPv4_GATEWAY1,
            if_with_default_route,
            IPv6_GATEWAY1,
            state=nmstate.Route.STATE_ABSENT,
        )
    }
    assert state == expected_state


@parametrize_bridged
def test_translate_remove_default_route_from_network(rconfig_mock, bridged):
    rconfig_mock.networks = {
        TESTNET1: {
            'nic': IFACE0,
            'bridged': bridged,
            'switch': 'legacy',
            'defaultRoute': True,
            'gateway': IPv4_GATEWAY1,
            'ipaddr': IPv4_ADDRESS1,
            'netmask': IPv4_NETMASK1,
            'ipv6addr': IPv6_ADDRESS1 + '/' + str(IPv6_PREFIX1),
            'ipv6gateway': IPv6_GATEWAY1,
        }
    }
    networks = {
        TESTNET1: create_network_config(
            'nic',
            IFACE0,
            bridged,
            static_ip_configuration=create_static_ip_configuration(
                IPv4_ADDRESS1, IPv4_NETMASK1, IPv6_ADDRESS1, IPv6_PREFIX1
            ),
            default_route=False,
        )
    }
    state = nmstate.generate_state(networks=networks, bondings={})

    eth0_state = create_ethernet_iface_state(IFACE0)
    ip0_state = create_ipv4_state(IPv4_ADDRESS1, IPv4_PREFIX1)
    ip0_state.update(create_ipv6_state(IPv6_ADDRESS1, IPv6_PREFIX1))

    expected_state = {nmstate.Interface.KEY: [eth0_state]}

    if bridged:
        disable_iface_ip(eth0_state)
        bridge1_state = create_bridge_iface_state(
            TESTNET1,
            IFACE0,
            options=generate_bridge_options(stp_enabled=False),
        )
        bridge1_state.update(ip0_state)
        expected_state[nmstate.Interface.KEY].append(bridge1_state)
        if_with_default_route = TESTNET1
    else:
        eth0_state.update(ip0_state)
        if_with_default_route = IFACE0

    expected_state[nmstate.Route.KEY] = {
        nmstate.Route.CONFIG: get_routes_config(
            IPv4_GATEWAY1,
            if_with_default_route,
            IPv6_GATEWAY1,
            state=nmstate.Route.STATE_ABSENT,
        )
    }
    assert state == expected_state


def test_translate_add_network_with_default_route_on_vlan_interface():
    networks = {
        TESTNET1: create_network_config(
            'nic',
            IFACE0,
            bridged=False,
            static_ip_configuration=create_static_ip_configuration(
                IPv4_ADDRESS1, IPv4_NETMASK1, IPv6_ADDRESS1, IPv6_PREFIX1
            ),
            default_route=True,
            gateway=IPv4_GATEWAY1,
            vlan=VLAN101,
        )
    }
    state = nmstate.generate_state(networks=networks, bondings={})

    vlan101_state = create_vlan_iface_state(IFACE0, VLAN101)
    ip0_state = create_ipv4_state(IPv4_ADDRESS1, IPv4_PREFIX1)
    ip0_state.update(create_ipv6_state(IPv6_ADDRESS1, IPv6_PREFIX1))
    vlan101_state.update(ip0_state)

    vlan_base_state = create_ethernet_iface_state(IFACE0)
    vlan_base_state.update(create_ipv4_state())
    vlan_base_state.update(create_ipv6_state())
    expected_state = {nmstate.Interface.KEY: [vlan_base_state, vlan101_state]}

    expected_state[nmstate.Route.KEY] = {
        nmstate.Route.CONFIG: get_routes_config(
            IPv4_GATEWAY1, vlan101_state[nmstate.Interface.NAME]
        )
    }
    assert expected_state == state


def test_update_network_from_bridged_to_bridgeless(rconfig_mock):
    networks = {TESTNET1: create_network_config('nic', IFACE0, bridged=True)}
    rconfig_mock.networks = networks

    updated_network = {
        TESTNET1: create_network_config('nic', IFACE0, bridged=False)
    }
    state = nmstate.generate_state(networks=updated_network, bondings={})

    eth0_state = create_ethernet_iface_state(IFACE0)
    disable_iface_ip(eth0_state)

    remove_bridge_state = create_bridge_iface_state(
        TESTNET1, port=None, state=nmstate.InterfaceState.ABSENT
    )

    expected_state = {nmstate.Interface.KEY: [eth0_state, remove_bridge_state]}

    assert expected_state == state


class TestDns(object):
    def test_dns_add_network_with_default_route(self):
        networks = {
            TESTNET1: create_network_config(
                'nic',
                IFACE0,
                bridged=True,
                default_route=True,
                gateway=IPv4_GATEWAY1,
                nameservers=DNS_SERVERS1,
            )
        }

        state = nmstate.generate_state(networks=networks, bondings={})

        expected_state = {
            nmstate.DNS.CONFIG: {nmstate.DNS.SERVER: DNS_SERVERS1}
        }

        assert expected_state == state[nmstate.DNS.KEY]

    def test_dns_add_network_with_default_route_and_empty_dns(self):
        networks = {
            TESTNET1: create_network_config(
                'nic',
                IFACE0,
                bridged=True,
                default_route=True,
                gateway=IPv4_GATEWAY1,
                nameservers=[],
            )
        }

        state = nmstate.generate_state(networks=networks, bondings={})

        expected_state = {nmstate.DNS.CONFIG: {nmstate.DNS.SERVER: []}}

        assert expected_state == state[nmstate.DNS.KEY]

    def test_dns_add_network_without_default_route(self):
        networks = {
            TESTNET1: create_network_config('nic', IFACE0, bridged=True)
        }

        state = nmstate.generate_state(networks=networks, bondings={})

        assert state.get(nmstate.DNS.KEY) is None

    def test_dns_remove_network_with_default_route(
        self, rconfig_mock, current_state_mock
    ):
        current_ifaces_states = current_state_mock[nmstate.Interface.KEY]
        current_ifaces_states.append(
            {
                nmstate.Interface.NAME: IFACE0,
                nmstate.Interface.TYPE: nmstate.InterfaceType.ETHERNET,
                nmstate.Interface.STATE: nmstate.InterfaceState.UP,
                nmstate.Interface.MTU: DEFAULT_MTU,
                nmstate.Interface.IPV4: {nmstate.InterfaceIP.ENABLED: False},
                nmstate.Interface.IPV6: {nmstate.InterfaceIP.ENABLED: False},
            }
        )
        rconfig_networks = {
            TESTNET1: create_network_config(
                'nic',
                IFACE0,
                bridged=True,
                default_route=True,
                gateway=IPv4_GATEWAY1,
                nameservers=DNS_SERVERS1,
            )
        }
        rconfig_mock.networks = rconfig_networks

        networks = {TESTNET1: {'remove': True}}

        state = nmstate.generate_state(networks=networks, bondings={})

        expected_state = {nmstate.DNS.CONFIG: {nmstate.DNS.SERVER: []}}

        assert expected_state == state[nmstate.DNS.KEY]

    def test_dns_replace_network_with_default_route(self, rconfig_mock):
        rconfig_networks = {
            TESTNET1: create_network_config(
                'nic',
                IFACE0,
                bridged=True,
                default_route=True,
                gateway=IPv4_GATEWAY1,
                nameservers=DNS_SERVERS1,
            )
        }
        rconfig_mock.networks = rconfig_networks
        networks = {
            TESTNET2: create_network_config(
                'nic',
                IFACE1,
                bridged=True,
                default_route=True,
                gateway=IPv4_GATEWAY1,
                nameservers=DNS_SERVERS2,
            )
        }

        state = nmstate.generate_state(networks=networks, bondings={})

        expected_state = {
            nmstate.DNS.CONFIG: {nmstate.DNS.SERVER: DNS_SERVERS2}
        }

        assert expected_state == state[nmstate.DNS.KEY]

    def test_dns_add_network_with_dynamic_ip(self):
        networks = {
            TESTNET1: create_network_config(
                'nic',
                IFACE0,
                bridged=False,
                default_route=True,
                dynamic_ip_configuration=create_dynamic_ip_configuration(
                    dhcpv4=True, dhcpv6=False, ipv6autoconf=False
                ),
            )
        }
        iface_state = create_ethernet_iface_state(name=IFACE0)
        iface_state.update(create_ipv4_state(dynamic=True, default_route=True))
        iface_state.update(create_ipv6_state())
        expected_state = {nmstate.Interface.KEY: [iface_state]}

        state = nmstate.generate_state(networks=networks, bondings={})

        assert expected_state == state

    def test_dns_add_network_with_dynamic_ip_and_static_dns(self):
        networks = {
            TESTNET1: create_network_config(
                'nic',
                IFACE0,
                bridged=False,
                default_route=True,
                dynamic_ip_configuration=create_dynamic_ip_configuration(
                    dhcpv4=True, dhcpv6=False, ipv6autoconf=False
                ),
                nameservers=DNS_SERVERS1,
            )
        }

        iface_state = create_ethernet_iface_state(name=IFACE0)
        iface_state.update(
            create_ipv4_state(dynamic=True, default_route=True, auto_dns=False)
        )
        iface_state.update(create_ipv6_state())
        expected_state = {
            nmstate.Interface.KEY: [iface_state],
            nmstate.DNS.KEY: {
                nmstate.DNS.CONFIG: {nmstate.DNS.SERVER: DNS_SERVERS1}
            },
        }

        state = nmstate.generate_state(networks=networks, bondings={})

        assert expected_state == state


class TestMtu(object):
    def test_single_network_with_specific_mtu(self, current_state_mock):
        mtu = 2000
        current_ifaces_states = current_state_mock[nmstate.Interface.KEY]
        current_ifaces_states += self._create_bond_slaves_states(
            DEFAULT_MTU, include_type=True
        )

        networks = {
            TESTNET1: create_network_config(
                'bonding', TESTBOND0, bridged=True, vlan=VLAN101, mtu=mtu
            )
        }
        bondings = create_bonding_config(slaves=[IFACE0, IFACE1])
        state = nmstate.generate_state(networks=networks, bondings=bondings)

        expected_slaves_states = self._create_bond_slaves_states(mtu)
        expected_bond_state = self._create_bond_state(mtu)
        expected_vlan_state = self._create_vlan_state(VLAN101, mtu)
        expected_bridge_state = self._create_bridge_state(
            TESTNET1, expected_vlan_state[nmstate.Interface.NAME], mtu
        )
        expected_state = {
            nmstate.Interface.KEY: expected_slaves_states
            + [expected_bond_state, expected_vlan_state, expected_bridge_state]
        }
        sort_by_name(expected_state[nmstate.Interface.KEY])
        assert expected_state == state

    def test_two_networks_with_different_mtu_on_same_southbound_iface(
        self, current_state_mock
    ):
        mtu_max = 2000
        mtu_min = 1600
        current_ifaces_states = current_state_mock[nmstate.Interface.KEY]
        current_ifaces_states += self._create_bond_slaves_states(
            DEFAULT_MTU, include_type=True
        )

        networks = {
            TESTNET1: create_network_config(
                'bonding', TESTBOND0, bridged=True, vlan=VLAN101, mtu=mtu_max
            ),
            TESTNET2: create_network_config(
                'bonding', TESTBOND0, bridged=True, vlan=VLAN102, mtu=mtu_min
            ),
        }
        bondings = create_bonding_config(slaves=[IFACE0, IFACE1])
        state = nmstate.generate_state(networks=networks, bondings=bondings)

        expected_slaves_states = self._create_bond_slaves_states(mtu_max)
        expected_bond_state = self._create_bond_state(mtu_max)
        expected_vlan101_state = self._create_vlan_state(VLAN101, mtu_max)
        expected_vlan102_state = self._create_vlan_state(VLAN102, mtu_min)
        expected_bridge1_state = self._create_bridge_state(
            TESTNET1, expected_vlan101_state[nmstate.Interface.NAME], mtu_max
        )
        expected_bridge2_state = self._create_bridge_state(
            TESTNET2, expected_vlan102_state[nmstate.Interface.NAME], mtu_min
        )
        expected_state = {
            nmstate.Interface.KEY: expected_slaves_states
            + [
                expected_bond_state,
                expected_vlan101_state,
                expected_vlan102_state,
                expected_bridge1_state,
                expected_bridge2_state,
            ]
        }
        sort_by_name(expected_state[nmstate.Interface.KEY])
        assert expected_state == state

    def test_add_network_with_higher_mtu(self, current_state_mock):
        mtu = DEFAULT_MTU + 500
        current_ifaces_states = current_state_mock[nmstate.Interface.KEY]
        current_ifaces_states += self._create_bond_with_slaves_ifaces_states(
            DEFAULT_MTU, include_type=True
        )
        current_ifaces_states += self._create_vlan_with_bridge_ifaces_states(
            VLAN101, TESTNET1, DEFAULT_MTU
        )

        networks = {
            TESTNET2: create_network_config(
                'bonding', TESTBOND0, bridged=True, vlan=VLAN102, mtu=mtu
            )
        }
        state = nmstate.generate_state(networks=networks, bondings={})

        expected_ifaces_states = self._create_vlan_with_bridge_ifaces_states(
            VLAN102, TESTNET2, mtu
        )
        expected_ifaces_states += self._create_bond_slaves_states(mtu)
        expected_bond_state = {
            nmstate.Interface.NAME: TESTBOND0,
            nmstate.Interface.STATE: nmstate.InterfaceState.UP,
            nmstate.Interface.MTU: mtu,
        }
        expected_ifaces_states.append(expected_bond_state)

        sort_by_name(expected_ifaces_states)
        assert {nmstate.Interface.KEY: expected_ifaces_states} == state

    def test_add_network_with_lower_mtu(self, current_state_mock):
        mtu = DEFAULT_MTU - 500
        current_ifaces_states = current_state_mock[nmstate.Interface.KEY]
        current_ifaces_states += self._create_bond_with_slaves_ifaces_states(
            DEFAULT_MTU, include_type=True
        )
        current_ifaces_states += self._create_vlan_with_bridge_ifaces_states(
            VLAN101, TESTNET1, DEFAULT_MTU
        )

        networks = {
            TESTNET2: create_network_config(
                'bonding', TESTBOND0, bridged=True, vlan=VLAN102, mtu=mtu
            )
        }
        state = nmstate.generate_state(networks=networks, bondings={})

        expected_ifaces_states = self._create_vlan_with_bridge_ifaces_states(
            VLAN102, TESTNET2, mtu
        )
        expected_bond_state = {
            nmstate.Interface.NAME: TESTBOND0,
            nmstate.Interface.STATE: nmstate.InterfaceState.UP,
            nmstate.Interface.MTU: DEFAULT_MTU,
        }
        expected_ifaces_states.append(expected_bond_state)

        sort_by_name(expected_ifaces_states)
        assert {nmstate.Interface.KEY: expected_ifaces_states} == state

    def test_remove_network_with_highest_mtu(
        self, rconfig_mock, current_state_mock
    ):
        high_mtu = DEFAULT_MTU + 500
        current_ifaces_states = current_state_mock[nmstate.Interface.KEY]
        current_ifaces_states += self._create_bond_with_slaves_ifaces_states(
            high_mtu, include_type=True
        )
        current_ifaces_states += self._create_vlan_with_bridge_ifaces_states(
            VLAN101, TESTNET1, DEFAULT_MTU
        )
        current_ifaces_states += self._create_vlan_with_bridge_ifaces_states(
            VLAN102, TESTNET2, high_mtu
        )

        rconfig_mock.networks = {
            TESTNET1: create_network_config(
                'bonding',
                TESTBOND0,
                bridged=True,
                vlan=VLAN101,
                mtu=DEFAULT_MTU,
            ),
            TESTNET2: create_network_config(
                'bonding', TESTBOND0, bridged=True, vlan=VLAN102, mtu=high_mtu
            ),
        }
        networks = {TESTNET2: {'remove': True}}
        state = nmstate.generate_state(networks=networks, bondings={})

        expected_ifaces_states = [
            {
                nmstate.Interface.NAME: TESTNET2,
                nmstate.Interface.STATE: nmstate.InterfaceState.ABSENT,
            },
            {
                nmstate.Interface.NAME: '{}.{}'.format(TESTBOND0, VLAN102),
                nmstate.Interface.STATE: nmstate.InterfaceState.ABSENT,
            },
        ]
        expected_ifaces_states += self._create_bond_slaves_states(DEFAULT_MTU)
        expected_bond_state = {
            nmstate.Interface.NAME: TESTBOND0,
            nmstate.Interface.MTU: DEFAULT_MTU,
        }
        expected_ifaces_states.append(expected_bond_state)

        sort_by_name(expected_ifaces_states)
        assert {nmstate.Interface.KEY: expected_ifaces_states} == state

    def test_remove_network_with_lowest_mtu(
        self, rconfig_mock, current_state_mock
    ):
        low_mtu = DEFAULT_MTU - 500
        current_ifaces_states = current_state_mock[nmstate.Interface.KEY]
        current_ifaces_states += self._create_bond_with_slaves_ifaces_states(
            DEFAULT_MTU, include_type=True
        )
        current_ifaces_states += self._create_vlan_with_bridge_ifaces_states(
            VLAN101, TESTNET1, DEFAULT_MTU
        )
        current_ifaces_states += self._create_vlan_with_bridge_ifaces_states(
            VLAN102, TESTNET2, low_mtu
        )

        rconfig_mock.networks = {
            TESTNET1: create_network_config(
                'bonding',
                TESTBOND0,
                bridged=True,
                vlan=VLAN101,
                mtu=DEFAULT_MTU,
            ),
            TESTNET2: create_network_config(
                'bonding', TESTBOND0, bridged=True, vlan=VLAN102, mtu=low_mtu
            ),
        }
        networks = {TESTNET2: {'remove': True}}
        state = nmstate.generate_state(networks=networks, bondings={})

        expected_ifaces_states = [
            {
                nmstate.Interface.NAME: TESTNET2,
                nmstate.Interface.STATE: nmstate.InterfaceState.ABSENT,
            },
            {
                nmstate.Interface.NAME: '{}.{}'.format(TESTBOND0, VLAN102),
                nmstate.Interface.STATE: nmstate.InterfaceState.ABSENT,
            },
        ]
        sort_by_name(expected_ifaces_states)
        assert {nmstate.Interface.KEY: expected_ifaces_states} == state

    def test_edit_network_to_higher_mtu(
        self, rconfig_mock, current_state_mock
    ):
        high_mtu = DEFAULT_MTU + 500
        current_ifaces_states = current_state_mock[nmstate.Interface.KEY]
        current_ifaces_states += self._create_bond_with_slaves_ifaces_states(
            DEFAULT_MTU, include_type=True
        )
        current_ifaces_states += self._create_vlan_with_bridge_ifaces_states(
            VLAN101, TESTNET1, DEFAULT_MTU
        )
        current_ifaces_states += self._create_vlan_with_bridge_ifaces_states(
            VLAN102, TESTNET2, DEFAULT_MTU
        )

        rconfig_mock.networks = {
            TESTNET1: create_network_config(
                'bonding',
                TESTBOND0,
                bridged=True,
                vlan=VLAN101,
                mtu=DEFAULT_MTU,
            ),
            TESTNET2: create_network_config(
                'bonding',
                TESTBOND0,
                bridged=True,
                vlan=VLAN102,
                mtu=DEFAULT_MTU,
            ),
        }
        networks = {
            TESTNET2: create_network_config(
                'bonding', TESTBOND0, bridged=True, vlan=VLAN102, mtu=high_mtu
            )
        }
        state = nmstate.generate_state(networks=networks, bondings={})

        expected_ifaces_states = self._create_vlan_with_bridge_ifaces_states(
            VLAN102, TESTNET2, high_mtu
        )
        expected_ifaces_states += self._create_bond_slaves_states(high_mtu)
        expected_bond_state = {
            nmstate.Interface.NAME: TESTBOND0,
            nmstate.Interface.STATE: nmstate.InterfaceState.UP,
            nmstate.Interface.MTU: high_mtu,
        }
        expected_ifaces_states.append(expected_bond_state)
        sort_by_name(expected_ifaces_states)
        assert {nmstate.Interface.KEY: expected_ifaces_states} == state

    def test_edit_network_to_lower_mtu(self, rconfig_mock, current_state_mock):
        lower_mtu = DEFAULT_MTU - 500
        current_ifaces_states = current_state_mock[nmstate.Interface.KEY]
        current_ifaces_states += self._create_bond_with_slaves_ifaces_states(
            DEFAULT_MTU, include_type=True
        )
        current_ifaces_states += self._create_vlan_with_bridge_ifaces_states(
            VLAN101, TESTNET1, DEFAULT_MTU
        )
        current_ifaces_states += self._create_vlan_with_bridge_ifaces_states(
            VLAN102, TESTNET2, DEFAULT_MTU
        )

        rconfig_mock.networks = {
            TESTNET1: create_network_config(
                'bonding',
                TESTBOND0,
                bridged=True,
                vlan=VLAN101,
                mtu=DEFAULT_MTU,
            ),
            TESTNET2: create_network_config(
                'bonding',
                TESTBOND0,
                bridged=True,
                vlan=VLAN102,
                mtu=DEFAULT_MTU,
            ),
        }
        networks = {
            TESTNET2: create_network_config(
                'bonding', TESTBOND0, bridged=True, vlan=VLAN102, mtu=lower_mtu
            )
        }
        state = nmstate.generate_state(networks=networks, bondings={})

        expected_ifaces_states = self._create_vlan_with_bridge_ifaces_states(
            VLAN102, TESTNET2, lower_mtu
        )
        expected_bond_state = {
            nmstate.Interface.NAME: TESTBOND0,
            nmstate.Interface.STATE: nmstate.InterfaceState.UP,
            nmstate.Interface.MTU: DEFAULT_MTU,
        }
        expected_ifaces_states.append(expected_bond_state)
        sort_by_name(expected_ifaces_states)
        assert {nmstate.Interface.KEY: expected_ifaces_states} == state

    @parametrize_vlanned
    def test_add_slave_to_bonded_network_with_non_default_mtu(
        self, rconfig_mock, vlanned, current_state_mock
    ):
        mtu = DEFAULT_MTU - 500
        current_ifaces_states = current_state_mock[nmstate.Interface.KEY]
        current_ifaces_states += self._create_bond_with_slaves_ifaces_states(
            mtu, include_type=True
        )
        current_ifaces_states.append(
            create_ethernet_iface_state(IFACE2, include_type=True)
        )
        if vlanned:
            vlan_state = self._create_vlan_state(VLAN101, mtu)
            current_ifaces_states.append(vlan_state)
            vlan_ifname = vlan_state[nmstate.Interface.NAME]
            current_ifaces_states.append(
                self._create_bridge_state(TESTNET1, vlan_ifname, mtu)
            )
        else:
            current_ifaces_states.append(
                self._create_bridge_state(TESTNET1, TESTBOND0, mtu)
            )
        rconfig_mock.networks = {
            TESTNET1: create_network_config(
                'bonding',
                TESTBOND0,
                bridged=True,
                vlan=VLAN101 if vlanned else None,
                mtu=mtu,
            )
        }
        rconfig_mock.bonds = {
            TESTBOND0: {'nics': [IFACE0, IFACE1], 'switch': 'legacy'}
        }

        bondings = create_bonding_config(slaves=[IFACE0, IFACE1, IFACE2])
        state = nmstate.generate_state(networks={}, bondings=bondings)

        slaves = [IFACE0, IFACE1, IFACE2]
        bond0_state = {
            nmstate.Interface.NAME: TESTBOND0,
            nmstate.Interface.TYPE: nmstate.InterfaceType.BOND,
            nmstate.Interface.STATE: nmstate.InterfaceState.UP,
            nmstate.BondSchema.CONFIG_SUBTREE: {
                nmstate.BondSchema.MODE: 'balance-rr',
                nmstate.BondSchema.SLAVES: slaves,
            },
        }
        if vlanned:
            bond0_state[nmstate.Interface.MTU] = mtu
        slave2_state = {
            nmstate.Interface.NAME: IFACE2,
            nmstate.Interface.STATE: nmstate.InterfaceState.UP,
            nmstate.Interface.MTU: mtu,
        }
        expected_ifaces_states = [bond0_state, slave2_state]
        sort_by_name(expected_ifaces_states)
        assert {nmstate.Interface.KEY: expected_ifaces_states} == state

    @parametrize_bridged
    def test_mtu_reset_on_network_dettach(
        self, rconfig_mock, bridged, current_state_mock
    ):
        mtu = DEFAULT_MTU * 4
        current_ifaces_states = current_state_mock[nmstate.Interface.KEY]
        current_ifaces_states.append(
            create_ethernet_iface_state(IFACE0, include_type=True, mtu=mtu)
        )
        if bridged:
            current_ifaces_states.append(
                self._create_bridge_state(TESTNET1, IFACE0, mtu)
            )

        rconfig_mock.bonds = {}
        rconfig_mock.networks = {
            TESTNET1: create_network_config(
                'nic', IFACE0, bridged=bridged, mtu=mtu
            )
        }
        remove_network = {TESTNET1: {'remove': True}}
        state = nmstate.generate_state(networks=remove_network, bondings={})

        expected_iface_state = create_ethernet_iface_state(IFACE0)
        disable_iface_ip(expected_iface_state)
        expected_iface_states = [expected_iface_state]

        if bridged:
            expected_iface_states.append(
                {
                    nmstate.Interface.NAME: TESTNET1,
                    nmstate.Interface.STATE: nmstate.InterfaceState.ABSENT,
                }
            )
        assert {nmstate.Interface.KEY: expected_iface_states} == state

    def test_remove_bond_with_custom_mtu(
        self, rconfig_mock, current_state_mock
    ):
        high_mtu = DEFAULT_MTU + 500
        current_ifaces_states = current_state_mock[nmstate.Interface.KEY]
        current_ifaces_states += self._create_bond_with_slaves_ifaces_states(
            high_mtu, include_type=True
        )
        current_ifaces_states.append(
            self._create_bridge_state(TESTNET1, TESTBOND0, high_mtu)
        )

        rconfig_mock.networks = {
            TESTNET1: create_network_config(
                'bonding', TESTBOND0, bridged=True, mtu=high_mtu
            )
        }
        rconfig_mock.bonds = {
            TESTBOND0: {'nics': [IFACE0, IFACE1], 'switch': 'legacy'}
        }
        networks = {TESTNET1: {'remove': True}}
        bondings = {TESTBOND0: {'remove': True}}
        state = nmstate.generate_state(networks=networks, bondings=bondings)

        expected_ifaces_states = [
            {
                nmstate.Interface.NAME: TESTNET1,
                nmstate.Interface.STATE: nmstate.InterfaceState.ABSENT,
            },
            {
                nmstate.Interface.NAME: TESTBOND0,
                nmstate.Interface.STATE: nmstate.InterfaceState.ABSENT,
                nmstate.Interface.TYPE: nmstate.InterfaceType.BOND,
            },
        ]
        expected_ifaces_states += self._create_bond_slaves_states(DEFAULT_MTU)

        sort_by_name(expected_ifaces_states)
        assert {nmstate.Interface.KEY: expected_ifaces_states} == state

    def _create_bond_with_slaves_ifaces_states(self, mtu, include_type=False):
        ifstates = self._create_bond_slaves_states(mtu, include_type)
        ifstates.append(self._create_bond_state(mtu))
        return ifstates

    def _create_vlan_with_bridge_ifaces_states(self, vlan_id, brname, mtu):
        vlan_state = self._create_vlan_state(vlan_id, mtu)
        vlan_ifname = vlan_state[nmstate.Interface.NAME]
        return [
            vlan_state,
            self._create_bridge_state(brname, vlan_ifname, mtu),
        ]

    def _create_bond_slaves_states(self, mtu, include_type=False):
        eth0_state = create_ethernet_iface_state(IFACE0, include_type, mtu)
        eth1_state = create_ethernet_iface_state(IFACE1, include_type, mtu)
        return [eth0_state, eth1_state]

    def _create_bond_state(self, mtu):
        bond0_state = create_bond_iface_state(
            TESTBOND0, 'balance-rr', [IFACE0, IFACE1], mtu
        )
        disable_iface_ip(bond0_state)
        return bond0_state

    def _create_vlan_state(self, vlan_id, mtu):
        vlan_state = create_vlan_iface_state(TESTBOND0, vlan_id, mtu)
        disable_iface_ip(vlan_state)
        return vlan_state

    def _create_bridge_state(self, brname, portname, mtu):
        bridge1_state = create_bridge_iface_state(
            brname,
            portname,
            mtu=mtu,
            options=generate_bridge_options(stp_enabled=False),
        )
        disable_iface_ip(bridge1_state)
        return bridge1_state
