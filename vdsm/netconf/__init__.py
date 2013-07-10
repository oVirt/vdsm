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

import logging

from netmodels import Bond, Bridge
from sourceRoute import StaticSourceRoute
from sourceRoute import DynamicSourceRoute
from vdsm import netinfo


class Configurator(object):
    def configureBridge(self, bridge, **opts):
        raise NotImplementedError

    def configureVlan(self, vlan, **opts):
        raise NotImplementedError

    def configureBond(self, bond, **opts):
        raise NotImplementedError

    def editBonding(self, bond, _netinfo):
        raise NotImplementedError

    def configureNic(self, nic, **opts):
        raise NotImplementedError

    def removeBridge(self, bridge):
        raise NotImplementedError

    def removeVlan(self, vlan):
        raise NotImplementedError

    def removeBond(self, bonding):
        raise NotImplementedError

    def removeNic(self, nic):
        raise NotImplementedError

    def configureSourceRoute(self, routes, rules, device):
        raise NotImplementedError

    def removeSourceRoute(self, routes, rules, device):
        raise NotImplementedError

    def configureLibvirtNetwork(self, network, iface, qosInbound=None,
                                qosOutbound=None):
        self.configApplier.createLibvirtNetwork(network,
                                                isinstance(iface, Bridge),
                                                iface.name,
                                                qosInbound=qosInbound,
                                                qosOutbound=qosOutbound)
        self._libvirtAdded.add(network)

    def removeLibvirtNetwork(self, network):
        self.configApplier.removeLibvirtNetwork(network)

    def _addSourceRoute(self, netEnt, ipaddr, netmask, gateway, bootproto):
        # bootproto is None for both static and no bootproto
        if bootproto != 'dhcp' and netEnt.master is None:
            logging.debug("Adding source route %s, %s, %s, %s" %
                          (netEnt.name, ipaddr, netmask, gateway))
            StaticSourceRoute(netEnt.name, self).\
                configure(ipaddr, netmask, gateway)
        DynamicSourceRoute.addInterfaceTracking(netEnt)

    def _removeSourceRoute(self, netEnt):
        _, _, _, bootproto, _ = netEnt.getIpConfig()
        if bootproto != 'dhcp' and netEnt.master is None:
            logging.debug("Removing source route for device %s" % netEnt.name)
            StaticSourceRoute(netEnt.name, self).remove()

    def _setNewMtu(self, iface, ifaceVlans):
        """
        Update an interface's MTU when one of its users is removed.

        :param iface: interface object (bond or nic device)
        :type iface: NetDevice instance

        :param ifaceVlans: vlan devices using the interface 'iface'
        :type ifaceVlans: iterable

        """
        ifaceMtu = netinfo.getMtu(iface.name)
        maxMtu = netinfo.getMaxMtu(ifaceVlans, None)
        if maxMtu and maxMtu < ifaceMtu:
            if isinstance(iface, Bond):
                self.configApplier.setBondingMtu(iface.name, maxMtu)
            else:
                self.configApplier.setIfaceMtu(iface.name, maxMtu)
