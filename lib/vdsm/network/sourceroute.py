# Copyright 2013-2016 Red Hat, Inc.
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

import logging
import netaddr

from vdsm.constants import P_VDSM_RUN

from .ipwrapper import Route
from .ipwrapper import routeShowTable
from .ipwrapper import Rule
from .ipwrapper import ruleList


TRACKED_INTERFACES_FOLDER = P_VDSM_RUN + 'trackedInterfaces'


class StaticSourceRoute(object):
    def __init__(self, device, ipaddr, mask, gateway):
        self.device = device
        self._ipaddr = ipaddr
        self._mask = mask
        self._gateway = gateway
        self._table = str(self._generateTableId(ipaddr)) if ipaddr else None
        self._network = self._parse_network(ipaddr, mask)

    def _parse_network(self, ipaddr, mask):
        if not ipaddr or not mask:
            return None
        network = netaddr.IPNetwork('%s/%s' % (ipaddr, mask))
        return "%s/%s" % (network.network, network.prefixlen)

    def _generateTableId(self, ipaddr):
        # TODO: Future proof for IPv6
        return netaddr.IPAddress(ipaddr).value

    def _buildRoutes(self):
        return [Route(network='0.0.0.0/0', via=self._gateway,
                      device=self.device, table=self._table),
                Route(network=self._network, via=self._ipaddr,
                      device=self.device, table=self._table)]

    def _buildRules(self):
        return [Rule(source=self._network, table=self._table),
                Rule(destination=self._network, table=self._table,
                     srcDevice=self.device)]

    def requested_config(self):
        return self._buildRoutes(), self._buildRules(), self.device

    def current_config(self):
        return (), (), self.device


class DynamicSourceRoute(StaticSourceRoute):

    @staticmethod
    def _getRoutes(table):
        routes = []
        for entry in routeShowTable('all'):
            try:
                route = Route.fromText(entry)
            except ValueError:
                logging.debug("Could not parse route %s", entry)
            else:
                if route.table == table:
                    routes.append(route)
        return routes

    @staticmethod
    def _getTable(rules):
        if rules:
            return rules[0].table
        else:
            logging.error("Table not found")
            return None

    @staticmethod
    def _getRules(device):
        """
            32764:	from all to 10.35.0.0/23 iif ovirtmgmt lookup 170066094
            32765:	from 10.35.0.0/23 lookup 170066094

            The first rule we'll find directly via the interface name
            We'll then use that rule's destination network, and use it
            to find the second rule via its source network
        """
        allRules = []
        for entry in ruleList():
            try:
                rule = Rule.fromText(entry)
            except ValueError:
                logging.debug("Could not parse rule %s", entry)
            else:
                allRules.append(rule)

        # Find the rule we put in place with 'device' as its 'srcDevice'
        rules = [r for r in allRules if r.srcDevice == device]

        if not rules:
            logging.error("Routing rules not found for device %s", device)
            return

        # Extract its destination network
        network = rules[0].destination

        # Find the other rule we put in place - It'll have 'network' as
        # its source
        rules += [r for r in allRules if r.source == network]

        return rules

    def current_config(self):
        rules = self._getRules(self.device) or ()
        table = self._getTable(rules) if rules else ()
        routes = self._getRoutes(table) if table else ()
        return routes, rules, self.device
