#
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
#

from __future__ import absolute_import
from __future__ import division

import pytest

from vdsm.network import nmstate

from network.compat import mock


IFACE0 = 'eth0'
IFACE1 = 'eth1'
IFACE2 = 'eth2'

TESTNET1 = 'testnet1'
TESTNET2 = 'testnet2'

TESTBOND0 = 'testbond0'

DEFAULT_MTU = 1500

VLAN101 = 101
VLAN102 = 102

IPv4_ADDRESS1 = '192.0.2.1'
IPv4_GATEWAY1 = '192.0.2.254'
IPv4_NETMASK1 = '255.255.255.0'
IPv4_PREFIX1 = 24
IPv6_ADDRESS1 = 'fdb3:84e5:4ff4:55e3::1'
IPv6_PREFIX1 = 64

IPv4_ADDRESS2 = '192.0.3.1'
IPv4_NETMASK2 = '255.255.255.0'
IPv4_PREFIX2 = 24
IPv6_ADDRESS2 = 'fdb3:84e5:4ff4:88e3::1'
IPv6_PREFIX2 = 64

DNS_SERVERS1 = ['1.2.3.4', '5.6.7.8']
DNS_SERVERS2 = ['9.10.11.12', '13.14.15.16']

parametrize_bridged = pytest.mark.parametrize(
    'bridged', [False, True], ids=['bridgeless', 'bridged']
)

parametrize_vlanned = pytest.mark.parametrize(
    'vlanned', [False, True], ids=['without-vlan', 'with-vlan']
)


@pytest.fixture(autouse=True)
def current_state_mock():
    with mock.patch.object(nmstate, 'state_show') as state:
        state.return_value = {nmstate.Interface.KEY: []}
        yield state.return_value


@parametrize_bridged
def test_translate_nets_without_ip(bridged):
    networks = {
        TESTNET1: _create_network_config('nic', IFACE0, bridged),
        TESTNET2: _create_network_config('nic', IFACE1, bridged),
    }
    state = nmstate.generate_state(networks=networks, bondings={})

    eth0_state = _create_ethernet_iface_state(IFACE0)
    eth1_state = _create_ethernet_iface_state(IFACE1)

    _disable_iface_ip(eth0_state, eth1_state)

    expected_state = {nmstate.Interface.KEY: [eth0_state, eth1_state]}
    if bridged:
        bridge1_state = _create_bridge_iface_state(
            TESTNET1,
            IFACE0,
            options=_generate_bridge_options(stp_enabled=False),
        )
        bridge2_state = _create_bridge_iface_state(
            TESTNET2,
            IFACE1,
            options=_generate_bridge_options(stp_enabled=False),
        )
        _disable_iface_ip(bridge1_state, bridge2_state)
        expected_state[nmstate.Interface.KEY].extend(
            [bridge1_state, bridge2_state]
        )
    _sort_by_name(expected_state[nmstate.Interface.KEY])
    assert expected_state == state


@parametrize_bridged
def test_translate_nets_with_ip(bridged):
    networks = {
        TESTNET1: _create_network_config(
            'nic',
            IFACE0,
            bridged,
            static_ip_configuration=_create_static_ip_configuration(
                IPv4_ADDRESS1, IPv4_NETMASK1, IPv6_ADDRESS1, IPv6_PREFIX1
            ),
        ),
        TESTNET2: _create_network_config(
            'nic',
            IFACE1,
            bridged,
            static_ip_configuration=_create_static_ip_configuration(
                IPv4_ADDRESS2, IPv4_NETMASK2, IPv6_ADDRESS2, IPv6_PREFIX2
            ),
        ),
    }
    state = nmstate.generate_state(networks=networks, bondings={})

    eth0_state = _create_ethernet_iface_state(IFACE0)
    eth1_state = _create_ethernet_iface_state(IFACE1)

    ip0_state = _create_ipv4_state(IPv4_ADDRESS1, IPv4_PREFIX1)
    ip0_state.update(_create_ipv6_state(IPv6_ADDRESS1, IPv6_PREFIX1))
    ip1_state = _create_ipv4_state(IPv4_ADDRESS2, IPv4_PREFIX2)
    ip1_state.update(_create_ipv6_state(IPv6_ADDRESS2, IPv6_PREFIX2))

    expected_state = {nmstate.Interface.KEY: [eth0_state, eth1_state]}
    if bridged:
        _disable_iface_ip(eth0_state, eth1_state)
        bridge1_state = _create_bridge_iface_state(
            TESTNET1,
            IFACE0,
            options=_generate_bridge_options(stp_enabled=False),
        )
        bridge2_state = _create_bridge_iface_state(
            TESTNET2,
            IFACE1,
            options=_generate_bridge_options(stp_enabled=False),
        )
        bridge1_state.update(ip0_state)
        bridge2_state.update(ip1_state)
        expected_state[nmstate.Interface.KEY].extend(
            [bridge1_state, bridge2_state]
        )
    else:
        eth0_state.update(ip0_state)
        eth1_state.update(ip1_state)

    _sort_by_name(expected_state[nmstate.Interface.KEY])
    assert expected_state == state


class TestBond(object):
    def test_translate_new_bond_with_two_slaves(self):
        bondings = {TESTBOND0: {'nics': [IFACE0, IFACE1], 'switch': 'legacy'}}
        state = nmstate.generate_state(networks={}, bondings=bondings)

        bond0_state = _create_bond_iface_state(
            TESTBOND0, 'balance-rr', [IFACE0, IFACE1], mtu=None
        )

        _disable_iface_ip(bond0_state)

        expected_state = {nmstate.Interface.KEY: [bond0_state]}
        assert expected_state == state

    @mock.patch.object(nmstate, 'RunningConfig')
    def test_translate_edit_bond_with_slaves(self, rconfig_mock):
        bondings = {TESTBOND0: {'nics': [IFACE0, IFACE1], 'switch': 'legacy'}}
        rconfig_mock.return_value.bonds = bondings

        state = nmstate.generate_state(networks={}, bondings=bondings)

        bond0_state = _create_bond_iface_state(
            TESTBOND0, 'balance-rr', [IFACE0, IFACE1], mtu=None
        )

        expected_state = {nmstate.Interface.KEY: [bond0_state]}
        assert expected_state == state

    def test_translate_new_bond_with_two_slaves_and_options(self):
        bondings = {
            TESTBOND0: {
                'nics': [IFACE0, IFACE1],
                'options': 'mode=4 miimon=150',
                'switch': 'legacy',
            }
        }
        state = nmstate.generate_state(networks={}, bondings=bondings)

        bond0_state = _create_bond_iface_state(
            TESTBOND0, '802.3ad', [IFACE0, IFACE1], mtu=None, miimon='150'
        )

        _disable_iface_ip(bond0_state)

        expected_state = {nmstate.Interface.KEY: [bond0_state]}
        assert expected_state == state

    def test_translate_remove_bonds(self):
        bondings = {TESTBOND0: {'remove': True}}

        state = nmstate.generate_state(networks={}, bondings=bondings)

        expected_state = {
            nmstate.Interface.KEY: [
                {'name': TESTBOND0, 'type': 'bond', 'state': 'absent'}
            ]
        }
        assert expected_state == state


class TestBondedNetwork(object):
    def test_translate_empty_networks_and_bonds(self):
        state = nmstate.generate_state(networks={}, bondings={})

        assert {nmstate.Interface.KEY: []} == state

    @parametrize_bridged
    def test_translate_net_with_ip_on_bond(self, bridged):
        networks = {
            TESTNET1: _create_network_config(
                'bonding',
                TESTBOND0,
                bridged,
                static_ip_configuration=_create_static_ip_configuration(
                    IPv4_ADDRESS1, IPv4_NETMASK1, IPv6_ADDRESS1, IPv6_PREFIX1
                ),
            )
        }
        bondings = {TESTBOND0: {'nics': [IFACE0, IFACE1], 'switch': 'legacy'}}
        state = nmstate.generate_state(networks=networks, bondings=bondings)

        bond0_state = _create_bond_iface_state(
            TESTBOND0, 'balance-rr', [IFACE0, IFACE1]
        )

        ip_state = _create_ipv4_state(IPv4_ADDRESS1, IPv4_PREFIX1)
        ip_state.update(_create_ipv6_state(IPv6_ADDRESS1, IPv6_PREFIX1))

        expected_state = {nmstate.Interface.KEY: [bond0_state]}
        if bridged:
            _disable_iface_ip(bond0_state)
            bridge1_state = _create_bridge_iface_state(
                TESTNET1,
                TESTBOND0,
                options=_generate_bridge_options(stp_enabled=False),
            )
            bridge1_state.update(ip_state)
            expected_state[nmstate.Interface.KEY].extend([bridge1_state])
        else:
            bond0_state.update(ip_state)

        assert expected_state == state

    @parametrize_bridged
    def test_translate_net_with_dynamic_ip(self, bridged):
        networks = {
            TESTNET1: _create_network_config(
                'bonding',
                TESTBOND0,
                bridged,
                dynamic_ip_configuration=_create_dynamic_ip_configuration(
                    dhcpv4=True, dhcpv6=True, ipv6autoconf=True
                ),
            )
        }
        bondings = {TESTBOND0: {'nics': [IFACE0, IFACE1], 'switch': 'legacy'}}
        state = nmstate.generate_state(networks=networks, bondings=bondings)

        bond0_state = _create_bond_iface_state(
            TESTBOND0, 'balance-rr', [IFACE0, IFACE1]
        )

        ip_state = _create_ipv4_state(dynamic=True)
        ip_state.update(_create_ipv6_state(dynamic=True))

        expected_state = {nmstate.Interface.KEY: [bond0_state]}
        if bridged:
            _disable_iface_ip(bond0_state)
            bridge1_state = _create_bridge_iface_state(
                TESTNET1,
                TESTBOND0,
                options=_generate_bridge_options(stp_enabled=False),
            )
            bridge1_state.update(ip_state)
            expected_state[nmstate.Interface.KEY].extend([bridge1_state])
        else:
            bond0_state.update(ip_state)

        assert expected_state == state

    @parametrize_bridged
    def test_translate_net_with_ip_on_vlan_on_bond(self, bridged):
        networks = {
            TESTNET1: _create_network_config(
                'bonding',
                TESTBOND0,
                bridged,
                static_ip_configuration=_create_static_ip_configuration(
                    IPv4_ADDRESS1, IPv4_NETMASK1, IPv6_ADDRESS1, IPv6_PREFIX1
                ),
                vlan=VLAN101,
            )
        }
        bondings = {TESTBOND0: {'nics': [IFACE0, IFACE1], 'switch': 'legacy'}}
        state = nmstate.generate_state(networks=networks, bondings=bondings)

        bond0_state = _create_bond_iface_state(
            TESTBOND0, 'balance-rr', [IFACE0, IFACE1]
        )

        _disable_iface_ip(bond0_state)

        vlan101_state = _create_vlan_iface_state(TESTBOND0, VLAN101)
        ip1_state = _create_ipv4_state(IPv4_ADDRESS1, IPv4_PREFIX1)
        ip1_state.update(_create_ipv6_state(IPv6_ADDRESS1, IPv6_PREFIX1))

        expected_state = {nmstate.Interface.KEY: [bond0_state, vlan101_state]}
        if bridged:
            _disable_iface_ip(vlan101_state)
            bridge1_state = _create_bridge_iface_state(
                TESTNET1,
                vlan101_state['name'],
                options=_generate_bridge_options(stp_enabled=False),
            )
            bridge1_state.update(ip1_state)
            expected_state[nmstate.Interface.KEY].extend([bridge1_state])
        else:
            vlan101_state.update(ip1_state)
        assert expected_state == state

    @parametrize_bridged
    @mock.patch.object(nmstate, 'RunningConfig')
    def test_translate_remove_net_on_bond(
        self, rconfig_mock, bridged, current_state_mock
    ):
        current_ifaces_states = current_state_mock[nmstate.Interface.KEY]
        current_ifaces_states.append(
            {
                'name': TESTBOND0,
                nmstate.Interface.TYPE: nmstate.InterfaceType.BOND,
                'state': 'up',
                nmstate.Interface.MTU: DEFAULT_MTU,
                'ipv4': {'enabled': False},
                'ipv6': {'enabled': False},
            }
        )
        rconfig_mock.return_value.networks = {
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
                    'name': TESTBOND0,
                    'state': 'up',
                    nmstate.Interface.MTU: DEFAULT_MTU,
                    'ipv4': {'enabled': False},
                    'ipv6': {'enabled': False},
                }
            ]
        }
        if bridged:
            expected_state[nmstate.Interface.KEY].append(
                {'name': TESTNET1, 'state': 'absent'}
            )
        _sort_by_name(expected_state[nmstate.Interface.KEY])
        assert expected_state == state

    @parametrize_bridged
    @mock.patch.object(nmstate, 'RunningConfig')
    def test_translate_remove_vlan_net_on_bond(self, rconfig_mock, bridged):
        rconfig_mock.return_value.networks = {
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
                {'name': TESTBOND0 + '.' + str(VLAN101), 'state': 'absent'}
            ]
        }
        if bridged:
            expected_state[nmstate.Interface.KEY].extend(
                [{'name': TESTNET1, 'state': 'absent'}]
            )
        _sort_by_name(expected_state[nmstate.Interface.KEY])
        assert expected_state == state

    @parametrize_bridged
    @mock.patch.object(nmstate, 'RunningConfig')
    def test_translate_remove_bridged_net_and_bond(
        self, rconfig_mock, bridged, current_state_mock
    ):
        current_ifaces_states = current_state_mock[nmstate.Interface.KEY]
        current_ifaces_states.append(
            {
                'name': TESTBOND0,
                nmstate.Interface.TYPE: nmstate.InterfaceType.BOND,
                'state': 'up',
                nmstate.Interface.MTU: DEFAULT_MTU,
                'ipv4': {'enabled': False},
                'ipv6': {'enabled': False},
            }
        )
        rconfig_mock.return_value.networks = {
            TESTNET1: {
                'bonding': TESTBOND0,
                'bridged': bridged,
                'switch': 'legacy',
                'defaultRoute': False,
            }
        }

        networks = {TESTNET1: {'remove': True}}
        bondings = {TESTBOND0: {'remove': True}}

        state = nmstate.generate_state(networks=networks, bondings=bondings)

        expected_state = {
            nmstate.Interface.KEY: [
                {'name': TESTBOND0, 'type': 'bond', 'state': 'absent'}
            ]
        }
        if bridged:
            expected_state[nmstate.Interface.KEY].append(
                {'name': TESTNET1, 'state': 'absent'}
            )
        assert expected_state == state


@parametrize_bridged
@mock.patch.object(nmstate, 'RunningConfig')
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
    rconfig_mock.return_value.networks = {
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

    eth0_state = _create_ethernet_iface_state(IFACE0)
    eth1_state = _create_ethernet_iface_state(IFACE1)

    _disable_iface_ip(eth0_state, eth1_state)

    expected_state = {nmstate.Interface.KEY: [eth0_state, eth1_state]}
    if bridged:
        expected_state[nmstate.Interface.KEY].extend(
            [
                {'name': TESTNET1, 'state': 'absent'},
                {'name': TESTNET2, 'state': 'absent'},
            ]
        )
    _sort_by_name(expected_state[nmstate.Interface.KEY])
    assert expected_state == state


@parametrize_bridged
@mock.patch.object(nmstate, 'RunningConfig')
def test_translate_remove_vlan_net(rconfig_mock, bridged):
    rconfig_mock.return_value.networks = {
        TESTNET1: {
            'nic': IFACE0,
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
            {'name': IFACE0 + '.' + str(VLAN101), 'state': 'absent'}
        ]
    }
    if bridged:
        expected_state[nmstate.Interface.KEY].append(
            {'name': TESTNET1, 'state': 'absent'}
        )
    _sort_by_name(expected_state[nmstate.Interface.KEY])
    assert expected_state == state


@parametrize_bridged
def test_translate_add_network_with_default_route(bridged):
    networks = {
        TESTNET1: _create_network_config(
            'nic',
            IFACE0,
            bridged=bridged,
            static_ip_configuration=_create_static_ip_configuration(
                IPv4_ADDRESS1, IPv4_NETMASK1, IPv6_ADDRESS1, IPv6_PREFIX1
            ),
            default_route=True,
            gateway=IPv4_GATEWAY1,
        )
    }
    state = nmstate.generate_state(networks=networks, bondings={})

    eth0_state = _create_ethernet_iface_state(IFACE0)
    ip0_state = _create_ipv4_state(IPv4_ADDRESS1, IPv4_PREFIX1)
    ip0_state.update(_create_ipv6_state(IPv6_ADDRESS1, IPv6_PREFIX1))

    expected_state = {nmstate.Interface.KEY: [eth0_state]}

    if bridged:
        _disable_iface_ip(eth0_state)
        bridge1_state = _create_bridge_iface_state(
            TESTNET1,
            IFACE0,
            options=_generate_bridge_options(stp_enabled=False),
        )
        bridge1_state.update(ip0_state)
        expected_state[nmstate.Interface.KEY].append(bridge1_state)
        if_with_default_route = TESTNET1
    else:
        eth0_state.update(ip0_state)
        if_with_default_route = IFACE0

    expected_state[nmstate.Route.KEY] = _get_routes_config(
        IPv4_GATEWAY1, if_with_default_route
    )
    assert state == expected_state


@parametrize_bridged
@mock.patch.object(nmstate, 'RunningConfig')
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
    rconfig_mock.return_value.networks = {
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

    eth0_state = _create_ethernet_iface_state(IFACE0)
    _disable_iface_ip(eth0_state)

    expected_state = {nmstate.Interface.KEY: [eth0_state]}

    if bridged:
        expected_state[nmstate.Interface.KEY].append(
            {'name': TESTNET1, 'state': 'absent'}
        )

    assert expected_state == state


@parametrize_bridged
@mock.patch.object(nmstate, 'RunningConfig')
def test_translate_default_route_network_static_to_dhcp(rconfig_mock, bridged):
    rconfig_mock.return_value.networks = {
        TESTNET1: {
            'nic': IFACE0,
            'bridged': bridged,
            'switch': 'legacy',
            'defaultRoute': True,
            'gateway': IPv4_GATEWAY1,
            'ipaddr': IPv4_ADDRESS1,
            'netmask': IPv4_NETMASK1,
        }
    }
    networks = {
        TESTNET1: _create_network_config(
            'nic',
            IFACE0,
            bridged,
            dynamic_ip_configuration=_create_dynamic_ip_configuration(
                dhcpv4=True, dhcpv6=False, ipv6autoconf=False
            ),
            default_route=True,
        )
    }
    state = nmstate.generate_state(networks=networks, bondings={})

    eth0_state = _create_ethernet_iface_state(IFACE0)
    ip0_state = _create_ipv4_state(dynamic=True, default_route=True)
    ip0_state.update(_create_ipv6_state())

    expected_state = {nmstate.Interface.KEY: [eth0_state]}

    if bridged:
        _disable_iface_ip(eth0_state)
        bridge1_state = _create_bridge_iface_state(
            TESTNET1,
            IFACE0,
            options=_generate_bridge_options(stp_enabled=False),
        )
        bridge1_state.update(ip0_state)
        expected_state[nmstate.Interface.KEY].append(bridge1_state)
        if_with_default_route = TESTNET1
    else:
        eth0_state.update(ip0_state)
        if_with_default_route = IFACE0

    expected_state[nmstate.Route.KEY] = _get_routes_config(
        IPv4_GATEWAY1, if_with_default_route, nmstate.Route.STATE_ABSENT
    )
    assert state == expected_state


@parametrize_bridged
@mock.patch.object(nmstate, 'RunningConfig')
def test_translate_remove_default_route_from_network(rconfig_mock, bridged):
    rconfig_mock.return_value.networks = {
        TESTNET1: {
            'nic': IFACE0,
            'bridged': bridged,
            'switch': 'legacy',
            'defaultRoute': True,
            'gateway': IPv4_GATEWAY1,
            'ipaddr': IPv4_ADDRESS1,
            'netmask': IPv4_NETMASK1,
        }
    }
    networks = {
        TESTNET1: _create_network_config(
            'nic',
            IFACE0,
            bridged,
            static_ip_configuration=_create_static_ip_configuration(
                IPv4_ADDRESS1, IPv4_NETMASK1, None, None
            ),
            default_route=False,
        )
    }
    state = nmstate.generate_state(networks=networks, bondings={})

    eth0_state = _create_ethernet_iface_state(IFACE0)
    ip0_state = _create_ipv4_state(IPv4_ADDRESS1, IPv4_PREFIX1)
    ip0_state.update(_create_ipv6_state())

    expected_state = {nmstate.Interface.KEY: [eth0_state]}

    if bridged:
        _disable_iface_ip(eth0_state)
        bridge1_state = _create_bridge_iface_state(
            TESTNET1,
            IFACE0,
            options=_generate_bridge_options(stp_enabled=False),
        )
        bridge1_state.update(ip0_state)
        expected_state[nmstate.Interface.KEY].append(bridge1_state)
        if_with_default_route = TESTNET1
    else:
        eth0_state.update(ip0_state)
        if_with_default_route = IFACE0

    expected_state[nmstate.Route.KEY] = _get_routes_config(
        IPv4_GATEWAY1, if_with_default_route, nmstate.Route.STATE_ABSENT
    )
    assert state == expected_state


def test_translate_add_network_with_default_route_on_vlan_interface():
    networks = {
        TESTNET1: _create_network_config(
            'nic',
            IFACE0,
            bridged=False,
            static_ip_configuration=_create_static_ip_configuration(
                IPv4_ADDRESS1, IPv4_NETMASK1, IPv6_ADDRESS1, IPv6_PREFIX1
            ),
            default_route=True,
            gateway=IPv4_GATEWAY1,
            vlan=VLAN101,
        )
    }
    state = nmstate.generate_state(networks=networks, bondings={})

    vlan101_state = _create_vlan_iface_state(IFACE0, VLAN101)
    ip0_state = _create_ipv4_state(IPv4_ADDRESS1, IPv4_PREFIX1)
    ip0_state.update(_create_ipv6_state(IPv6_ADDRESS1, IPv6_PREFIX1))
    vlan101_state.update(ip0_state)

    vlan_base_state = _create_ethernet_iface_state(IFACE0)
    vlan_base_state.update(_create_ipv4_state())
    vlan_base_state.update(_create_ipv6_state())
    expected_state = {nmstate.Interface.KEY: [vlan_base_state, vlan101_state]}

    expected_state[nmstate.Route.KEY] = _get_routes_config(
        IPv4_GATEWAY1, vlan101_state['name']
    )
    assert expected_state == state


def test_translate_vlan_id_0():
    networks = {
        TESTNET1: _create_network_config('nic', IFACE0, bridged=False, vlan=0)
    }
    state = nmstate.generate_state(networks=networks, bondings={})

    vlan0_state = _create_vlan_iface_state(IFACE0, 0)
    ipv4_state = _create_ipv4_state()
    ipv6_state = _create_ipv6_state()
    vlan0_state.update(ipv4_state)
    vlan0_state.update(ipv6_state)

    vlan_base_state = _create_ethernet_iface_state(IFACE0)
    vlan_base_state.update(ipv4_state)
    vlan_base_state.update(ipv6_state)
    expected_state = {nmstate.Interface.KEY: [vlan_base_state, vlan0_state]}

    assert expected_state == state


def test_bridgeless_and_vlan_networks_on_the_same_nic():
    networks = {
        TESTNET1: _create_network_config(
            'nic',
            IFACE0,
            bridged=False,
            static_ip_configuration=_create_static_ip_configuration(
                IPv4_ADDRESS1, IPv4_NETMASK1, None, None
            ),
        ),
        TESTNET2: _create_network_config(
            'nic', IFACE0, bridged=False, vlan=VLAN101
        ),
    }
    state = nmstate.generate_state(networks=networks, bondings={})

    bridgeless_state = _create_ethernet_iface_state(IFACE0)
    vlan0_state = _create_vlan_iface_state(IFACE0, VLAN101)

    ipv4_state = _create_ipv4_state(IPv4_ADDRESS1, IPv4_PREFIX1)
    ipv4_disabled_state = _create_ipv4_state()
    ipv6_disabled_state = _create_ipv6_state()
    bridgeless_state.update(ipv4_state)
    bridgeless_state.update(ipv6_disabled_state)
    vlan0_state.update(ipv4_disabled_state)
    vlan0_state.update(ipv6_disabled_state)

    expected_state = {nmstate.Interface.KEY: [bridgeless_state, vlan0_state]}

    assert expected_state == state


@mock.patch.object(nmstate, 'RunningConfig')
def test_update_network_from_bridged_to_bridgeless(rconfig_mock):
    networks = {TESTNET1: _create_network_config('nic', IFACE0, bridged=True)}
    rconfig_mock.return_value.networks = networks

    updated_network = {
        TESTNET1: _create_network_config('nic', IFACE0, bridged=False)
    }
    state = nmstate.generate_state(networks=updated_network, bondings={})

    eth0_state = _create_ethernet_iface_state(IFACE0)
    _disable_iface_ip(eth0_state)

    remove_bridge_state = _create_bridge_iface_state(
        TESTNET1, port=None, state='absent'
    )

    expected_state = {nmstate.Interface.KEY: [eth0_state, remove_bridge_state]}

    assert expected_state == state


@mock.patch.object(nmstate, 'RunningConfig')
def test_move_vlan_to_another_iface(rconfig_mock):
    rconfig_mock.return_value.networks = {
        TESTNET1: _create_network_config(
            'nic', IFACE0, bridged=False, vlan=VLAN101
        )
    }

    networks = {
        TESTNET1: _create_network_config(
            'nic', IFACE1, bridged=False, vlan=VLAN101
        )
    }
    state = nmstate.generate_state(networks=networks, bondings={})
    eth1_vlan_state = _create_vlan_iface_state(IFACE1, VLAN101)
    _disable_iface_ip(eth1_vlan_state)
    eth1_state = _create_ethernet_iface_state(IFACE1)
    _disable_iface_ip(eth1_state)
    remove_vlan_eth0_state = {
        nmstate.Interface.NAME: 'eth0.101',
        nmstate.Interface.STATE: nmstate.InterfaceState.ABSENT,
    }
    expected_state = {
        nmstate.Interface.KEY: [
            eth1_vlan_state,
            remove_vlan_eth0_state,
            eth1_state,
        ]
    }
    _sort_by_name(expected_state[nmstate.Interface.KEY])
    assert expected_state == state


class TestDns(object):
    def test_dns_add_network_with_default_route(self):
        networks = {
            TESTNET1: _create_network_config(
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
            TESTNET1: _create_network_config(
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
            TESTNET1: _create_network_config('nic', IFACE0, bridged=True)
        }

        state = nmstate.generate_state(networks=networks, bondings={})

        assert state.get(nmstate.DNS.KEY) is None

    @mock.patch.object(nmstate, 'RunningConfig')
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
            TESTNET1: _create_network_config(
                'nic',
                IFACE0,
                bridged=True,
                default_route=True,
                gateway=IPv4_GATEWAY1,
                nameservers=DNS_SERVERS1,
            )
        }
        rconfig_mock.return_value.networks = rconfig_networks

        networks = {TESTNET1: {'remove': True}}

        state = nmstate.generate_state(networks=networks, bondings={})

        expected_state = {nmstate.DNS.CONFIG: {nmstate.DNS.SERVER: []}}

        assert expected_state == state[nmstate.DNS.KEY]

    @mock.patch.object(nmstate, 'RunningConfig')
    def test_dns_replace_network_with_default_route(self, rconfig_mock):
        rconfig_networks = {
            TESTNET1: _create_network_config(
                'nic',
                IFACE0,
                bridged=True,
                default_route=True,
                gateway=IPv4_GATEWAY1,
                nameservers=DNS_SERVERS1,
            )
        }
        rconfig_mock.return_value.networks = rconfig_networks
        networks = {
            TESTNET2: _create_network_config(
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


class TestMtu(object):
    def test_single_network_with_specific_mtu(self, current_state_mock):
        mtu = 2000
        current_ifaces_states = current_state_mock[nmstate.Interface.KEY]
        current_ifaces_states += self._create_bond_slaves_states(
            DEFAULT_MTU, include_type=True
        )

        networks = {
            TESTNET1: _create_network_config(
                'bonding', TESTBOND0, bridged=True, vlan=VLAN101, mtu=mtu
            )
        }
        bondings = _create_bonding_config(slaves=[IFACE0, IFACE1])
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
        _sort_by_name(expected_state[nmstate.Interface.KEY])
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
            TESTNET1: _create_network_config(
                'bonding', TESTBOND0, bridged=True, vlan=VLAN101, mtu=mtu_max
            ),
            TESTNET2: _create_network_config(
                'bonding', TESTBOND0, bridged=True, vlan=VLAN102, mtu=mtu_min
            ),
        }
        bondings = _create_bonding_config(slaves=[IFACE0, IFACE1])
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
        _sort_by_name(expected_state[nmstate.Interface.KEY])
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
            TESTNET2: _create_network_config(
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

        _sort_by_name(expected_ifaces_states)
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
            TESTNET2: _create_network_config(
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

        _sort_by_name(expected_ifaces_states)
        assert {nmstate.Interface.KEY: expected_ifaces_states} == state

    @mock.patch.object(nmstate, 'RunningConfig')
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

        rconfig_mock.return_value.networks = {
            TESTNET1: _create_network_config(
                'bonding',
                TESTBOND0,
                bridged=True,
                vlan=VLAN101,
                mtu=DEFAULT_MTU,
            ),
            TESTNET2: _create_network_config(
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

        _sort_by_name(expected_ifaces_states)
        assert {nmstate.Interface.KEY: expected_ifaces_states} == state

    @mock.patch.object(nmstate, 'RunningConfig')
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

        rconfig_mock.return_value.networks = {
            TESTNET1: _create_network_config(
                'bonding',
                TESTBOND0,
                bridged=True,
                vlan=VLAN101,
                mtu=DEFAULT_MTU,
            ),
            TESTNET2: _create_network_config(
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
            {
                nmstate.Interface.NAME: TESTBOND0,
                nmstate.Interface.MTU: DEFAULT_MTU,
            },
        ]
        _sort_by_name(expected_ifaces_states)
        assert {nmstate.Interface.KEY: expected_ifaces_states} == state

    @mock.patch.object(nmstate, 'RunningConfig')
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

        rconfig_mock.return_value.networks = {
            TESTNET1: _create_network_config(
                'bonding',
                TESTBOND0,
                bridged=True,
                vlan=VLAN101,
                mtu=DEFAULT_MTU,
            ),
            TESTNET2: _create_network_config(
                'bonding',
                TESTBOND0,
                bridged=True,
                vlan=VLAN102,
                mtu=DEFAULT_MTU,
            ),
        }
        networks = {
            TESTNET2: _create_network_config(
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
        _sort_by_name(expected_ifaces_states)
        assert {nmstate.Interface.KEY: expected_ifaces_states} == state

    @mock.patch.object(nmstate, 'RunningConfig')
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

        rconfig_mock.return_value.networks = {
            TESTNET1: _create_network_config(
                'bonding',
                TESTBOND0,
                bridged=True,
                vlan=VLAN101,
                mtu=DEFAULT_MTU,
            ),
            TESTNET2: _create_network_config(
                'bonding',
                TESTBOND0,
                bridged=True,
                vlan=VLAN102,
                mtu=DEFAULT_MTU,
            ),
        }
        networks = {
            TESTNET2: _create_network_config(
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
        _sort_by_name(expected_ifaces_states)
        assert {nmstate.Interface.KEY: expected_ifaces_states} == state

    @parametrize_vlanned
    @mock.patch.object(nmstate, 'RunningConfig')
    def test_add_slave_to_bonded_network_with_non_default_mtu(
        self, rconfig_mock, vlanned, current_state_mock
    ):
        mtu = DEFAULT_MTU - 500
        current_ifaces_states = current_state_mock[nmstate.Interface.KEY]
        current_ifaces_states += self._create_bond_with_slaves_ifaces_states(
            mtu, include_type=True
        )
        current_ifaces_states.append(
            _create_ethernet_iface_state(IFACE2, include_type=True)
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
        rconfig_mock.return_value.networks = {
            TESTNET1: _create_network_config(
                'bonding',
                TESTBOND0,
                bridged=True,
                vlan=VLAN101 if vlanned else None,
                mtu=mtu,
            )
        }
        rconfig_mock.return_value.bonds = {
            TESTBOND0: {'nics': [IFACE0, IFACE1], 'switch': 'legacy'}
        }

        bondings = _create_bonding_config(slaves=[IFACE0, IFACE1, IFACE2])
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
        _sort_by_name(expected_ifaces_states)
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
        eth0_state = _create_ethernet_iface_state(IFACE0, include_type, mtu)
        eth1_state = _create_ethernet_iface_state(IFACE1, include_type, mtu)
        return [eth0_state, eth1_state]

    def _create_bond_state(self, mtu):
        bond0_state = _create_bond_iface_state(
            TESTBOND0, 'balance-rr', [IFACE0, IFACE1], mtu
        )
        _disable_iface_ip(bond0_state)
        return bond0_state

    def _create_vlan_state(self, vlan_id, mtu):
        vlan_state = _create_vlan_iface_state(TESTBOND0, vlan_id, mtu)
        _disable_iface_ip(vlan_state)
        return vlan_state

    def _create_bridge_state(self, brname, portname, mtu):
        bridge1_state = _create_bridge_iface_state(
            brname,
            portname,
            mtu=mtu,
            options=_generate_bridge_options(stp_enabled=False),
        )
        _disable_iface_ip(bridge1_state)
        return bridge1_state


def _sort_by_name(ifaces_states):
    ifaces_states.sort(key=lambda d: d['name'])


def _create_ethernet_iface_state(name, include_type=False, mtu=DEFAULT_MTU):
    state = {nmstate.Interface.NAME: name, nmstate.Interface.STATE: 'up'}
    if include_type:
        state[nmstate.Interface.TYPE] = nmstate.InterfaceType.ETHERNET
    if mtu is not None:
        state[nmstate.Interface.MTU] = mtu
    return state


def _create_bond_iface_state(name, mode, slaves, mtu=DEFAULT_MTU, **options):
    state = {
        nmstate.Interface.NAME: name,
        nmstate.Interface.TYPE: 'bond',
        nmstate.Interface.STATE: 'up',
        'link-aggregation': {'mode': mode, 'slaves': slaves},
    }
    if mtu is not None:
        state[nmstate.Interface.MTU] = mtu
    if options:
        state['link-aggregation']['options'] = options
    return state


def _create_bridge_iface_state(
    name, port, state='up', mtu=DEFAULT_MTU, options=None
):
    bridge_state = {
        nmstate.Interface.NAME: name,
        nmstate.Interface.STATE: state,
    }

    if state == 'up':
        bridge_state[nmstate.Interface.TYPE] = 'linux-bridge'
        bridge_state[nmstate.Interface.MTU] = mtu
    if port:
        bridge_state[nmstate.LinuxBridge.CONFIG_SUBTREE] = {
            'port': [{'name': port}]
        }
    if options:
        bridge_state['bridge']['options'] = options
    return bridge_state


def _generate_bridge_options(stp_enabled):
    return {'stp': {'enabled': stp_enabled}}


def _create_vlan_iface_state(base, vlan, mtu=DEFAULT_MTU):
    return {
        nmstate.Interface.NAME: base + '.' + str(vlan),
        nmstate.Interface.TYPE: 'vlan',
        nmstate.Interface.STATE: 'up',
        nmstate.Interface.MTU: mtu,
        'vlan': {'id': vlan, 'base-iface': base},
    }


def _disable_iface_ip(*ifaces_states):
    ip_disabled_state = _create_ipv4_state()
    ip_disabled_state.update(_create_ipv6_state())
    for iface_state in ifaces_states:
        iface_state.update(ip_disabled_state)


def _create_ipv4_state(
    address=None, prefix=None, dynamic=False, default_route=False
):
    state = {nmstate.Interface.IPV4: {nmstate.InterfaceIP.ENABLED: False}}
    if dynamic:
        state[nmstate.Interface.IPV4] = {
            nmstate.InterfaceIP.ENABLED: True,
            nmstate.InterfaceIP.DHCP: True,
            nmstate.InterfaceIP.AUTO_DNS: default_route,
            nmstate.InterfaceIP.AUTO_GATEWAY: default_route,
            nmstate.InterfaceIP.AUTO_ROUTES: default_route,
        }
    elif address and prefix:
        state[nmstate.Interface.IPV4] = {
            nmstate.InterfaceIP.ENABLED: True,
            nmstate.InterfaceIP.ADDRESS: [
                {
                    nmstate.InterfaceIP.ADDRESS_IP: address,
                    nmstate.InterfaceIP.ADDRESS_PREFIX_LENGTH: prefix,
                }
            ],
            nmstate.InterfaceIP.DHCP: False,
        }
    return state


def _create_ipv6_state(
    address=None, prefix=None, dynamic=False, default_route=False
):
    state = {nmstate.Interface.IPV6: {nmstate.InterfaceIP.ENABLED: False}}
    if dynamic:
        state[nmstate.Interface.IPV6] = {
            nmstate.InterfaceIP.ENABLED: True,
            nmstate.InterfaceIP.DHCP: True,
            'autoconf': True,
            nmstate.InterfaceIP.AUTO_DNS: default_route,
            nmstate.InterfaceIP.AUTO_GATEWAY: default_route,
            nmstate.InterfaceIP.AUTO_ROUTES: default_route,
        }
    elif address and prefix:
        state[nmstate.Interface.IPV6] = {
            nmstate.InterfaceIP.ENABLED: True,
            nmstate.InterfaceIP.ADDRESS: [
                {
                    nmstate.InterfaceIP.ADDRESS_IP: address,
                    nmstate.InterfaceIP.ADDRESS_PREFIX_LENGTH: prefix,
                }
            ],
            nmstate.InterfaceIP.DHCP: False,
            nmstate.InterfaceIPv6.AUTOCONF: False,
        }
    return state


def _get_routes_config(gateway, next_hop, state=None):
    return {
        nmstate.Route.CONFIG: [_create_default_route(gateway, next_hop, state)]
    }


def _create_default_route(gateway, next_hop, state=None):
    route_state = {
        nmstate.Route.DESTINATION: '0.0.0.0/0',
        nmstate.Route.NEXT_HOP_ADDRESS: gateway,
        nmstate.Route.NEXT_HOP_INTERFACE: next_hop,
        nmstate.Route.TABLE_ID: nmstate.Route.USE_DEFAULT_ROUTE_TABLE,
    }
    if state:
        route_state[nmstate.Route.STATE] = state
    return route_state


def _create_bonding_config(slaves):
    return {TESTBOND0: {'nics': slaves, 'switch': 'legacy'}}


def _create_network_config(
    if_type,
    if_name,
    bridged,
    static_ip_configuration=None,
    dynamic_ip_configuration=None,
    vlan=None,
    mtu=None,
    default_route=False,
    gateway=None,
    nameservers=None,
):
    network_config = _create_interface_network_config(if_type, if_name)
    network_config.update(
        _create_bridge_network_config(bridged, stp_enabled=False)
    )
    network_config.update(static_ip_configuration or {})
    network_config.update(dynamic_ip_configuration or {})
    network_config.update({'vlan': vlan} if vlan is not None else {})
    network_config.update({'mtu': mtu} if mtu is not None else {})
    network_config.update({'defaultRoute': default_route})
    network_config.update({'gateway': gateway} if gateway else {})
    network_config.update(
        {'nameservers': nameservers} if nameservers is not None else {}
    )
    return network_config


def _create_interface_network_config(if_type, if_name):
    return {if_type: if_name, 'switch': 'legacy'}


def _create_bridge_network_config(bridged, stp_enabled):
    network_config = {'bridged': bridged}
    if bridged:
        network_config['stp'] = stp_enabled
    return network_config


def _create_static_ip_configuration(
    ipv4_address, ipv4_netmask, ipv6_address, ipv6_prefix_length
):
    ip_config = {}
    if ipv4_address and ipv4_netmask:
        ip_config['ipaddr'] = ipv4_address
        ip_config['netmask'] = ipv4_netmask
    if ipv6_address and ipv6_prefix_length:
        ip_config['ipv6addr'] = ipv6_address + '/' + str(ipv6_prefix_length)
    return ip_config


def _create_dynamic_ip_configuration(dhcpv4, dhcpv6, ipv6autoconf):
    dynamic_ip_config = {}
    if dhcpv4:
        dynamic_ip_config['bootproto'] = 'dhcp'
    if dhcpv6:
        dynamic_ip_config['dhcpv6'] = True
    if ipv6autoconf:
        dynamic_ip_config['ipv6autoconf'] = True
    return dynamic_ip_config
