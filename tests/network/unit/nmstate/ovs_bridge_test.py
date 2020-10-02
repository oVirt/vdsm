#
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
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA  02110-1301 USA
#
# Refer to the README and COPYING files for full details of the license
#

import pytest

from network.compat import mock

from vdsm.network import nmstate
from vdsm.network.nmstate.ovs import network

from .testlib import (
    IFACE0,
    IFACE1,
    IPv4_ADDRESS1,
    IPv4_FAMILY,
    IPv4_NETMASK1,
    IPv4_PREFIX1,
    IPv6_ADDRESS1,
    IPv6_FAMILY,
    IPv6_PREFIX1,
    MAC_ADDRESS,
    OVS_BRIDGE,
    VLAN0,
    VLAN101,
    VLAN102,
    TESTBOND0,
    TESTNET1,
    TESTNET2,
    create_dynamic_ip_configuration,
    create_ethernet_iface_state,
    create_ipv4_state,
    create_ipv6_state,
    create_network_config,
    create_ovs_bridge_state,
    create_ovs_northbound_state,
    create_ovs_port_state,
    create_static_ip_configuration,
    disable_iface_ip,
    parametrize_bridged,
    parametrize_vlanned,
    sort_by_name,
)

parametrize_ip = pytest.mark.parametrize(
    'families',
    [(IPv4_FAMILY,), (IPv6_FAMILY,), (IPv4_FAMILY, IPv6_FAMILY)],
    ids=['IPv4', 'IPv6', 'IPv4&IPv6'],
)


@pytest.fixture(autouse=True)
def bridge_name_mock():
    with mock.patch.object(network, 'random_interface_name') as rnd:
        rnd.side_effect = OVS_BRIDGE
        yield


class TestBasicNetWithoutIp(object):
    @parametrize_bridged
    @pytest.mark.parametrize(
        'vlan', [VLAN0, VLAN101, None], ids=['vlan0', 'vlan101', 'non-vlan']
    )
    def test_add_single_net(self, bridged, vlan):
        networks = {
            TESTNET1: create_network_config(
                'nic', IFACE0, bridged, switch='ovs', vlan=vlan
            )
        }
        state = nmstate.generate_state(networks=networks, bondings={})

        eth0_state = create_ethernet_iface_state(IFACE0, mtu=None)

        bridge_ports = [
            create_ovs_port_state(IFACE0),
            create_ovs_port_state(TESTNET1, vlan=vlan),
        ]
        sort_by_name(bridge_ports)
        bridge_state = create_ovs_bridge_state(OVS_BRIDGE[0], bridge_ports)
        nb_state = create_ovs_northbound_state(TESTNET1)

        disable_iface_ip(eth0_state, nb_state)

        expected_state = {
            nmstate.Interface.KEY: [eth0_state, bridge_state, nb_state]
        }
        sort_by_name(expected_state[nmstate.Interface.KEY])
        assert expected_state == state

    @parametrize_bridged
    @parametrize_vlanned
    def test_add_nets(self, bridged, vlanned):
        vlan1 = VLAN101 if vlanned else None
        vlan2 = VLAN102 if vlanned else None
        networks = {
            TESTNET1: create_network_config(
                'nic', IFACE0, bridged, switch='ovs', vlan=vlan1
            ),
            TESTNET2: create_network_config(
                'nic', IFACE1, bridged, switch='ovs', vlan=vlan2
            ),
        }
        state = nmstate.generate_state(networks=networks, bondings={})

        eth0_state = create_ethernet_iface_state(IFACE0, mtu=None)
        eth1_state = create_ethernet_iface_state(IFACE1, mtu=None)

        bridge1_ports = [
            create_ovs_port_state(IFACE0),
            create_ovs_port_state(TESTNET1, vlan=vlan1),
        ]
        sort_by_name(bridge1_ports)
        bridge2_ports = [
            create_ovs_port_state(IFACE1),
            create_ovs_port_state(TESTNET2, vlan=vlan2),
        ]
        sort_by_name(bridge2_ports)
        bridge1_state = create_ovs_bridge_state(OVS_BRIDGE[0], bridge1_ports)
        bridge2_state = create_ovs_bridge_state(OVS_BRIDGE[1], bridge2_ports)
        nb1_state = create_ovs_northbound_state(TESTNET1)
        nb2_state = create_ovs_northbound_state(TESTNET2)

        disable_iface_ip(eth0_state, eth1_state, nb1_state, nb2_state)

        expected_state = {
            nmstate.Interface.KEY: [
                eth0_state,
                bridge1_state,
                nb1_state,
                eth1_state,
                bridge2_state,
                nb2_state,
            ]
        }
        sort_by_name(expected_state[nmstate.Interface.KEY])
        assert expected_state == state

    @parametrize_bridged
    @parametrize_vlanned
    def test_add_nets_over_single_sb(self, bridged, vlanned):
        vlan1 = VLAN101 if vlanned else None
        vlan2 = VLAN102 if vlanned else None
        networks = {
            TESTNET1: create_network_config(
                'nic', IFACE0, bridged, switch='ovs', vlan=vlan1
            ),
            TESTNET2: create_network_config(
                'nic', IFACE0, bridged, switch='ovs', vlan=vlan2
            ),
        }
        state = nmstate.generate_state(networks=networks, bondings={})

        eth0_state = create_ethernet_iface_state(IFACE0, mtu=None)

        bridge_ports = [
            create_ovs_port_state(IFACE0),
            create_ovs_port_state(TESTNET1, vlan=vlan1),
            create_ovs_port_state(TESTNET2, vlan=vlan2),
        ]
        sort_by_name(bridge_ports)
        bridge_state = create_ovs_bridge_state(OVS_BRIDGE[0], bridge_ports)
        nb1_state = create_ovs_northbound_state(TESTNET1)
        nb2_state = create_ovs_northbound_state(TESTNET2)

        disable_iface_ip(eth0_state, nb1_state, nb2_state)

        expected_state = {
            nmstate.Interface.KEY: [
                eth0_state,
                bridge_state,
                nb1_state,
                nb2_state,
            ]
        }
        sort_by_name(expected_state[nmstate.Interface.KEY])
        assert expected_state == state

    @parametrize_bridged
    @parametrize_vlanned
    def test_add_net_over_existing_bridge(
        self, bridged, vlanned, rconfig_mock, current_state_mock
    ):
        vlan = VLAN101 if vlanned else None
        rconfig_mock.networks = {
            TESTNET1: {'nic': IFACE0, 'bridged': bridged, 'switch': 'ovs'}
        }
        current_ifaces_states = current_state_mock[nmstate.Interface.KEY]
        current_ifaces_states.append(
            {
                'name': OVS_BRIDGE[5],
                nmstate.Interface.TYPE: nmstate.InterfaceType.OVS_BRIDGE,
                'state': 'up',
                nmstate.OvsBridgeSchema.CONFIG_SUBTREE: {
                    nmstate.OvsBridgeSchema.PORT_SUBTREE: [
                        {nmstate.OvsBridgeSchema.Port.NAME: TESTNET1},
                        {nmstate.OvsBridgeSchema.Port.NAME: IFACE0},
                    ]
                },
            }
        )
        networks = {
            TESTNET2: create_network_config(
                'nic', IFACE0, bridged, switch='ovs', vlan=vlan
            )
        }
        state = nmstate.generate_state(networks=networks, bondings={})

        bridge_ports = [
            create_ovs_port_state(IFACE0),
            create_ovs_port_state(TESTNET1),
            create_ovs_port_state(TESTNET2, vlan=vlan),
        ]
        sort_by_name(bridge_ports)
        bridge_state = create_ovs_bridge_state(OVS_BRIDGE[5], bridge_ports)
        nb_state = create_ovs_northbound_state(TESTNET2)

        disable_iface_ip(nb_state)

        expected_state = {nmstate.Interface.KEY: [bridge_state, nb_state]}
        sort_by_name(expected_state[nmstate.Interface.KEY])
        assert expected_state == state

    @parametrize_bridged
    @parametrize_vlanned
    def test_move_net_to_different_sb(
        self, bridged, vlanned, rconfig_mock, current_state_mock
    ):
        vlan = VLAN102 if vlanned else None
        rconfig_mock.networks = {
            TESTNET1: {'nic': IFACE0, 'bridged': bridged, 'switch': 'ovs'}
        }
        if vlanned:
            rconfig_mock.networks[TESTNET1]['vlan'] = VLAN101

        nb_port = {nmstate.OvsBridgeSchema.Port.NAME: TESTNET1}
        if vlanned:
            access = nmstate.OvsBridgeSchema.Port.Vlan.Mode.ACCESS
            nb_port[nmstate.OvsBridgeSchema.Port.VLAN_SUBTREE] = {
                nmstate.OvsBridgeSchema.Port.Vlan.MODE: access,
                nmstate.OvsBridgeSchema.Port.Vlan.TAG: VLAN101,
            }

        current_ifaces_states = current_state_mock[nmstate.Interface.KEY]
        current_ifaces_states.append(
            {
                'name': OVS_BRIDGE[5],
                nmstate.Interface.TYPE: nmstate.InterfaceType.OVS_BRIDGE,
                'state': 'up',
                nmstate.OvsBridgeSchema.CONFIG_SUBTREE: {
                    nmstate.OvsBridgeSchema.PORT_SUBTREE: [
                        nb_port,
                        {nmstate.OvsBridgeSchema.Port.NAME: IFACE0},
                    ]
                },
            }
        )
        networks = {
            TESTNET1: create_network_config(
                'nic', IFACE1, bridged, switch='ovs', vlan=vlan
            )
        }
        state = nmstate.generate_state(networks=networks, bondings={})

        eth0_state = create_ethernet_iface_state(IFACE0, mtu=None)
        eth1_state = create_ethernet_iface_state(IFACE1, mtu=None)
        bridge_ports = [
            create_ovs_port_state(IFACE1),
            create_ovs_port_state(TESTNET1, vlan=vlan),
        ]
        sort_by_name(bridge_ports)
        new_bridge_state = create_ovs_bridge_state(OVS_BRIDGE[0], bridge_ports)
        old_bridge_state = create_ovs_bridge_state(
            OVS_BRIDGE[5], None, 'absent'
        )
        nb_state = create_ovs_northbound_state(TESTNET1)

        disable_iface_ip(eth1_state, nb_state)

        expected_state = {
            nmstate.Interface.KEY: [
                eth0_state,
                eth1_state,
                new_bridge_state,
                old_bridge_state,
                nb_state,
            ]
        }
        sort_by_name(expected_state[nmstate.Interface.KEY])
        assert expected_state == state

    @parametrize_bridged
    def test_remove_last_net(self, bridged, rconfig_mock, current_state_mock):
        rconfig_mock.networks = {
            TESTNET1: {'nic': IFACE0, 'bridged': bridged, 'switch': 'ovs'}
        }
        current_ifaces_states = current_state_mock[nmstate.Interface.KEY]
        current_ifaces_states.append(
            {
                'name': OVS_BRIDGE[5],
                nmstate.Interface.TYPE: nmstate.InterfaceType.OVS_BRIDGE,
                'state': 'up',
                nmstate.OvsBridgeSchema.CONFIG_SUBTREE: {
                    nmstate.OvsBridgeSchema.PORT_SUBTREE: [
                        {nmstate.OvsBridgeSchema.Port.NAME: TESTNET1},
                        {nmstate.OvsBridgeSchema.Port.NAME: IFACE0},
                    ]
                },
            }
        )
        networks = {TESTNET1: {'remove': True}}
        state = nmstate.generate_state(networks=networks, bondings={})

        eth0_state = create_ethernet_iface_state(IFACE0, mtu=None)
        bridge_state = create_ovs_bridge_state(OVS_BRIDGE[5], None, 'absent')
        nb_state = {'name': TESTNET1, 'state': 'absent'}

        expected_state = {
            nmstate.Interface.KEY: [eth0_state, bridge_state, nb_state]
        }
        sort_by_name(expected_state[nmstate.Interface.KEY])
        assert expected_state == state

    @parametrize_bridged
    def test_remove_net(self, bridged, rconfig_mock, current_state_mock):
        rconfig_mock.networks = {
            TESTNET1: {'nic': IFACE0, 'bridged': bridged, 'switch': 'ovs'},
            TESTNET2: {'nic': IFACE0, 'bridged': bridged, 'switch': 'ovs'},
        }
        current_ifaces_states = current_state_mock[nmstate.Interface.KEY]
        current_ifaces_states.append(
            {
                'name': OVS_BRIDGE[5],
                nmstate.Interface.TYPE: nmstate.InterfaceType.OVS_BRIDGE,
                'state': 'up',
                nmstate.OvsBridgeSchema.CONFIG_SUBTREE: {
                    nmstate.OvsBridgeSchema.PORT_SUBTREE: [
                        {nmstate.OvsBridgeSchema.Port.NAME: TESTNET1},
                        {nmstate.OvsBridgeSchema.Port.NAME: TESTNET2},
                        {nmstate.OvsBridgeSchema.Port.NAME: IFACE0},
                    ]
                },
            }
        )
        networks = {TESTNET2: {'remove': True}}
        state = nmstate.generate_state(networks=networks, bondings={})

        nb_state = {'name': TESTNET2, 'state': 'absent'}

        expected_state = {nmstate.Interface.KEY: [nb_state]}
        sort_by_name(expected_state[nmstate.Interface.KEY])
        assert expected_state == state


class TestBasicNetWithIp(object):
    @parametrize_ip
    def test_dynamic_ip(self, families):
        dhcpv4 = IPv4_FAMILY in families
        dhcpv6 = IPv6_FAMILY in families

        networks = {
            TESTNET1: create_network_config(
                'nic',
                IFACE0,
                bridged=True,
                switch='ovs',
                dynamic_ip_configuration=create_dynamic_ip_configuration(
                    dhcpv4, dhcpv6, ipv6autoconf=dhcpv6
                ),
            )
        }
        state = nmstate.generate_state(networks=networks, bondings={})

        eth0_state = create_ethernet_iface_state(IFACE0, mtu=None)
        disable_iface_ip(eth0_state)

        bridge_ports = [
            create_ovs_port_state(IFACE0),
            create_ovs_port_state(TESTNET1),
        ]
        sort_by_name(bridge_ports)
        bridge_state = create_ovs_bridge_state(OVS_BRIDGE[0], bridge_ports)
        nb_state = create_ovs_northbound_state(TESTNET1)
        nb_state.update(create_ipv4_state(dynamic=dhcpv4, auto_dns=False))
        nb_state.update(create_ipv6_state(dynamic=dhcpv6, auto_dns=False))

        expected_state = {
            nmstate.Interface.KEY: [eth0_state, bridge_state, nb_state]
        }
        sort_by_name(expected_state[nmstate.Interface.KEY])
        assert expected_state == state

    @parametrize_ip
    def test_static_ip(self, families):
        ipv4 = IPv4_FAMILY in families
        ipv6 = IPv6_FAMILY in families

        ipv4_addr = IPv4_ADDRESS1 if ipv4 else None
        ipv4_netmask = IPv4_NETMASK1 if ipv4 else None
        ipv4_prefix = IPv4_PREFIX1 if ipv4 else None
        ipv6_addr = IPv6_ADDRESS1 if ipv6 else None
        ipv6_prefix = IPv6_PREFIX1 if ipv6 else None

        networks = {
            TESTNET1: create_network_config(
                'nic',
                IFACE0,
                bridged=True,
                switch='ovs',
                dynamic_ip_configuration=create_static_ip_configuration(
                    ipv4_addr, ipv4_netmask, ipv6_addr, ipv6_prefix
                ),
            )
        }
        state = nmstate.generate_state(networks=networks, bondings={})

        eth0_state = create_ethernet_iface_state(IFACE0, mtu=None)
        disable_iface_ip(eth0_state)

        bridge_ports = [
            create_ovs_port_state(IFACE0),
            create_ovs_port_state(TESTNET1),
        ]
        sort_by_name(bridge_ports)
        bridge_state = create_ovs_bridge_state(OVS_BRIDGE[0], bridge_ports)
        nb_state = create_ovs_northbound_state(TESTNET1)
        nb_state.update(create_ipv4_state(ipv4_addr, ipv4_prefix))
        nb_state.update(create_ipv6_state(ipv6_addr, ipv6_prefix))

        expected_state = {
            nmstate.Interface.KEY: [eth0_state, bridge_state, nb_state]
        }
        sort_by_name(expected_state[nmstate.Interface.KEY])
        assert expected_state == state


class TestEnforceMacAddress(object):
    @parametrize_bridged
    @parametrize_vlanned
    @pytest.mark.parametrize(
        'iface_type', ['nic', 'bonding'], ids=['over-nic', 'over-bond']
    )
    def test_net_over_existing_interface_enforce_mac(
        self, bridged, vlanned, iface_type, current_state_mock
    ):
        vlan = VLAN101 if vlanned else None

        if iface_type == 'nic':
            iface_name = IFACE0
            nmstate_iface_type = nmstate.InterfaceType.ETHERNET
        else:
            iface_name = TESTBOND0
            nmstate_iface_type = nmstate.InterfaceType.BOND

        current_ifaces_states = current_state_mock[nmstate.Interface.KEY]
        current_ifaces_states.append(
            {
                nmstate.Interface.NAME: iface_name,
                nmstate.Interface.TYPE: nmstate_iface_type,
                nmstate.Interface.MAC: MAC_ADDRESS,
            }
        )
        networks = {
            TESTNET1: create_network_config(
                iface_type, iface_name, bridged, switch='ovs', vlan=vlan
            )
        }
        state = nmstate.generate_state(networks=networks, bondings={})

        eth0_state = create_ethernet_iface_state(iface_name, mtu=None)

        bridge_ports = [
            create_ovs_port_state(iface_name),
            create_ovs_port_state(TESTNET1, vlan=vlan),
        ]
        sort_by_name(bridge_ports)
        bridge_state = create_ovs_bridge_state(OVS_BRIDGE[0], bridge_ports)
        nb_state = create_ovs_northbound_state(
            TESTNET1, enforced_mac=MAC_ADDRESS
        )

        disable_iface_ip(eth0_state, nb_state)

        expected_state = {
            nmstate.Interface.KEY: [eth0_state, bridge_state, nb_state]
        }
        sort_by_name(expected_state[nmstate.Interface.KEY])
        assert expected_state == state
