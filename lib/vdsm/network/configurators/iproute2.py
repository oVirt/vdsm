# Copyright 2013-2017 Red Hat, Inc.
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

from vdsm.network import ifacetracking
from vdsm.network import ipwrapper
from vdsm.network.ip import address
from vdsm.network.ip import dhclient
from vdsm.network.ipwrapper import (routeAdd, routeDel, ruleAdd, ruleDel,
                                    IPRoute2Error)
from vdsm.network.link.bond import Bond
from vdsm.network.netinfo import bonding, vlans, bridges, mtus
from vdsm.network.netinfo.cache import ifaceUsed
from vdsm.constants import EXT_BRCTL
from vdsm.network.netconfpersistence import RunningConfig
from vdsm.utils import CommandPath
from vdsm.commands import execCmd

from . import Configurator, getEthtoolOpts
from ..errors import ConfigNetworkError, ERR_FAILED_IFUP, ERR_FAILED_IFDOWN
from ..models import Nic
from ..sourceroute import DynamicSourceRoute
from ..utils import remove_custom_bond_option

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
    def __init__(self, net_info, inRollback=False):
        is_unipersistence = True
        super(Iproute2, self).__init__(ConfigApplier(), net_info,
                                       is_unipersistence,
                                       inRollback)
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
        if bridge.ipv4.bootproto == 'dhcp':
            ifacetracking.add(bridge.name)
        self.configApplier.setIfaceConfigAndUp(bridge)
        self._addSourceRoute(bridge)
        if 'custom' in opts and 'bridge_opts' in opts['custom']:
            self.configApplier._setBridgeOpts(bridge,
                                              opts['custom']['bridge_opts'])

    def configureVlan(self, vlan, **opts):
        vlan.device.configure(**opts)
        self.configApplier.addVlan(vlan)
        if vlan.ipv4.bootproto == 'dhcp':
            ifacetracking.add(vlan.name)
        self.configApplier.setIfaceConfigAndUp(vlan)
        self._addSourceRoute(vlan)

    def configureBond(self, bond, **opts):
        self.configApplier.addBond(bond)
        if not bond.areOptionsApplied():
            self.configApplier.ifdown(bond)
            self.configApplier.addBondOptions(bond)
        for slave in bond.slaves:
            if slave.name not in Bond(bond.name).slaves:
                self.configApplier.addBondSlave(bond, slave)
                slave.configure(**opts)
        if bond.ipv4.bootproto == 'dhcp':
            ifacetracking.add(bond.name)
        self.configApplier.setIfaceConfigAndUp(bond)
        self._addSourceRoute(bond)
        self.runningConfig.setBonding(
            bond.name, {'options': bond.options,
                        'nics': sorted(slave.name for slave in bond.slaves),
                        'switch': 'legacy'})

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
                        'nics': sorted(slave.name for slave in bond.slaves),
                        'switch': 'legacy'})

    def configureNic(self, nic, **opts):
        if nic.ipv4.bootproto == 'dhcp':
            ifacetracking.add(nic.name)
        self.configApplier.setIfaceConfigAndUp(nic)
        self._addSourceRoute(nic)

        ethtool_opts = getEthtoolOpts(nic.name)
        if ethtool_opts:
            # We ignore ethtool's return code to maintain initscripts'
            # behaviour.
            execCmd(
                [_ETHTOOL_BINARY.cmd, '-K', nic.name] + ethtool_opts.split())

    def removeBridge(self, bridge):
        if bridge.ipv4.bootproto == 'dhcp':
            ifacetracking.add(bridge.name)
        self.configApplier.ifdown(bridge)
        self._removeSourceRoute(bridge)
        self.configApplier.removeBridge(bridge)
        if bridge.port:
            bridge.port.remove()

    def removeVlan(self, vlan):
        if vlan.ipv4.bootproto == 'dhcp':
            ifacetracking.add(vlan.name)
        self.configApplier.ifdown(vlan)
        self._removeSourceRoute(vlan)
        self.configApplier.removeVlan(vlan)
        vlan.device.remove()

    def _destroyBond(self, bonding):
        for slave in bonding.slaves:
            self.configApplier.removeBondSlave(bonding, slave)
            slave.remove()
        self.configApplier.removeBond(bonding)

    def removeBond(self, bonding):
        toBeRemoved = not ifaceUsed(bonding.name)

        if toBeRemoved:
            if bonding.master is None:
                address.flush(bonding.name)
                if bonding.ipv4.bootproto == 'dhcp':
                    ifacetracking.add(bonding.name)
                self._removeSourceRoute(bonding)

            if bonding.on_removal_just_detach_from_network:
                self.configApplier.setIfaceMtu(bonding.name, mtus.DEFAULT_MTU)
                self.configApplier.ifdown(bonding)
            else:
                self._destroyBond(bonding)
                self.runningConfig.removeBonding(bonding.name)
        else:
            self._setNewMtu(bonding, vlans.vlan_devs_for_iface(bonding.name))

    def removeNic(self, nic, remove_even_if_used=False):
        """
        Remove a nic from the kernel. By default, do nothing if the nic is used
        When remove_even_if_used=True, remove the nic anyway
        # FIXME the caller of this method is responsible to remove
        # the nic from its users (such as bond)
        """
        toBeRemoved = not ifaceUsed(nic.name) or remove_even_if_used

        if toBeRemoved:
            if nic.master is None:
                address.flush(nic.name)
                if nic.ipv4.bootproto == 'dhcp':
                    ifacetracking.add(nic.name)
                self._removeSourceRoute(nic)
            else:
                self.configApplier.setIfaceMtu(nic.name, mtus.DEFAULT_MTU)
                self.configApplier.ifdown(nic)
        else:
            self._setNewMtu(nic, vlans.vlan_devs_for_iface(nic.name))

    @staticmethod
    def configureSourceRoute(routes, rules, device):
        try:
            for route in routes:
                routeAdd(route)

            for rule in rules:
                ruleAdd(rule)

        except IPRoute2Error as e:
            logging.error('ip binary failed during source route configuration'
                          ': %s', e.message)

    @staticmethod
    def removeSourceRoute(routes, rules, device):
        try:
            for route in routes:
                try:
                    routeDel(route, family=4)
                except IPRoute2Error as e:
                    if 'No such process' in e.message[0]:
                        # The kernel or dhclient has won the race and removed
                        # the route already.
                        # We have yet to remove routing rules.
                        pass
                    else:
                        raise

            for rule in rules:
                ruleDel(rule)

        except IPRoute2Error as e:
            logging.error('ip binary failed during source route '
                          'removal: %s' % e.message)

    def _addSourceRoute(self, netEnt):
        ipv4 = netEnt.ipv4
        if ipv4.bootproto != 'dhcp' and netEnt.master is None:
            valid_args = (ipv4.address and ipv4.netmask and
                          ipv4.gateway not in (None, '0.0.0.0'))
            if valid_args:
                sroute = DynamicSourceRoute(netEnt.name, ipv4.address,
                                            ipv4.netmask, ipv4.gateway)
                self.configureSourceRoute(*sroute.requested_config())
            else:
                logging.warning(
                    'Invalid input for source routing: '
                    'name=%s, addr=%s, netmask=%s, gateway=%s',
                    netEnt.name, ipv4.address, ipv4.netmask, ipv4.gateway)

    def _removeSourceRoute(self, netEnt):
        if netEnt.ipv4.bootproto != 'dhcp' and netEnt.master is None:
            logging.debug("Removing source route for device %s", netEnt.name)
            sroute = DynamicSourceRoute(netEnt.name, None, None, None)
            self.removeSourceRoute(*sroute.current_config())


class ConfigApplier(object):

    def setIfaceMtu(self, iface, mtu):
        ipwrapper.linkSet(iface, ['mtu', str(mtu)])

    def setBondingMtu(self, iface, mtu):
        self.setIfaceMtu(iface, mtu)

    def ifup(self, iface):
        ipwrapper.linkSet(iface.name, ['up'])
        if iface.ipv4.bootproto == 'dhcp':
            dhclient.run(iface.name, 4, iface.ipv4.defaultRoute,
                         iface.duid_source, iface.blockingdhcp)
        if iface.ipv6.dhcpv6:
            dhclient.run(iface.name, 6, iface.ipv6.defaultRoute,
                         iface.duid_source, iface.blockingdhcp)

    def ifdown(self, iface):
        ipwrapper.linkSet(iface.name, ['down'])
        dhclient.stop(iface.name)

    def setIfaceConfigAndUp(self, iface):
        if iface.ipv4 or iface.ipv6:
            address.flush(iface.name)
            address.add(iface.name, iface.ipv4, iface.ipv6)
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
            with open(bridges.BRIDGING_OPT %
                      (bridge.name, 'stp_state'), 'w') as bridge_stp:
                bridge_stp.write('1')

    def addBridgePort(self, bridge):
        rc, _, err = execCmd([EXT_BRCTL, 'addif', bridge.name,
                              bridge.port.name])
        if rc != 0:
            raise ConfigNetworkError(ERR_FAILED_IFUP, err)

    def _setBridgeOpts(self, bridge, options):
        for key, value in (opt.split('=') for opt in options.split(' ')):
            with open(bridges.BRIDGING_OPT % (bridge.name, key), 'w') as f:
                f.write(value)

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
        if bond.name not in bonding.bondings():
            logging.debug('Add new bonding %s', bond)
            with open(bonding.BONDING_MASTERS, 'w') as f:
                f.write('+%s' % bond.name)

    def removeBond(self, bond):
        logging.debug('Remove bonding %s', bond)
        with open(bonding.BONDING_MASTERS, 'w') as f:
            f.write('-%s' % bond.name)

    def addBondSlave(self, bond, slave):
        logging.debug('Add slave %s to bonding %s', slave, bond)
        self.ifdown(slave)
        with open(bonding.BONDING_SLAVES % bond.name, 'w') as f:
            f.write('+%s' % slave.name)
        self.ifup(slave)

    def removeBondSlave(self, bond, slave):
        logging.debug('Remove slave %s from bonding %s', slave, bond)
        with open(bonding.BONDING_SLAVES % bond.name, 'w') as f:
            f.write('-%s' % slave.name)

    def addBondOptions(self, bond):
        logging.debug('Add bond options %s', bond.options)
        # 'custom' is not a real bond option, it just piggybacks custom values
        options = remove_custom_bond_option(bond.options)
        for option in options.split():
            key, value = option.split('=')
            with open(bonding.BONDING_OPT % (bond.name, key), 'w') as f:
                f.write(value)

    def networkBackup(self, network):
        pass
