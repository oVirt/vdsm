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

import logging

from vdsm import netinfo
from vdsm import ipwrapper
from vdsm.constants import EXT_BRCTL
from vdsm.ipwrapper import routeAdd
from vdsm.ipwrapper import routeDel
from vdsm.ipwrapper import ruleAdd
from vdsm.ipwrapper import ruleDel
from vdsm.netconfpersistence import RunningConfig
from vdsm.utils import CommandPath
from vdsm.utils import execCmd

from . import Configurator, getEthtoolOpts, libvirt
from .dhclient import DhcpClient
from ..errors import ConfigNetworkError, ERR_FAILED_IFUP, ERR_FAILED_IFDOWN
from ..models import Nic
from ..sourceroute import DynamicSourceRoute

_BRIDGING_OPT_PATH = '/sys/class/net/%s/bridge/%s'
_ETHTOOL_BINARY = CommandPath(
    'ethtool',
    '/usr/sbin/ethtool',  # F19+
    '/sbin/ethtool',  # EL6, ubuntu and Debian
    '/usr/bin/ethtool',  # Arch
)
_BRCTL_DEV_EXISTS = ("device %s already exists; can't create bridge with the "
                     "same name")


def is_available():
    return True


class Iproute2(Configurator):
    def __init__(self, inRollback=False):
        super(Iproute2, self).__init__(ConfigApplier(), inRollback)
        self.runningConfig = RunningConfig()

    def begin(self):
        if self.configApplier is None:
            self.configApplier = ConfigApplier()
        if self.runningConfig is None:
            self.runningConfig = RunningConfig()

    def commit(self):
        self.configApplier = None
        self.runningConfig.save()
        self.runningConfig = None

    def configureBridge(self, bridge, **opts):
        self.configApplier.addBridge(bridge)
        if bridge.port:
            bridge.port.configure(**opts)
            self.configApplier.addBridgePort(bridge)
        DynamicSourceRoute.addInterfaceTracking(bridge)
        self.configApplier.setIfaceConfigAndUp(bridge)
        self._addSourceRoute(bridge)
        if 'custom' in opts and 'bridge_opts' in opts['custom']:
            self.configApplier._setBridgeOpts(bridge,
                                              opts['custom']['bridge_opts'])

    def configureVlan(self, vlan, **opts):
        vlan.device.configure(**opts)
        self.configApplier.addVlan(vlan)
        DynamicSourceRoute.addInterfaceTracking(vlan)
        self.configApplier.setIfaceConfigAndUp(vlan)
        self._addSourceRoute(vlan)

    def configureBond(self, bond, **opts):
        self.configApplier.addBond(bond)
        if not bond.areOptionsApplied():
            self.configApplier.ifdown(bond)
            self.configApplier.addBondOptions(bond)
        for slave in bond.slaves:
            if slave.name not in netinfo.slaves(bond.name):
                self.configApplier.addBondSlave(bond, slave)
                slave.configure(**opts)
        DynamicSourceRoute.addInterfaceTracking(bond)
        self.configApplier.setIfaceConfigAndUp(bond)
        self._addSourceRoute(bond)
        self.runningConfig.setBonding(
            bond.name, {'options': bond.options,
                        'nics': [slave.name for slave in bond.slaves]})

    def editBonding(self, bond, _netinfo):
        """
        Modifies the bond so that the bond in the system ends up with the
        same slave and options configuration that are requested. Makes a
        best effort not to interrupt connectivity.
        """
        nicsToSet = frozenset(nic.name for nic in bond.slaves)
        currentNics = frozenset(_netinfo.getNicsForBonding(bond.name))
        nicsToAdd = nicsToSet
        nicsToRemove = currentNics

        if bond.areOptionsApplied():
            nicsToAdd -= currentNics
            nicsToRemove -= nicsToSet

        for nic in nicsToRemove:
            slave = Nic(nic, self, _netinfo=_netinfo)
            self.configApplier.removeBondSlave(bond, slave)
            slave.remove()

        if not bond.areOptionsApplied():
            self.configApplier.ifdown(bond)
            self.configApplier.addBondOptions(bond)

        for slave in bond.slaves:
            if slave.name in nicsToAdd:
                self.configApplier.addBondSlave(bond, slave)

        self.configApplier.ifup(bond)
        self.runningConfig.setBonding(
            bond.name, {'options': bond.options,
                        'nics': [slave.name for slave in bond.slaves]})

    def configureNic(self, nic, **opts):
        DynamicSourceRoute.addInterfaceTracking(nic)
        self.configApplier.setIfaceConfigAndUp(nic)
        self._addSourceRoute(nic)

        ethtool_opts = getEthtoolOpts(nic.name)
        if ethtool_opts:
            # We ignore ethtool's return code to maintain initscripts'
            # behaviour.
            execCmd(
                [_ETHTOOL_BINARY.cmd, '-K', nic.name] + ethtool_opts.split())

    def removeBridge(self, bridge):
        DynamicSourceRoute.addInterfaceTracking(bridge)
        self.configApplier.ifdown(bridge)
        self._removeSourceRoute(bridge, DynamicSourceRoute)
        self.configApplier.removeBridge(bridge)
        if bridge.port:
            bridge.port.remove()

    def removeVlan(self, vlan):
        DynamicSourceRoute.addInterfaceTracking(vlan)
        self.configApplier.ifdown(vlan)
        self._removeSourceRoute(vlan, DynamicSourceRoute)
        self.configApplier.removeVlan(vlan)
        vlan.device.remove()

    def _destroyBond(self, bonding):
        for slave in bonding.slaves:
            self.configApplier.removeBondSlave(bonding, slave)
            slave.remove()
        self.configApplier.removeBond(bonding)

    def removeBond(self, bonding):
        toBeRemoved = not netinfo.ifaceUsed(bonding.name)

        if toBeRemoved:
            if bonding.master is None:
                self.configApplier.removeIpConfig(bonding)
                DynamicSourceRoute.addInterfaceTracking(bonding)
                self._removeSourceRoute(bonding, DynamicSourceRoute)

            if bonding.destroyOnMasterRemoval:
                self._destroyBond(bonding)
                self.runningConfig.removeBonding(bonding.name)
            else:
                self.configApplier.setIfaceMtu(bonding.name,
                                               netinfo.DEFAULT_MTU)
                self.configApplier.ifdown(bonding)
        else:
            self._setNewMtu(bonding, netinfo.vlanDevsForIface(bonding.name))

    def removeNic(self, nic):
        toBeRemoved = not netinfo.ifaceUsed(nic.name)

        if toBeRemoved:
            if nic.master is None:
                self.configApplier.removeIpConfig(nic)
                DynamicSourceRoute.addInterfaceTracking(nic)
                self._removeSourceRoute(nic, DynamicSourceRoute)
            else:
                self.configApplier.setIfaceMtu(nic.name,
                                               netinfo.DEFAULT_MTU)
                self.configApplier.ifdown(nic)
        else:
            self._setNewMtu(nic, netinfo.vlanDevsForIface(nic.name))

    @staticmethod
    def configureSourceRoute(routes, rules, device):
        for route in routes:
            routeAdd(route)

        for rule in rules:
            ruleAdd(rule)

    @staticmethod
    def removeSourceRoute(routes, rules, device):
        for route in routes:
            routeDel(route)

        for rule in rules:
            ruleDel(rule)


class ConfigApplier(object):

    def _setIpConfig(self, iface):
        ipConfig = iface.ipConfig
        if ipConfig.ipaddr or ipConfig.ipv6addr:
            self.removeIpConfig(iface)
        if ipConfig.ipaddr:
            ipwrapper.addrAdd(iface.name, ipConfig.ipaddr,
                              ipConfig.netmask)
            if ipConfig.gateway and ipConfig.defaultRoute:
                ipwrapper.routeAdd(['default', 'via', ipConfig.gateway])
        if ipConfig.ipv6addr:
            ipv6addr, ipv6netmask = ipConfig.ipv6addr.split('/')
            ipwrapper.addrAdd(iface.name, ipv6addr, ipv6netmask, family=6)
            if ipConfig.ipv6gateway:
                ipwrapper.routeAdd(['default', 'via', ipConfig.ipv6gateway],
                                   dev=iface.name, family=6)
        if ipConfig.ipv6autoconf is not None:
            with open('/proc/sys/net/ipv6/conf/%s/autoconf' % iface.name,
                      'w') as ipv6_autoconf:
                ipv6_autoconf.write('1' if ipConfig.ipv6autoconf else '0')

    def removeIpConfig(self, iface):
        ipwrapper.addrFlush(iface.name)

    def setIfaceMtu(self, iface, mtu):
        ipwrapper.linkSet(iface, ['mtu', str(mtu)])

    def setBondingMtu(self, iface, mtu):
        self.setIfaceMtu(iface, mtu)

    def _runDhclient(self, iface, family=4):
        dhclient = DhcpClient(iface.name, family)
        rc = dhclient.start(iface.ipConfig.async)
        if not iface.ipConfig.async and rc:
            raise ConfigNetworkError(ERR_FAILED_IFUP, 'dhclient%s failed',
                                     family)

    def ifup(self, iface):
        ipwrapper.linkSet(iface.name, ['up'])
        if iface.ipConfig.bootproto == 'dhcp':
            self._runDhclient(iface)
        if iface.ipConfig.dhcpv6:
            self._runDhclient(iface, 6)

    def ifdown(self, iface):
        ipwrapper.linkSet(iface.name, ['down'])
        dhclient = DhcpClient(iface.name)
        dhclient.shutdown()

    def setIfaceConfigAndUp(self, iface):
        if iface.ip:
            self._setIpConfig(iface)
        if iface.mtu:
            self.setIfaceMtu(iface.name, iface.mtu)
        self.ifup(iface)

    def addBridge(self, bridge):
        rc, _, err = execCmd([EXT_BRCTL, 'addbr', bridge.name])
        if rc != 0:
            err_used_bridge = (_BRCTL_DEV_EXISTS % bridge.name == err[0]
                               if err else False)
            if not err_used_bridge:
                raise ConfigNetworkError(ERR_FAILED_IFUP, err)
        if bridge.stp:
            with open(netinfo.BRIDGING_OPT %
                      (bridge.name, 'stp_state'), 'w') as bridge_stp:
                bridge_stp.write('1')

    def addBridgePort(self, bridge):
        rc, _, err = execCmd([EXT_BRCTL, 'addif', bridge.name,
                              bridge.port.name])
        if rc != 0:
            raise ConfigNetworkError(ERR_FAILED_IFUP, err)

    def _setBridgeOpts(self, bridge, options):
        for key, value in (opt.split('=') for opt in options.split(' ')):
            with open(_BRIDGING_OPT_PATH % (bridge.name, key), 'w') as optFile:
                optFile.write(value)

    def removeBridge(self, bridge):
        rc, _, err = execCmd([EXT_BRCTL, 'delbr', bridge.name])
        if rc != 0:
            raise ConfigNetworkError(ERR_FAILED_IFDOWN, err)

    def addVlan(self, vlan):
        ipwrapper.linkAdd(name=vlan.name, linkType='vlan',
                          link=vlan.device.name, args=['id', str(vlan.tag)])

    def removeVlan(self, vlan):
        ipwrapper.linkDel(vlan.name)

    def addBond(self, bond):
        if bond.name not in netinfo.bondings():
            logging.debug('Add new bonding %s', bond)
            with open(netinfo.BONDING_MASTERS, 'w') as f:
                f.write('+%s' % bond.name)

    def removeBond(self, bond):
        logging.debug('Remove bonding %s', bond)
        with open(netinfo.BONDING_MASTERS, 'w') as f:
            f.write('-%s' % bond.name)

    def addBondSlave(self, bond, slave):
        logging.debug('Add slave %s to bonding %s', slave, bond)
        self.ifdown(slave)
        with open(netinfo.BONDING_SLAVES % bond.name, 'w') as f:
            f.write('+%s' % slave.name)
        self.ifup(slave)

    def removeBondSlave(self, bond, slave):
        logging.debug('Remove slave %s from bonding %s', slave, bond)
        with open(netinfo.BONDING_SLAVES % bond.name, 'w') as f:
            f.write('-%s' % slave.name)

    def addBondOptions(self, bond):
        logging.debug('Add bond options %s', bond.options)
        for option in bond.options.split():
            key, value = option.split('=')
            with open(netinfo.BONDING_OPT % (bond.name, key), 'w') as f:
                f.write(value)

    def createLibvirtNetwork(self, network, bridged, iface, qosInbound=None,
                             qosOutbound=None):
        netXml = libvirt.createNetworkDef(network, bridged, iface,
                                          qosInbound, qosOutbound)
        libvirt.createNetwork(netXml)

    def removeLibvirtNetwork(self, network):
        libvirt.removeNetwork(network)
