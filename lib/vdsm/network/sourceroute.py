# Copyright 2013-2020 Red Hat, Inc.
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

import logging
import netaddr

from vdsm.network.ip import route as ip_route
from vdsm.network.ip import rule as ip_rule
from vdsm.network.ip.route import IPRouteData
from vdsm.network.ip.route import IPRouteError, IPRouteDeleteError
from vdsm.network.ip.rule import IPRuleData
from vdsm.network.ip.rule import IPRuleError


IPRoute = ip_route.driver(ip_route.Drivers.IPROUTE2)
IPRule = ip_rule.driver(ip_rule.Drivers.IPROUTE2)

RULE_PRIORITY = 32000


class StaticSourceRoute(object):
    def __init__(self, device, ipaddr, mask, gateway):
        self.device = device
        self._ipaddr = ipaddr
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

    def current_config(self):
        return (), (), self.device


class DynamicSourceRoute(StaticSourceRoute):
    @staticmethod
    def _getTable(rules):
        if rules:
            return rules[0].table
        else:
            logging.error("Table not found")
            return None

    def current_srconfig(self):
        """
        Returns the current source route configuration  associated with
        a device.
        This is a new version of the configuration report. It uses ip.route and
        ip.rule interfaces with the IPRouteData/IPRuleData structures.
        The configuration is aimed to be set/del by the ip.route/rule drivers
        through sourceroute.add() and sourceroute.remove() functions.
        """
        rules = self._sourceroute_rules()
        routes = self._sourceroute_routes(rules) if rules else ()
        return routes, rules

    def requested_srconfig(self):
        routes = [
            IPRouteData(
                to='0.0.0.0/0',
                via=self._gateway,
                family=4,
                device=self.device,
                table=self._table,
            ),
            IPRouteData(
                to=self._network,
                via=self._ipaddr,
                family=4,
                device=self.device,
                table=self._table,
            ),
        ]
        rules = [
            IPRuleData(
                src=self._network, table=self._table, prio=RULE_PRIORITY
            ),
            IPRuleData(
                to=self._network,
                table=self._table,
                iif=self.device,
                prio=RULE_PRIORITY,
            ),
        ]
        return routes, rules

    def _sourceroute_rules(self):
        sroute_rules = ()
        device_rules = [r for r in IPRule.rules() if r.iif == self.device]
        if device_rules:
            to = device_rules[0].to
            sroute_rules = tuple(
                device_rules + [r for r in IPRule.rules() if r.src == to]
            )
        return sroute_rules

    def _sourceroute_routes(self, rules):
        table = self._getTable(rules)
        return tuple(IPRoute.routes(table) or ()) if table else ()


def add(device, ip, mask, gateway):
    sroute = DynamicSourceRoute(device, ip, mask, gateway)
    routes, rules = sroute.requested_srconfig()
    logging.debug('Adding source route for device %s', device)
    try:
        for route in routes:
            IPRoute.add(route)
    except IPRouteError as e:
        if 'RTNETLINK answers: File exists' in e.args:
            logging.debug('Route already exists, addition failed,: %s', e.args)
        else:
            logging.error('Failed source route addition: %s', e.args)

    try:
        for rule in rules:
            IPRule.add(rule)
    except IPRuleError as e:
        logging.error('Failed source rule addition: %s', e.args)


def remove(device):
    sroute = DynamicSourceRoute(device, None, None, None)
    routes, rules = sroute.current_srconfig()
    logging.debug('Removing source route for device %s', device)
    try:
        for route in routes:
            try:
                # The kernel has won the race and removed the route already.
                IPRoute.delete(route)
            except IPRouteDeleteError:
                pass

        for rule in rules:
            IPRule.delete(rule)

    except (IPRouteError, IPRuleError) as e:
        logging.error('Failed source route removal: %s', e.args)
