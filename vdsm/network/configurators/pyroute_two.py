# Copyright 2014 Robert Cernak
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

from vdsm import netinfo
from vdsm import ipwrapper
from vdsm.netconfpersistence import RunningConfig

from . import libvirt, runDhclient
from .dhclient import DhcpClient
from .iproute2 import Iproute2

try:
    from pyroute2 import IPDB
except ImportError as ie:
    _OPTIONAL_AVAILABLE = False
else:
    _OPTIONAL_AVAILABLE = True


def is_available():
    return _OPTIONAL_AVAILABLE


class PyrouteTwo(Iproute2):
    def __init__(self, inRollback=False):
        super(Iproute2, self).__init__(ConfigApplier(), inRollback)
        self.runningConfig = RunningConfig()

    def commit(self):
        self.configApplier.releaseSocket()
        self.configApplier = None
        self.runningConfig.save()
        self.runningConfig = None


class ConfigApplier(object):
    def __init__(self):
        self.ip = IPDB()

    def _setIpConfig(self, iface):
        ipConfig = iface.ipConfig
        if ipConfig.ipaddr or ipConfig.ipv6addr:
            self.removeIpConfig(iface)
        if ipConfig.ipaddr:
            with self.ip.interfaces[iface.name] as i:
                i.add_ip('%s/%s' % (ipConfig.ipaddr, ipConfig.netmask))
            if ipConfig.gateway and ipConfig.defaultRoute:
                self.ip.routes.add({'dst': 'default',
                                    'gateway': ipConfig.gateway}).commit()
        if ipConfig.ipv6addr:
            with self.ip.interfaces[iface.name] as i:
                i.add_ip(ipConfig.ipv6addr)
            if ipConfig.ipv6gateway:
                self.ip.routes.add({'dst': 'default',
                                    'gateway': ipConfig.ipv6gateway}).commit()
        if ipConfig.ipv6autoconf is not None:
            with open('/proc/sys/net/ipv6/conf/%s/autoconf' % iface.name,
                      'w') as ipv6_autoconf:
                ipv6_autoconf.write('1' if ipConfig.ipv6autoconf else '0')

    def removeIpConfig(self, iface):
        ipwrapper.addrFlush(iface.name)

    def setIfaceMtu(self, iface, mtu):
        with self.ip.interfaces[iface] as i:
            i['mtu'] = int(mtu)

    def setBondingMtu(self, iface, mtu):
        self.setIfaceMtu(iface, mtu)

    def ifup(self, iface):
        with self.ip.interfaces[iface.name] as i:
            i.up()
        if iface.ipConfig.bootproto == 'dhcp':
            runDhclient(iface)
        if iface.ipConfig.dhcpv6:
            runDhclient(iface, 6)

    def ifdown(self, iface):
        with self.ip.interfaces[iface.name] as i:
            i.down()
        dhclient = DhcpClient(iface.name)
        dhclient.shutdown()

    def setIfaceConfigAndUp(self, iface):
        if iface.ip:
            self._setIpConfig(iface)
        if iface.mtu:
            self.setIfaceMtu(iface.name, iface.mtu)
        self.ifup(iface)

    def addBridge(self, bridge):
        self.ip.create(kind='bridge', ifname=bridge.name).commit()

    def addBridgePort(self, bridge):
        with self.ip.interfaces[bridge.name] as i:
            i.add_port(self.ip.interfaces[bridge.port.name])

    def removeBridge(self, bridge):
        with self.ip.interfaces[bridge.name] as i:
            i.remove()

    def removeBridgePort(self, bridge):
        with self.ip.interfaces[bridge.name] as i:
            i.del_port(self.ip.interfaces[bridge.port.name])

    def addVlan(self, vlan):
        link = self.ip.interfaces[vlan.device.name].index
        self.ip.create(kind='vlan', ifname=vlan.name,
                       link=link, vlan_id=vlan.tag).commit()

    def removeVlan(self, vlan):
        with self.ip.interfaces[vlan.name] as i:
            i.remove()

    def addBond(self, bond):
        if bond.name not in netinfo.bondings():
            self.ip.create(kind='bond', ifname=bond.name).commit()

    def removeBond(self, bond):
        with self.ip.interfaces[bond.name] as i:
            i.remove()

    def addBondSlave(self, bond, slave):
        self.ifdown(slave)
        with self.ip.interfaces[bond.name] as i:
            i.add_port(self.ip.interfaces[slave.name])
        self.ifup(slave)

    def removeBondSlave(self, bond, slave):
        with self.ip.interfaces[bond.name] as i:
            i.del_port(self.ip.interfaces[slave.name])

    def addBondOptions(self, bond):
        logging.debug('Add bond options %s', bond.options)
        for option in bond.options.split():
            key, value = option.split('=')
            with open(netinfo.BONDING_OPT % (bond.name, key), 'w') as f:
                f.write(value)

    def createLibvirtNetwork(self, network, bridged=True, iface=None):
        netXml = libvirt.createNetworkDef(network, bridged, iface)
        libvirt.createNetwork(netXml)

    def removeLibvirtNetwork(self, network):
        libvirt.removeNetwork(network)

    def releaseSocket(self):
        self.ip.release()
