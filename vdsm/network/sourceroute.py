# Copyright 2013-2014 Red Hat, Inc.
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
import os

from glob import iglob
from libvirt import libvirtError
import logging
import netaddr

from vdsm import netinfo
from vdsm.constants import P_VDSM_RUN
from vdsm.ipwrapper import IPRoute2Error
from vdsm.ipwrapper import Route
from vdsm.ipwrapper import routeShowTable
from vdsm.ipwrapper import Rule
from vdsm.ipwrapper import ruleList
from vdsm.utils import rmFile


TRACKED_INTERFACES_FOLDER = P_VDSM_RUN + 'trackedInterfaces'


class StaticSourceRoute(object):
    def __init__(self, device, configurator, ipaddr, mask, gateway):
        self.device = device
        self._configurator = configurator
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

    def configure(self):
        logging.info(("Configuring gateway - ip: %s, network: %s, " +
                      "subnet: %s, gateway: %s, table: %s, device: %s") %
                     (self._ipaddr, self._network, self._mask, self._gateway,
                      self._table, self.device))

        routes = self._buildRoutes()
        rules = self._buildRules()

        try:
            self._configurator.configureSourceRoute(routes, rules, self.device)
        except IPRoute2Error as e:
            logging.error('ip binary failed during source route configuration'
                          ': %s', e.message)

    def remove(self):
        self._configurator.removeSourceRoute(None, None, self.device)


class DynamicSourceRoute(StaticSourceRoute):
    @staticmethod
    def getTrackingFilePath(device):
        return os.path.join(TRACKED_INTERFACES_FOLDER, device)

    @staticmethod
    def addInterfaceTracking(device):
        if device.ipConfig.bootproto == 'dhcp':
            open(DynamicSourceRoute.getTrackingFilePath(device.name), 'a').\
                close()

    @staticmethod
    def removeInterfaceTracking(device):
        rmFile(DynamicSourceRoute.getTrackingFilePath(device))

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

    def remove(self):
        logging.info("Removing gateway - device: %s", self.device)

        rules = self._getRules(self.device)
        if rules:
            table = self._getTable(rules)
            if table:
                try:
                    self._configurator.removeSourceRoute(
                        self._getRoutes(table), rules,
                        self.device)
                except IPRoute2Error as e:
                    logging.error('ip binary failed during source route '
                                  'removal: %s' % e.message)

    @staticmethod
    def _isLibvirtInterfaceFallback(device):
        """
        Checks whether the device belongs to libvirt when libvirt is not yet
        running (network.service runs before libvirtd is started). To do so,
        it must check if there is an autostart network that uses the device.
        """
        bridged_name = "bridge name='%s'" % device
        bridgeless_name = "interface dev='%s'" % device
        for filename in iglob('/etc/libvirt/qemu/networks/autostart/'
                              'vdsm-*'):
            with open(filename, 'r') as xml_file:
                xml_content = xml_file.read()
                if bridged_name in xml_content or \
                        bridgeless_name in xml_content:
                    return True
        return False

    @staticmethod
    def _isLibvirtInterface(device):
        try:
            networks = netinfo.networks()
        except libvirtError:  # libvirt might not be started or it just fails
            logging.error('Libvirt failed to answer. It might be the case that'
                          ' this script is being run before libvirt startup. '
                          ' Thus, check if vdsm owns %s an alternative way' %
                          device)
            return DynamicSourceRoute._isLibvirtInterfaceFallback(device)
        trackedInterfaces = [network.get('bridge') or network.get('iface')
                             for network in networks.itervalues()]
        return device in trackedInterfaces

    @staticmethod
    def isVDSMInterface(device):
        if os.path.exists(DynamicSourceRoute.getTrackingFilePath(device)):
            return True
        else:
            return DynamicSourceRoute._isLibvirtInterface(device)
