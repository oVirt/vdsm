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
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA
#
# Refer to the README and COPYING files for full details of the license
#

import ipaddress
import zlib

from copy import deepcopy

from vdsm.network.common.switch_util import SwitchType

from .schema import Route
from .schema import RouteRule

DEFAULT_TABLE_ID = 254


class Family(object):
    IPV4 = 4
    IPV6 = 6


class DefaultRouteDestination(object):
    IPV4 = '0.0.0.0/0'
    IPV6 = '::/0'

    @staticmethod
    def get_by_family(family):
        if family == Family.IPV4:
            return DefaultRouteDestination.IPV4
        if family == Family.IPV6:
            return DefaultRouteDestination.IPV6
        return None


class Routes(object):
    def __init__(self, netconf, runconf):
        self._netconf = netconf
        self._runconf = runconf
        self._state = self._create_routes()

    @property
    def state(self):
        return self._state

    def _create_routes(self):
        routes = []
        next_hop = _get_next_hop_interface(self._netconf)
        for family in (Family.IPV4, Family.IPV6):
            gateway = _get_gateway_by_ip_family(self._netconf, family)
            runconf_gateway = _get_gateway_by_ip_family(self._runconf, family)
            if gateway:
                routes.append(self._create_route(next_hop, gateway, family))
                if (
                    _gateway_has_changed(runconf_gateway, gateway)
                    and runconf_gateway
                ):
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

    def _should_remove_def_route(self, family):
        dhcp = (
            self._netconf.dhcpv4
            if family == Family.IPV4
            else self._netconf.dhcpv6
        )
        return (
            not self._netconf.remove
            and _get_gateway_by_ip_family(self._runconf, family)
            and self._runconf.default_route
            and (dhcp or not self._netconf.default_route)
        )

    @staticmethod
    def _create_add_default_route(next_hop_interface, gateway, family):
        return _create_route_state(
            next_hop_interface,
            gateway,
            DefaultRouteDestination.get_by_family(family),
        )

    @staticmethod
    def _create_remove_default_route(next_hop_interface, gateway, family):
        return _create_route_state(
            next_hop_interface,
            gateway,
            DefaultRouteDestination.get_by_family(family),
            absent=True,
        )


class SourceRoutes(object):
    def __init__(self, netconf, runconf, current_state):
        self._netconf = netconf
        self._runconf = runconf
        self._current_routes_state = deepcopy(current_state.routes_state)
        self._current_rules_state = deepcopy(current_state.rules_state)
        self._routes_state, self._rules_state = self._create_routes_and_rules()

    @property
    def routes_state(self):
        return self._routes_state

    @property
    def rules_state(self):
        return self._rules_state

    def _create_routes_and_rules(self):
        next_hop = _get_next_hop_interface(
            self._runconf if self._netconf.remove else self._netconf
        )
        gateway = _get_gateway_by_ip_family(self._netconf, Family.IPV4)
        runconf_gateway = _get_gateway_by_ip_family(self._runconf, Family.IPV4)
        routes, rules = self._create_remove_outdated_source_routes(
            next_hop, gateway, runconf_gateway
        )
        if gateway and _gateway_has_changed(runconf_gateway, gateway):
            helper = SourceRouteHelper(
                next_hop,
                self._netconf.ipv4addr,
                self._netconf.ipv4netmask,
                gateway,
            )
            routes.extend(helper.routes_state())
            rules.extend(helper.rules_state())
        return routes, rules

    def _create_remove_outdated_source_routes(
        self, next_hop, gateway, runconf_gateway
    ):
        if not (
            self._netconf.remove
            or self._is_changing_between_static_and_dynamic()
            or (
                runconf_gateway
                and _gateway_has_changed(runconf_gateway, gateway)
            )
        ):
            return [], []

        current_source_routes = self._find_current_source_routes(next_hop)
        current_route_rules = (
            self._find_current_route_rules(
                current_source_routes[0][Route.TABLE_ID]
            )
            if current_source_routes
            else []
        )
        for route in current_source_routes:
            route[Route.STATE] = Route.STATE_ABSENT
        for rule in current_route_rules:
            rule[RouteRule.STATE] = RouteRule.STATE_ABSENT

        return current_source_routes, current_route_rules

    def _find_current_source_routes(self, next_hop):
        return [
            r
            for r in self._current_routes_state
            if r.get(Route.NEXT_HOP_INTERFACE) == next_hop
            and r.get(Route.TABLE_ID) != DEFAULT_TABLE_ID
        ]

    def _find_current_route_rules(self, table_id):
        return [
            r
            for r in self._current_rules_state
            if r.get(RouteRule.ROUTE_TABLE) == table_id
        ]

    def _is_changing_between_static_and_dynamic(self):
        is_dynamic = self._netconf.dhcpv4
        is_static = self._netconf.ipv4addr is not None

        was_dynamic = self._runconf.dhcpv4
        was_static = self._runconf.ipv4addr is not None

        return (is_static and was_dynamic) or (is_dynamic and was_static)


# FIXME: Currently we are supporting only IPv4 source routing
class SourceRouteHelper(object):
    RULE_PRIORITY = 3200

    def __init__(self, next_hop, ipaddr, mask, gateway):
        self._next_hop = next_hop
        self._ipaddr = ipaddr
        self._mask = mask
        self._gateway = gateway

        self._table_id = generate_table_id(next_hop) if next_hop else None
        self._network = self._parse_network()

    def _parse_network(self):
        if not self._ipaddr or not self._mask:
            return None

        return str(
            ipaddress.ip_interface(f'{self._ipaddr}/{self._mask}').network
        )

    def routes_state(self):
        return [
            _create_route_state(
                self._next_hop,
                self._gateway,
                DefaultRouteDestination.IPV4,
                table_id=self._table_id,
            ),
            _create_route_state(
                self._next_hop,
                self._ipaddr,
                self._network,
                table_id=self._table_id,
            ),
        ]

    def rules_state(self):
        return [
            _create_route_rule_state(self._table_id, ip_from=self._network),
            _create_route_rule_state(self._table_id, ip_to=self._network),
        ]


def generate_table_id(next_hop):
    return zlib.adler32(next_hop.encode("utf-8"))


def _gateway_has_changed(runconf_gateway, netconf_gateway):
    return runconf_gateway != netconf_gateway


def _get_next_hop_interface(source):
    if source.switch == SwitchType.OVS or source.bridged:
        return source.name

    return source.vlan_iface or source.base_iface


def _get_gateway_by_ip_family(source, family):
    return source.gateway if family == Family.IPV4 else source.ipv6gateway


def _create_route_state(
    next_hop_interface,
    gateway,
    destination,
    absent=False,
    table_id=Route.USE_DEFAULT_ROUTE_TABLE,
):
    state = {
        Route.NEXT_HOP_ADDRESS: gateway,
        Route.NEXT_HOP_INTERFACE: next_hop_interface,
        Route.DESTINATION: destination,
        Route.TABLE_ID: table_id,
    }
    if absent:
        state[Route.STATE] = Route.STATE_ABSENT

    return state


def _create_route_rule_state(
    table_id,
    ip_from=None,
    ip_to=None,
    priority=SourceRouteHelper.RULE_PRIORITY,
):
    state = {RouteRule.ROUTE_TABLE: table_id, RouteRule.PRIORITY: priority}
    if ip_from:
        state[RouteRule.IP_FROM] = ip_from
    if ip_to:
        state[RouteRule.IP_TO] = ip_to

    return state
