# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from vdsm.network import nmstate

from .testlib import (
    IFACE0,
    IFACE1,
    IPv4_ADDRESS1,
    IPv4_NETMASK1,
    IPv4_PREFIX1,
    TESTNET1,
    TESTNET2,
    VLAN101,
    VLAN102,
    create_bridge_iface_state,
    create_ethernet_iface_state,
    create_ipv4_state,
    create_ipv6_state,
    create_network_config,
    create_static_ip_configuration,
    create_vlan_iface_state,
    disable_iface_ip,
    generate_bridge_options,
    parametrize_bridged,
    sort_by_name,
)


@parametrize_bridged
def test_translate_remove_vlan_net(rconfig_mock, bridged):
    rconfig_mock.networks = {
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
            {
                nmstate.Interface.NAME: IFACE0 + '.' + str(VLAN101),
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
    sort_by_name(expected_state[nmstate.Interface.KEY])
    assert expected_state == state


def test_translate_vlan_id_0():
    networks = {
        TESTNET1: create_network_config('nic', IFACE0, bridged=False, vlan=0)
    }
    state = nmstate.generate_state(networks=networks, bondings={})

    vlan0_state = create_vlan_iface_state(IFACE0, 0)
    ipv4_state = create_ipv4_state()
    ipv6_state = create_ipv6_state()
    vlan0_state.update(ipv4_state)
    vlan0_state.update(ipv6_state)

    vlan_base_state = create_ethernet_iface_state(IFACE0)
    vlan_base_state.update(ipv4_state)
    vlan_base_state.update(ipv6_state)
    expected_state = {nmstate.Interface.KEY: [vlan_base_state, vlan0_state]}

    assert expected_state == state


def test_bridgeless_and_vlan_networks_on_the_same_nic():
    networks = {
        TESTNET1: create_network_config(
            'nic',
            IFACE0,
            bridged=False,
            static_ip_configuration=create_static_ip_configuration(
                IPv4_ADDRESS1, IPv4_NETMASK1, None, None
            ),
        ),
        TESTNET2: create_network_config(
            'nic', IFACE0, bridged=False, vlan=VLAN101
        ),
    }
    state = nmstate.generate_state(networks=networks, bondings={})

    bridgeless_state = create_ethernet_iface_state(IFACE0)
    vlan0_state = create_vlan_iface_state(IFACE0, VLAN101)

    ipv4_state = create_ipv4_state(IPv4_ADDRESS1, IPv4_PREFIX1)
    ipv4_disabled_state = create_ipv4_state()
    ipv6_disabled_state = create_ipv6_state()
    bridgeless_state.update(ipv4_state)
    bridgeless_state.update(ipv6_disabled_state)
    vlan0_state.update(ipv4_disabled_state)
    vlan0_state.update(ipv6_disabled_state)

    expected_state = {nmstate.Interface.KEY: [bridgeless_state, vlan0_state]}

    assert expected_state == state


def test_move_vlan_to_another_iface(rconfig_mock):
    rconfig_mock.networks = {
        TESTNET1: create_network_config(
            'nic', IFACE0, bridged=False, vlan=VLAN101
        )
    }

    networks = {
        TESTNET1: create_network_config(
            'nic', IFACE1, bridged=False, vlan=VLAN101
        )
    }
    state = nmstate.generate_state(networks=networks, bondings={})
    eth1_vlan_state = create_vlan_iface_state(IFACE1, VLAN101)
    disable_iface_ip(eth1_vlan_state)
    eth1_state = create_ethernet_iface_state(IFACE1)
    disable_iface_ip(eth1_state)
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
    sort_by_name(expected_state[nmstate.Interface.KEY])
    assert expected_state == state


@parametrize_bridged
def test_edit_network_vlan_id(rconfig_mock, bridged):
    rconfig_mock.networks = {
        TESTNET1: create_network_config(
            'nic', IFACE0, bridged=bridged, vlan=VLAN101
        )
    }

    networks = {
        TESTNET1: create_network_config(
            'nic', IFACE0, bridged=bridged, vlan=VLAN102
        )
    }
    state = nmstate.generate_state(networks=networks, bondings={})
    vlan102_state = create_vlan_iface_state(IFACE0, VLAN102)
    disable_iface_ip(vlan102_state)
    base_nic_state = create_ethernet_iface_state(IFACE0)
    disable_iface_ip(base_nic_state)
    remove_vlan101_state = {
        nmstate.Interface.NAME: f'{IFACE0}.{VLAN101}',
        nmstate.Interface.STATE: nmstate.InterfaceState.ABSENT,
    }
    expected_state = {
        nmstate.Interface.KEY: [
            vlan102_state,
            remove_vlan101_state,
            base_nic_state,
        ]
    }
    if bridged:
        iface_bridge_state = create_bridge_iface_state(
            TESTNET1,
            f'{IFACE0}.{VLAN102}',
            options=generate_bridge_options(stp_enabled=False),
        )
        disable_iface_ip(iface_bridge_state)
        expected_state[nmstate.Interface.KEY].append(iface_bridge_state)

    sort_by_name(expected_state[nmstate.Interface.KEY])
    assert expected_state == state
