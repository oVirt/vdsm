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

from .schema import Route


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
