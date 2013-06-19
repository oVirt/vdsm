# Copyright 2013 Red Hat, Inc.
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

from argparse import ArgumentParser
from glob import iglob
from libvirt import libvirtError
import logging
import netaddr

from vdsm import netinfo
from vdsm.ipwrapper import Route
from vdsm.ipwrapper import routeLinkNetForDevice
from vdsm.ipwrapper import routeShowTable
from vdsm.ipwrapper import Rule
from vdsm.ipwrapper import ruleList


class StaticSourceRoute(object):
    def __init__(self, device, configurator):
        self.device = device
        self.configurator = configurator
        self.ipaddr = None
        self.mask = None
        self.gateway = None
        self.table = None
        self.network = None
        self.routes = None
        self.rules = None

    def _generateTableId(self):
        #TODO: Future proof for IPv6
        return netaddr.IPAddress(self.ipaddr).value

    def _buildRoutes(self):
        return [Route(network='0.0.0.0/0', ipaddr=self.gateway,
                      device=self.device, table=self.table),
                Route(network=self.network, ipaddr=self.ipaddr,
                      device=self.device, table=self.table)]

    def _buildRules(self):
        return [Rule(source=self.network, table=self.table),
                Rule(destination=self.network, table=self.table)]

    def configure(self, ipaddr, mask, gateway):
        if gateway in (None, '0.0.0.0') or not ipaddr or not mask:
            logging.error("ipaddr, mask or gateway not received")
            return

        self.ipaddr = ipaddr
        self.mask = mask
        self.gateway = gateway
        self.table = self._generateTableId()
        network = netaddr.IPNetwork(str(self.ipaddr) + '/' + str(self.mask))
        self.network = "%s/%s" % (network.network, network.prefixlen)

        logging.info(("Configuring gateway - ip: %s, network: %s, " +
                      "subnet: %s, gateway: %s, table: %s, device: %s") %
                     (self.ipaddr, self.network, self.mask, self.gateway,
                      self.table, self.device))

        self.routes = self._buildRoutes()
        self.rules = self._buildRules()

        self.configurator.configureSourceRoute(self.routes, self.rules,
                                               self.device)

    def _isLibvirtInterfaceFallback(self):
        """
        Checks whether the device belongs to libvirt when libvirt is not yet
        running (network.service runs before libvirtd is started). To do so,
        it must check if there is an autostart network that uses the device.
        """
        bridged_name = "bridge name='%s'" % self.device
        bridgeless_name = "interface dev='%s'" % self.device
        for filename in iglob('/etc/libvirt/qemu/networks/autostart/'
                              'vdsm-*'):
            with open(filename, 'r') as xml_file:
                xml_content = xml_file.read()
                if bridged_name in xml_content or \
                        bridgeless_name in xml_content:
                    return True
        return False

    def isLibvirtInterface(self):
        try:
            networks = netinfo.networks()
        except libvirtError:  # libvirt might not be started or it just fails
            logging.error('Libvirt failed to answer. It might be the case that'
                          ' this script is being run before libvirt startup. '
                          ' Thus, check if vdsm owns %s an alternative way' %
                          self.device)
            return self._isLibvirtInterfaceFallback()
        trackedInterfaces = [network.get('bridge') or network.get('iface')
                             for network in networks.itervalues()]
        return self.device in trackedInterfaces

    def remove(self):
        self.configurator.removeSourceRoute(None, None, self.device)


class DynamicSourceRoute(StaticSourceRoute):
    @staticmethod
    def _getRoutes(table, device):
        routes = []
        for entry in routeShowTable(table):
            """
            When displaying routes from a table, the table is omitted, so add
            it back again
            """
            try:
                route = Route.fromText(entry)
            except ValueError:
                pass
            else:
                route.table = table
                if route.device == device:
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
    def _getRules(network):
        allRules = [Rule.fromText(entry) for entry in ruleList()]
        rules = [rule for rule in allRules if rule.source == network or
                 rule.destination == network]

        if not rules:
            logging.error("Rules not found for network %s" % network)

        return rules

    @staticmethod
    def _getNetwork(device):
        output = routeLinkNetForDevice(device)

        if output:
            route = Route.parse(output[0])
            return route['network']
        logging.error("Network for given device name not found.")
        return None

    def remove(self):
        logging.info("Removing gateway - device: %s" % self.device)

        network = self._getNetwork(self.device)
        if network:
            rules = self._getRules(network)
            if rules:
                table = self._getTable(rules)
                if table:
                    self.configurator.removeSourceRoute(
                        self._getRoutes(table, self.device), rules,
                        self.device)


def main():
    parser = ArgumentParser()
    subparsers = parser.add_subparsers(
        title='actions', description='available source routing actions.',
        help='configure will set the source routing and remove will unset it',
        dest='action')

    configure_parser = subparsers.add_parser('configure')
    configure_parser.add_argument('bootproto',
                                  choices=('none', 'dhcp'),
                                  help='whether the IP information was '
                                  'acquired statically or via DHCP')
    configure_parser.add_argument('ip', help='base IP address of the network')
    configure_parser.add_argument('mask', help='Mask of the network either in '
                                  'prefix or in dot decimal notation')
    configure_parser.add_argument('gateway', help='IP address of the gateway')
    configure_parser.add_argument('device', help='Device to use for routing')

    remove_parser = subparsers.add_parser('remove')
    remove_parser.add_argument('bootproto', choices=('none', 'dhcp'),
                               help='whether the IP information was acquired '
                               'statically or via DHCP')
    remove_parser.add_argument('device', help='Device for which to unset the '
                               'routing')

    args = parser.parse_args()
    if args.bootproto == 'none':
        configurator = Ifcfg(ConfigWriter())
        sourceRoute = StaticSourceRoute(args.device, configurator)
    else:
        configurator = Iproute2()
        sourceRoute = DynamicSourceRoute(args.device, configurator)

    if not sourceRoute.isLibvirtInterface():
        logging.info("interface %s is not a libvirt interface" %
                     sourceRoute.device)
        return

    if args.action == 'configure':
        sourceRoute.configure(args.ip, args.mask, args.gateway)
    else:
        sourceRoute.remove()


if __name__ == "__main__":
    # This imports are here due to the fact that we only need to create
    # configurators if being used as a standalone script and because otherwise
    # when importing SourceRoute from the configurators, we'd get a circular
    # dependency.
    from netconf.ifcfg import ConfigWriter
    from netconf.ifcfg import Ifcfg
    from netconf.iproute2 import Iproute2
    main()
