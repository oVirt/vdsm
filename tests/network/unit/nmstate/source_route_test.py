# Copyright 2021 Red Hat, Inc.
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

from vdsm.network import nmstate
from vdsm.network.nmstate import route
from vdsm.network.nmstate.bridge_util import NetworkConfig

from .testlib import (
    IFACE0,
    IPv4_ADDRESS1,
    IPv4_ADDRESS2,
    IPv4_GATEWAY1,
    IPv4_GATEWAY2,
    IPv4_NETMASK1,
    IPv4_NETMASK2,
    TESTNET1,
    create_dynamic_ip_configuration,
    create_network_config,
    create_source_routes_and_rules_state,
    create_static_ip_configuration,
    parametrize_bridged,
)


class TestIpv4SourceRoute(object):
    @parametrize_bridged
    def test_remove_net_with_static_source_route(
        self, bridged, current_state_mock
    ):
        running_network = create_network_config(
            'nic',
            IFACE0,
            bridged,
            static_ip_configuration=create_static_ip_configuration(
                IPv4_ADDRESS1,
                IPv4_NETMASK1,
                ipv6_address=None,
                ipv6_prefix_length=None,
            ),
            gateway=IPv4_GATEWAY1,
        )
        routes_state, rules_state = create_source_routes_and_rules_state(
            TESTNET1 if bridged else IFACE0,
            IPv4_ADDRESS1,
            IPv4_NETMASK1,
            IPv4_GATEWAY1,
        )
        _mock_routes_and_rules(current_state_mock, routes_state, rules_state)

        source_routes = route.SourceRoutes(
            NetworkConfig(TESTNET1, {'remove': True}),
            NetworkConfig(TESTNET1, running_network),
            nmstate.get_current_state(),
        )

        _add_absent_to_state(routes_state, nmstate.Route)
        _add_absent_to_state(rules_state, nmstate.RouteRule)

        assert routes_state == source_routes.routes_state
        assert rules_state == source_routes.rules_state

    @parametrize_bridged
    def test_switch_from_static_to_dynamic(self, bridged, current_state_mock):
        running_network = create_network_config(
            'nic',
            IFACE0,
            bridged,
            static_ip_configuration=create_static_ip_configuration(
                IPv4_ADDRESS1,
                IPv4_NETMASK1,
                ipv6_address=None,
                ipv6_prefix_length=None,
            ),
            gateway=IPv4_GATEWAY1,
        )
        routes_state, rules_state = create_source_routes_and_rules_state(
            TESTNET1 if bridged else IFACE0,
            IPv4_ADDRESS1,
            IPv4_NETMASK1,
            IPv4_GATEWAY1,
        )
        _mock_routes_and_rules(current_state_mock, routes_state, rules_state)

        network = create_network_config(
            'nic',
            IFACE0,
            bridged,
            dynamic_ip_configuration=create_dynamic_ip_configuration(
                dhcpv4=True, dhcpv6=False, ipv6autoconf=False
            ),
        )

        source_routes = route.SourceRoutes(
            NetworkConfig(TESTNET1, network),
            NetworkConfig(TESTNET1, running_network),
            nmstate.get_current_state(),
        )

        _add_absent_to_state(routes_state, nmstate.Route)
        _add_absent_to_state(rules_state, nmstate.RouteRule)

        assert routes_state == source_routes.routes_state
        assert rules_state == source_routes.rules_state

    @parametrize_bridged
    def test_switch_from_dynamic_to_static(self, bridged, current_state_mock):
        running_network = create_network_config(
            'nic',
            IFACE0,
            bridged,
            dynamic_ip_configuration=create_dynamic_ip_configuration(
                dhcpv4=True, dhcpv6=False, ipv6autoconf=False
            ),
        )
        routes_state, rules_state = create_source_routes_and_rules_state(
            TESTNET1 if bridged else IFACE0,
            IPv4_ADDRESS2,
            IPv4_NETMASK2,
            IPv4_GATEWAY2,
        )
        _mock_routes_and_rules(current_state_mock, routes_state, rules_state)

        network = create_network_config(
            'nic',
            IFACE0,
            bridged,
            static_ip_configuration=create_static_ip_configuration(
                IPv4_ADDRESS1,
                IPv4_NETMASK1,
                ipv6_address=None,
                ipv6_prefix_length=None,
            ),
            gateway=IPv4_GATEWAY1,
        )

        source_routes = route.SourceRoutes(
            NetworkConfig(TESTNET1, network),
            NetworkConfig(TESTNET1, running_network),
            nmstate.get_current_state(),
        )

        _add_absent_to_state(routes_state, nmstate.Route)
        _add_absent_to_state(rules_state, nmstate.RouteRule)
        new_routes, new_rules = create_source_routes_and_rules_state(
            TESTNET1 if bridged else IFACE0,
            IPv4_ADDRESS1,
            IPv4_NETMASK1,
            IPv4_GATEWAY1,
        )
        routes_state.extend(new_routes)
        rules_state.extend(new_rules)

        assert routes_state == source_routes.routes_state
        assert rules_state == source_routes.rules_state

    @parametrize_bridged
    def test_update_gateway(self, bridged, current_state_mock):
        running_network = create_network_config(
            'nic',
            IFACE0,
            bridged,
            static_ip_configuration=create_static_ip_configuration(
                IPv4_ADDRESS1,
                IPv4_NETMASK1,
                ipv6_address=None,
                ipv6_prefix_length=None,
            ),
            gateway=IPv4_GATEWAY1,
        )
        routes_state, rules_state = create_source_routes_and_rules_state(
            TESTNET1 if bridged else IFACE0,
            IPv4_ADDRESS1,
            IPv4_NETMASK1,
            IPv4_GATEWAY1,
        )
        _mock_routes_and_rules(current_state_mock, routes_state, rules_state)

        network = create_network_config(
            'nic',
            IFACE0,
            bridged,
            static_ip_configuration=create_static_ip_configuration(
                IPv4_ADDRESS1,
                IPv4_NETMASK1,
                ipv6_address=None,
                ipv6_prefix_length=None,
            ),
            gateway=IPv4_GATEWAY2,
        )

        source_routes = route.SourceRoutes(
            NetworkConfig(TESTNET1, network),
            NetworkConfig(TESTNET1, running_network),
            nmstate.get_current_state(),
        )

        _add_absent_to_state(routes_state, nmstate.Route)
        _add_absent_to_state(rules_state, nmstate.RouteRule)
        new_routes, new_rules = create_source_routes_and_rules_state(
            TESTNET1 if bridged else IFACE0,
            IPv4_ADDRESS1,
            IPv4_NETMASK1,
            IPv4_GATEWAY2,
        )
        routes_state.extend(new_routes)
        rules_state.extend(new_rules)

        assert routes_state == source_routes.routes_state
        assert rules_state == source_routes.rules_state

    @parametrize_bridged
    def test_remove_gateway(self, bridged, current_state_mock):
        running_network = create_network_config(
            'nic',
            IFACE0,
            bridged,
            static_ip_configuration=create_static_ip_configuration(
                IPv4_ADDRESS1,
                IPv4_NETMASK1,
                ipv6_address=None,
                ipv6_prefix_length=None,
            ),
            gateway=IPv4_GATEWAY1,
        )
        routes_state, rules_state = create_source_routes_and_rules_state(
            TESTNET1 if bridged else IFACE0,
            IPv4_ADDRESS1,
            IPv4_NETMASK1,
            IPv4_GATEWAY1,
        )
        _mock_routes_and_rules(current_state_mock, routes_state, rules_state)

        network = create_network_config(
            'nic',
            IFACE0,
            bridged,
            static_ip_configuration=create_static_ip_configuration(
                IPv4_ADDRESS1,
                IPv4_NETMASK1,
                ipv6_address=None,
                ipv6_prefix_length=None,
            ),
        )

        source_routes = route.SourceRoutes(
            NetworkConfig(TESTNET1, network),
            NetworkConfig(TESTNET1, running_network),
            nmstate.get_current_state(),
        )

        _add_absent_to_state(routes_state, nmstate.Route)
        _add_absent_to_state(rules_state, nmstate.RouteRule)

        assert routes_state == source_routes.routes_state
        assert rules_state == source_routes.rules_state

    @parametrize_bridged
    def test_add_gateway(self, bridged, current_state_mock):
        running_network = create_network_config(
            'nic',
            IFACE0,
            bridged,
            static_ip_configuration=create_static_ip_configuration(
                IPv4_ADDRESS1,
                IPv4_NETMASK1,
                ipv6_address=None,
                ipv6_prefix_length=None,
            ),
        )

        network = create_network_config(
            'nic',
            IFACE0,
            bridged,
            static_ip_configuration=create_static_ip_configuration(
                IPv4_ADDRESS1,
                IPv4_NETMASK1,
                ipv6_address=None,
                ipv6_prefix_length=None,
            ),
            gateway=IPv4_GATEWAY1,
        )

        source_routes = route.SourceRoutes(
            NetworkConfig(TESTNET1, network),
            NetworkConfig(TESTNET1, running_network),
            nmstate.get_current_state(),
        )

        routes_state, rules_state = create_source_routes_and_rules_state(
            TESTNET1 if bridged else IFACE0,
            IPv4_ADDRESS1,
            IPv4_NETMASK1,
            IPv4_GATEWAY1,
        )

        assert routes_state == source_routes.routes_state
        assert rules_state == source_routes.rules_state


def _mock_routes_and_rules(current_state_mock, routes, rules):
    current_state_mock[nmstate.Route.KEY] = {nmstate.Route.RUNNING: routes}
    current_state_mock[nmstate.RouteRule.KEY] = {nmstate.Route.CONFIG: rules}


def _create_current_state(routes_state, rules_state):
    return {
        nmstate.Route.KEY: {nmstate.Route.CONFIG: routes_state},
        nmstate.RouteRule.KEY: {nmstate.RouteRule.CONFIG: rules_state},
    }


def _add_absent_to_state(state, type):
    for r in state:
        r[type.STATE] = type.STATE_ABSENT
