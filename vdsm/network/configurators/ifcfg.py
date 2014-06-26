# Copyright 2011-2014 Red Hat, Inc.
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

import glob
import logging
import os
import pipes
import pwd
import re
import selinux
import shutil
import threading

from libvirt import libvirtError, VIR_ERR_NO_NETWORK

from vdsm.config import config
from vdsm import constants
from vdsm import ipwrapper
from vdsm import netinfo
from vdsm import utils
from vdsm.netconfpersistence import RunningConfig

if utils.isOvirtNode():
    from ovirt.node.utils import fs as node_fs

from . import Configurator, dhclient, getEthtoolOpts, libvirt
from ..errors import ConfigNetworkError, ERR_FAILED_IFUP
from ..models import Nic, Bridge, IpConfig
from ..sourceroute import StaticSourceRoute, DynamicSourceRoute
import dsaversion  # TODO: Make parent package import when vdsm is a package


def _hwaddr_required():
    return config.get('vars', 'hwaddr_in_ifcfg') == 'always'


class Ifcfg(Configurator):
    # TODO: Do all the configApplier interaction from here.
    def __init__(self, inRollback=False):
        self.unifiedPersistence = \
            config.get('vars', 'net_persistence') == 'unified'
        super(Ifcfg, self).__init__(ConfigWriter(self.unifiedPersistence),
                                    inRollback)
        if self.unifiedPersistence:
            self.runningConfig = RunningConfig()

    def begin(self):
        if self.configApplier is None:
            self.configApplier = ConfigWriter(self.unifiedPersistence)
        if self.unifiedPersistence and self.runningConfig is None:
            self.runningConfig = RunningConfig()

    def rollback(self):
        self.configApplier.restoreBackups()
        self.configApplier = None
        if self.unifiedPersistence:
            self.runningConfig = None

    def commit(self):
        self.configApplier = None
        if self.unifiedPersistence:
            self.runningConfig.save()
            self.runningConfig = None

    def configureBridge(self, bridge, **opts):
        self.configApplier.addBridge(bridge, **opts)
        ifdown(bridge.name)
        if bridge.port:
            bridge.port.configure(**opts)
        self._addSourceRoute(bridge)
        ifup(bridge.name, bridge.ipConfig.async)

    def configureVlan(self, vlan, **opts):
        self.configApplier.addVlan(vlan, **opts)
        vlan.device.configure(**opts)
        self._addSourceRoute(vlan)
        ifup(vlan.name, vlan.ipConfig.async)

    def configureBond(self, bond, **opts):
        self.configApplier.addBonding(bond, **opts)
        if not netinfo.isVlanned(bond.name):
            for slave in bond.slaves:
                ifdown(slave.name)
        for slave in bond.slaves:
            slave.configure(**opts)
        self._addSourceRoute(bond)
        ifup(bond.name, bond.ipConfig.async)
        if self.unifiedPersistence:
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
        nicsToAdd = nicsToSet - currentNics

        # Create bond configuration in case it was a non ifcfg controlled bond.
        # Needed to be before slave configuration for initscripts to add slave
        # to bond.
        bondIfcfgWritten = False
        isIfcfgControlled = os.path.isfile(netinfo.NET_CONF_PREF + bond.name)
        areOptionsApplied = bond.areOptionsApplied()
        if not isIfcfgControlled or not areOptionsApplied:
            bridgeName = _netinfo.getBridgedNetworkForIface(bond.name)
            if isIfcfgControlled and bridgeName:
                bond.master = Bridge(bridgeName, self, port=bond)
            self.configApplier.addBonding(bond)
            bondIfcfgWritten = True

        for nic in currentNics - nicsToSet:
            ifdown(nic)  # So that no users will be detected for it.
            Nic(nic, self, _netinfo=_netinfo).remove()

        for slave in bond.slaves:
            if slave.name in nicsToAdd:
                ifdown(slave.name)  # nics must be down to join a bond
                self.configApplier.addNic(slave)
                ifup(slave.name)

        if bondIfcfgWritten:
            ifdown(bond.name)
            ifup(bond.name)
        if self.unifiedPersistence:
            self.runningConfig.setBonding(
                bond.name, {'options': bond.options,
                            'nics': [slave.name for slave in bond.slaves]})

    def configureNic(self, nic, **opts):
        self.configApplier.addNic(nic, **opts)
        self._addSourceRoute(nic)
        if nic.bond is None:
            if not netinfo.isVlanned(nic.name):
                ifdown(nic.name)
            ifup(nic.name, nic.ipConfig.async)

    def removeBridge(self, bridge):
        DynamicSourceRoute.addInterfaceTracking(bridge)
        ifdown(bridge.name)
        self._removeSourceRoute(bridge, StaticSourceRoute)
        utils.execCmd([constants.EXT_BRCTL, 'delbr', bridge.name])
        self.configApplier.removeBridge(bridge.name)
        if bridge.port:
            bridge.port.remove()

    def removeVlan(self, vlan):
        DynamicSourceRoute.addInterfaceTracking(vlan)
        ifdown(vlan.name)
        self._removeSourceRoute(vlan, StaticSourceRoute)
        self.configApplier.removeVlan(vlan.name)
        vlan.device.remove()

    def _ifaceDownAndCleanup(self, iface):
        """Returns True iff the iface is to be removed."""
        DynamicSourceRoute.addInterfaceTracking(iface)
        to_be_removed = not netinfo.ifaceUsed(iface.name)
        if to_be_removed:
            ifdown(iface.name)
        self._removeSourceRoute(iface, StaticSourceRoute)
        return to_be_removed

    def _addSourceRoute(self, netEnt):
        """For ifcfg tracking can be done together with route/rule addition"""
        super(Ifcfg, self)._addSourceRoute(netEnt)
        DynamicSourceRoute.addInterfaceTracking(netEnt)

    def removeBond(self, bonding):
        to_be_removed = self._ifaceDownAndCleanup(bonding)
        if to_be_removed:
            self.configApplier.removeBonding(bonding.name)
            if bonding.destroyOnMasterRemoval:
                for slave in bonding.slaves:
                    slave.remove()
                if self.unifiedPersistence:
                    self.runningConfig.removeBonding(bonding.name)
            else:  # Recreate the bond with ip and master info cleared
                bonding.ip = bonding.master = None
                bonding.configure()
        else:
            set_mtu = self._setNewMtu(bonding,
                                      netinfo.vlanDevsForIface(bonding.name))
            # Since we are not taking the device up again, ifcfg will not be
            # read at this point and we need to set the live mtu value.
            # Note that ip link set dev bondX mtu Y sets Y on all its links
            if set_mtu is not None:
                ipwrapper.linkSet(bonding.name, ['mtu', str(set_mtu)])

    def removeNic(self, nic):
        to_be_removed = self._ifaceDownAndCleanup(nic)
        if to_be_removed:
            self.configApplier.removeNic(nic.name)
            if nic.name in netinfo.nics():
                ifup(nic.name)
            else:
                logging.warning('host interface %s missing', nic.name)
        else:
            set_mtu = self._setNewMtu(nic, netinfo.vlanDevsForIface(nic.name))
            # Since we are not taking the device up again, ifcfg will not be
            # read at this point and we need to set the live mtu value
            if set_mtu is not None:
                ipwrapper.linkSet(nic.name, ['mtu', str(set_mtu)])

    def _getFilePath(self, fileType, device):
        return os.path.join(netinfo.NET_CONF_DIR, '%s-%s' % (fileType, device))

    def _removeSourceRouteFile(self, fileType, device):
        filePath = self._getFilePath(fileType, device)
        self.configApplier._backup(filePath)
        self.configApplier._removeFile(filePath)

    def _writeConfFile(self, contents, fileType, device):
        filePath = self._getFilePath(fileType, device)

        configuration = ''
        for entry in contents:
            configuration += str(entry) + '\n'

        self.configApplier.writeConfFile(filePath, configuration)

    def configureSourceRoute(self, routes, rules, device):
        self._writeConfFile(routes, 'route', device)
        self._writeConfFile(rules, 'rule', device)

    def removeSourceRoute(self, routes, rules, device):
        self._removeSourceRouteFile('rule', device)
        self._removeSourceRouteFile('route', device)

    def flush(self):
        super(Ifcfg, self).flush()
        self.configApplier.flush()


class ConfigWriter(object):
    CONFFILE_HEADER_BASE = '# Generated by VDSM version'
    CONFFILE_HEADER = CONFFILE_HEADER_BASE + ' %s' % \
        dsaversion.raw_version_revision
    DELETED_HEADER = '# original file did not exist'

    def __init__(self, unifiedPersistence=False):
        self._backups = {}
        self._networksBackups = {}
        self.unifiedPersistence = unifiedPersistence

    def flush(self):
        """Removes all owned ifcfg, route and rule files."""
        ownedIfcfgFiles = list(self._ownedFiles())
        sortedDeviceIfcfgFiles = self._sortDeviceIfcfgs(ownedIfcfgFiles)
        self._stopDevices(sortedDeviceIfcfgFiles)
        for fpath in ownedIfcfgFiles:
            self._removeFile(fpath)

    @staticmethod
    def _removeFile(filename):
        """Remove file (directly or using oVirt node's library)"""
        if utils.isOvirtNode():
            node_fs.Config().delete(filename)  # unpersists and shreds the file
        else:
            utils.rmFile(filename)
        logging.debug("Removed file %s", filename)

    def createLibvirtNetwork(self, network, bridged=True, iface=None,
                             skipBackup=False, qosInbound=None,
                             qosOutbound=None):
        netXml = libvirt.createNetworkDef(network, bridged, iface,
                                          qosInbound=qosInbound,
                                          qosOutbound=qosOutbound)
        if not skipBackup:
            self._networkBackup(network)
        libvirt.createNetwork(netXml)

    def removeLibvirtNetwork(self, network, skipBackup=False):
        if not skipBackup:
            self._networkBackup(network)
        libvirt.removeNetwork(network)

    @classmethod
    def writeBackupFile(cls, dirName, fileName, content):
        backup = os.path.join(dirName, fileName)
        if os.path.exists(backup):
            # original copy already backed up
            return

        vdsm_uid = pwd.getpwnam('vdsm').pw_uid

        # make directory (if it doesn't exist) and assign it to vdsm
        if not os.path.exists(dirName):
            os.makedirs(dirName)
        os.chown(dirName, vdsm_uid, 0)

        with open(backup, 'w') as backupFile:
            backupFile.write(content)
        os.chown(backup, vdsm_uid, 0)
        logging.debug("Persistently backed up %s "
                      "(until next 'set safe config')", backup)

    def _networkBackup(self, network):
        self._atomicNetworkBackup(network)
        if config.get('vars', 'net_persistence') != 'unified':
            self._persistentNetworkBackup(network)

    def _atomicNetworkBackup(self, network):
        """ In-memory backup libvirt networks """
        if network not in self._networksBackups:
            self._networksBackups[network] = libvirt.getNetworkDef(network)
            logging.debug("Backed up %s", network)

    @classmethod
    def _persistentNetworkBackup(cls, network):
        """ Persistently backup libvirt networks """
        content = libvirt.getNetworkDef(network)
        if not content:
            # For non-exists networks use predefined header
            content = cls.DELETED_HEADER + '\n'
        logging.debug("backing up network %s: %s", network, content)

        cls.writeBackupFile(netinfo.NET_LOGICALNET_CONF_BACK_DIR, network,
                            content)

    def restoreAtomicNetworkBackup(self):
        logging.info("Rolling back logical networks configuration "
                     "(restoring atomic logical networks backup)")
        for network, content in self._networksBackups.iteritems():
            # Networks with content None should be removed.
            # Networks with real content should be recreated.
            # To avoid libvirt errors during recreation we need
            # to remove the old network first
            try:
                libvirt.removeNetwork(network)
            except libvirtError as e:
                if e.get_error_code() == VIR_ERR_NO_NETWORK:
                    pass

            if content:
                libvirt.createNetwork(content)

            logging.info('Restored %s', network)

    def _backup(self, filename):
        self._atomicBackup(filename)
        if config.get('vars', 'net_persistence') != 'unified':
            self._persistentBackup(filename)

    def _atomicBackup(self, filename):
        """
        Backs up configuration to memory,
        for a later rollback in case of error.
        """

        if filename not in self._backups:
            try:
                self._backups[filename] = open(filename).read()
                logging.debug("Backed up %s", filename)
            except IOError as e:
                if e.errno == os.errno.ENOENT:
                    self._backups[filename] = None
                else:
                    raise

    def restoreAtomicBackup(self):
        logging.info("Rolling back configuration (restoring atomic backup)")
        for confFilePath, content in self._backups.iteritems():
            if content is None:
                utils.rmFile(confFilePath)
                logging.debug('Removing empty configuration backup %s',
                              confFilePath)
            else:
                with open(confFilePath, 'w') as confFile:
                    confFile.write(content)
            logging.info('Restored %s', confFilePath)

    def _devType(self, content):
        if re.search('^TYPE=Bridge$', content, re.MULTILINE):
            return "Bridge"
        elif re.search('^VLAN=yes$', content, re.MULTILINE):
            return "Vlan"
        elif re.search('^SLAVE=yes$', content, re.MULTILINE):
            return "Slave"
        else:
            return "Other"

    def _sortDeviceIfcfgs(self, deviceIfcfgs):
        devdict = {'Bridge': [],
                   'Vlan': [],
                   'Slave': [],
                   'Other': []}
        for confFile in deviceIfcfgs:
            if not confFile.startswith(netinfo.NET_CONF_PREF):
                continue
            try:
                content = file(confFile).read()
            except IOError as e:
                if e.errno == os.errno.ENOENT:
                    continue
                else:
                    raise
            dev = confFile[len(netinfo.NET_CONF_PREF):]

            devdict[self._devType(content)].append(dev)

        return devdict['Other'] + devdict['Vlan'] + devdict['Bridge']

    def _stopDevices(self, deviceIfcfgs):
        for dev in reversed(deviceIfcfgs):
            ifdown(dev)

    def _startDevices(self, deviceIfcfgs):
        for dev in deviceIfcfgs:
            try:
                ifup(dev)
            except ConfigNetworkError:
                logging.error('Failed to ifup device %s during rollback.', dev,
                              exc_info=True)

    @classmethod
    def _persistentBackup(cls, filename):
        """ Persistently backup ifcfg-* config files """
        if os.path.exists('/usr/libexec/ovirt-functions'):
            utils.execCmd([constants.EXT_SH, '/usr/libexec/ovirt-functions',
                           'unmount_config', filename])
            logging.debug("unmounted %s using ovirt", filename)

        (dummy, basename) = os.path.split(filename)
        if os.path.exists(filename):
            content = open(filename).read()
        else:
            # For non-exists ifcfg-* file use predefined header
            content = cls.DELETED_HEADER + '\n'
        logging.debug("backing up %s: %s", basename, content)

        cls.writeBackupFile(netinfo.NET_CONF_BACK_DIR, basename, content)

    def restorePersistentBackup(self):
        """Restore network config to last known 'safe' state"""

        self.loadBackups()
        self.restoreBackups()
        self.clearBackups()

    def _loadBackupFiles(self, loadDir, restoreDir=None):
        for fpath in glob.iglob(loadDir + '/*'):
            if not os.path.isfile(fpath):
                continue

            content = open(fpath).read()
            if content.startswith(self.DELETED_HEADER):
                content = None

            basename = os.path.basename(fpath)
            if restoreDir:
                self._backups[os.path.join(restoreDir, basename)] = content
            else:
                self._networksBackups[basename] = content

            logging.info('Loaded %s', fpath)

    def _ownedFiles(self):
        for fpath in glob.iglob(netinfo.NET_CONF_DIR + '/*'):
            if not os.path.isfile(fpath):
                continue
            try:
                with open(fpath) as confFile:
                    content = confFile.read()
            except IOError as e:
                if e.errno == os.errno.ENOENT:
                    continue
                else:
                    raise
            if content.startswith(self.CONFFILE_HEADER_BASE):
                yield fpath

    def loadBackups(self):
        """ Load persistent backups into memory """
        # Load logical networks
        self._loadBackupFiles(netinfo.NET_LOGICALNET_CONF_BACK_DIR)
        # Load config files
        self._loadBackupFiles(netinfo.NET_CONF_BACK_DIR, netinfo.NET_CONF_DIR)

    def restoreBackups(self):
        """ Restore network backups from memory."""
        if not self._backups and not self._networksBackups:
            return

        self._stopDevices(self._sortDeviceIfcfgs(self._backups.iterkeys()))

        self.restoreAtomicNetworkBackup()
        self.restoreAtomicBackup()

        self._startDevices(self._sortDeviceIfcfgs(self._backups.iterkeys()))

    @classmethod
    def clearBackups(cls):
        """ Clear backup files """
        for fpath in glob.iglob(netinfo.NET_CONF_BACK_DIR + "*"):
            if os.path.isdir(fpath):
                shutil.rmtree(fpath)
            else:
                os.remove(fpath)

    @classmethod
    def ifcfgPorts(cls, network):
        ports = []
        for filePath in glob.iglob(netinfo.NET_CONF_PREF + '*'):
            with open(filePath, 'r') as confFile:
                for line in confFile:
                    if line.startswith('BRIDGE=' + network):
                        port = filePath[filePath.rindex('-') + 1:]
                        logging.debug('port %s found in ifcfg for %s', port,
                                      network)
                        ports.append(port)
                        break
        return ports

    def writeConfFile(self, fileName, configuration):
        '''Backs up the previous contents of the file referenced by fileName
        writes the new configuration and sets the specified access mode.'''
        self._backup(fileName)
        configuration = self.CONFFILE_HEADER + '\n' + configuration
        logging.debug('Writing to file %s configuration:\n%s', fileName,
                      configuration)
        with open(fileName, 'w') as confFile:
            confFile.write(configuration)
        os.chmod(fileName, 0o664)
        try:
            selinux.restorecon(fileName)
        except:
            logging.debug('ignoring restorecon error in case '
                          'SElinux is disabled', exc_info=True)

    @staticmethod
    def _toIfcfgFormat(defaultRoute):
        if defaultRoute is None:
            return None
        return 'yes' if defaultRoute else 'no'

    def _createConfFile(self, conf, name, ipconfig, mtu=None, **kwargs):
        """ Create ifcfg-* file with proper fields per device """

        cfg = """DEVICE=%s\n""" % pipes.quote(name)
        cfg += conf
        if ipconfig.ipaddr:
            cfg = cfg + 'IPADDR=%s\n' % pipes.quote(ipconfig.ipaddr)
            cfg = cfg + 'NETMASK=%s\n' % pipes.quote(ipconfig.netmask)
            if ipconfig.gateway:
                cfg = cfg + 'GATEWAY=%s\n' % pipes.quote(ipconfig.gateway)
            # According to manual the BOOTPROTO=none should be set
            # for static IP
            cfg = cfg + 'BOOTPROTO=none\n'
        elif ipconfig.bootproto:
            cfg = cfg + 'BOOTPROTO=%s\n' % pipes.quote(ipconfig.bootproto)
            if (ipconfig.bootproto == 'dhcp' and
                    os.path.exists(os.path.join(netinfo.NET_PATH, name))):
                # Ask dhclient to stop any dhclient running for the device
                dhclient.kill_dhclient(name)

        if mtu:
            cfg = cfg + 'MTU=%d\n' % mtu
        if ipconfig.defaultRoute:
            cfg = cfg + 'DEFROUTE=%s\n' % ipconfig.defaultRoute
        cfg += 'NM_CONTROLLED=no\n'
        if ipconfig.ipv6addr or ipconfig.ipv6autoconf or ipconfig.dhcpv6:
            cfg += 'IPV6INIT=yes\n'
            if ipconfig.ipv6addr is not None:
                cfg += 'IPV6ADDR=%s\n' % pipes.quote(ipconfig.ipv6addr)
                if ipconfig.ipv6gateway is not None:
                    cfg += 'IPV6_DEFAULTGW=%s\n' % \
                        pipes.quote(ipconfig.ipv6gateway)
            elif ipconfig.dhcpv6:
                cfg += 'DHCPV6C=yes\n'
            if ipconfig.ipv6autoconf:
                cfg += 'IPV6_AUTOCONF=%s\n' % 'yes'
            else:
                cfg += 'IPV6_AUTOCONF=%s\n' % 'no'
        BLACKLIST = ['TYPE', 'NAME', 'DEVICE', 'VLAN', 'bondingOptions',
                     'force', 'blockingdhcp', 'custom',
                     'connectivityCheck', 'connectivityTimeout',
                     'implicitBonding', 'delay', 'onboot', 'forward_delay',
                     'DELAY', 'ONBOOT']
        for k in set(kwargs.keys()).difference(set(BLACKLIST)):
            if re.match('^[a-zA-Z_]\w*$', k):
                cfg += '%s=%s\n' % (k.upper(), pipes.quote(kwargs[k]))
            else:
                logging.debug('ignoring variable %s', k)

        self.writeConfFile(netinfo.NET_CONF_PREF + name, cfg)

    def addBridge(self, bridge, **opts):
        """ Create ifcfg-* file with proper fields for bridge """
        conf = 'TYPE=Bridge\nDELAY=0\n'
        opts['hotplug'] = 'no'  # So that udev doesn't trigger an ifup
        if bridge.stp is not None:
            conf += 'STP=%s\n' % ('on' if bridge.stp else 'off')
        ipconfig = bridge.ipConfig
        if not self.unifiedPersistence or ipconfig.defaultRoute:
            conf += 'ONBOOT=%s\n' % 'yes'
        else:
            conf += 'ONBOOT=%s\n' % 'no'
        defaultRoute = ConfigWriter._toIfcfgFormat(ipconfig.defaultRoute)
        ipconfig = ipconfig._replace(defaultRoute=defaultRoute)

        if 'custom' in opts and 'bridge_opts' in opts['custom']:
            opts['bridging_opts'] = opts['custom']['bridge_opts']
        self._createConfFile(conf, bridge.name, ipconfig, bridge.mtu, **opts)

    def addVlan(self, vlan, **opts):
        """ Create ifcfg-* file with proper fields for VLAN """
        conf = 'VLAN=yes\n'
        opts['hotplug'] = 'no'  # So that udev doesn't trigger an ifup
        if vlan.bridge:
            conf += 'BRIDGE=%s\n' % pipes.quote(vlan.bridge.name)
        if not self.unifiedPersistence or vlan.serving_default_route:
            conf += 'ONBOOT=%s\n' % 'yes'
        else:
            conf += 'ONBOOT=%s\n' % 'no'
        ipconfig = vlan.ipConfig
        defaultRoute = ConfigWriter._toIfcfgFormat(ipconfig.defaultRoute)
        ipconfig = ipconfig._replace(defaultRoute=defaultRoute)
        self._createConfFile(conf, vlan.name, ipconfig, vlan.mtu, **opts)

    def addBonding(self, bond, **opts):
        """ Create ifcfg-* file with proper fields for bond """
        conf = 'BONDING_OPTS=%s\n' % pipes.quote(bond.options or '')
        opts['hotplug'] = 'no'  # So that udev doesn't trigger an ifup
        if bond.bridge:
            conf += 'BRIDGE=%s\n' % pipes.quote(bond.bridge.name)
        if not self.unifiedPersistence or bond.serving_default_route:
            conf += 'ONBOOT=%s\n' % 'yes'
        else:
            conf += 'ONBOOT=%s\n' % 'no'

        ipconfig, mtu = self._getIfaceConfValues(bond)
        self._createConfFile(conf, bond.name, ipconfig, mtu, **opts)

        # create the bonding device to avoid initscripts noise
        if bond.name not in open(netinfo.BONDING_MASTERS).read().split():
            with open(netinfo.BONDING_MASTERS, 'w') as bondingMasters:
                bondingMasters.write('+%s\n' % bond.name)

    def addNic(self, nic, **opts):
        """ Create ifcfg-* file with proper fields for NIC """
        conf = ''
        if _hwaddr_required():
            _netinfo = netinfo.NetInfo()
            hwaddr = (_netinfo.nics[nic.name].get('permhwaddr') or
                      _netinfo.nics[nic.name]['hwaddr'])

            conf += 'HWADDR=%s\n' % pipes.quote(hwaddr)
        if nic.bridge:
            conf += 'BRIDGE=%s\n' % pipes.quote(nic.bridge.name)
        if nic.bond:
            conf += 'MASTER=%s\nSLAVE=yes\n' % pipes.quote(nic.bond.name)
        if not self.unifiedPersistence or nic.serving_default_route:
            conf += 'ONBOOT=%s\n' % 'yes'
        else:
            conf += 'ONBOOT=%s\n' % 'no'

        ethtool_opts = getEthtoolOpts(nic.name)
        if ethtool_opts:
            conf += 'ETHTOOL_OPTS=%s\n' % pipes.quote(ethtool_opts)

        ipconfig, mtu = self._getIfaceConfValues(nic)
        self._createConfFile(conf, nic.name, ipconfig, mtu, **opts)

    @staticmethod
    def _getIfaceConfValues(iface):
        ipaddr, netmask, gateway, defaultRoute, ipv6addr, ipv6gateway, \
            ipv6defaultRoute, bootproto, async, ipv6autoconf, dhcpv6 = \
            iface.ipConfig
        defaultRoute = ConfigWriter._toIfcfgFormat(defaultRoute)
        mtu = iface.mtu
        if netinfo.ifaceUsed(iface.name):
            confParams = netinfo.getIfaceCfg(iface.name)
            if not ipaddr and bootproto != 'dhcp':
                ipaddr = confParams.get('IPADDR', None)
                netmask = confParams.get('NETMASK', None)
                gateway = confParams.get('GATEWAY', None)
                bootproto = bootproto or confParams.get('BOOTPROTO', None)
            defaultRoute = defaultRoute or confParams.get('DEFROUTE', None)
            if confParams.get('IPV6INIT', 'no') == 'yes':
                ipv6addr = confParams.get('IPV6ADDR', None)
                ipv6gateway = confParams.get('IPV6_DEFAULTGW', None)
                ipv6autoconf = (confParams.get('IPV6_AUTOCONF', 'no') == 'yes')
                dhcpv6 = (confParams.get('DHCPV6C', 'no') == 'yes')
            if not iface.mtu:
                mtu = confParams.get('MTU', None)
                if mtu:
                    mtu = int(mtu)
        ipconfig = IpConfig.ipConfig(ipaddr, netmask, gateway, defaultRoute,
                                     ipv6addr, ipv6gateway, ipv6defaultRoute,
                                     bootproto, async, ipv6autoconf, dhcpv6)
        return ipconfig, mtu

    def removeNic(self, nic):
        cf = netinfo.NET_CONF_PREF + nic
        self._backup(cf)
        with open(cf) as nicFile:
            hwlines = [line for line in nicFile if line.startswith('HWADDR=')]
        l = [self.CONFFILE_HEADER + '\n', 'DEVICE=%s\n' % nic, 'ONBOOT=yes\n',
             'MTU=%s\n' % netinfo.DEFAULT_MTU] + hwlines
        l += 'NM_CONTROLLED=no\n'
        with open(cf, 'w') as nicFile:
            nicFile.writelines(l)

    def removeVlan(self, vlan):
        self._backup(netinfo.NET_CONF_PREF + vlan)
        self._removeFile(netinfo.NET_CONF_PREF + vlan)

    def removeBonding(self, bonding):
        self._backup(netinfo.NET_CONF_PREF + bonding)
        self._removeFile(netinfo.NET_CONF_PREF + bonding)
        with open(netinfo.BONDING_MASTERS, 'w') as f:
            f.write("-%s\n" % bonding)

    def removeBridge(self, bridge):
        self._backup(netinfo.NET_CONF_PREF + bridge)
        self._removeFile(netinfo.NET_CONF_PREF + bridge)

    def _getConfigValue(self, conffile, entry):
        """
        Get value from network configuration file

        :param entry: entry to look for (entry=value)
        :type entry: string

        :returns: value for entry (or None)
        :rtype: string

        Search for entry in conffile and return
        its value or None if not found
        """
        with open(conffile) as f:
            entries = [line for line in f.readlines()
                       if line.startswith(entry + '=')]
        if len(entries) != 0:
            value = entries[0].split('=', 1)[1]
            return value.strip()
        return None

    def _updateConfigValue(self, conffile, entry, value):
        """
        Set value for network configuration file. If value is None, remove the
        entry from conffile.

        :param entry: entry to update (entry=value)
        :type entry: string

        :param value: value to update (entry=value)
        :type value: string

        Update conffile entry with the given value.
        """
        with open(conffile) as f:
            entries = [line for line in f.readlines()
                       if not line.startswith(entry + '=')]

        if value is not None:
            entries.append('\n' + entry + '=' + value)
        self._backup(conffile)
        with open(conffile, 'w') as f:
            f.writelines(entries)

    def setIfaceMtu(self, iface, newmtu):
        cf = netinfo.NET_CONF_PREF + iface
        self._updateConfigValue(cf, 'MTU', str(newmtu))

    def setBondingMtu(self, bonding, newmtu):
        self.setIfaceMtu(bonding, newmtu)
        slaves = netinfo.slaves(bonding)
        for slave in slaves:
            self.setIfaceMtu(slave, newmtu)


def ifdown(iface):
    "Bring down an interface"
    rc, out, err = utils.execCmd([constants.EXT_IFDOWN, iface], raw=True)
    return rc


def ifup(iface, async=False):
    "Bring up an interface"
    def _ifup(netIf):
        rc, out, err = utils.execCmd([constants.EXT_IFUP, netIf], raw=False)

        if rc != 0:
            # In /etc/sysconfig/network-scripts/ifup* the last line usually
            # contains the error reason.
            raise ConfigNetworkError(ERR_FAILED_IFUP, out[-1] if out else '')
        return rc, out, err

    if async:
        # wait for dhcp in another thread,
        # so vdsm won't get stuck (BZ#498940)
        t = threading.Thread(target=_ifup, name='ifup-waiting-on-dhcp',
                             args=(iface,))
        t.daemon = True
        t.start()
    else:
        rc, out, err = _ifup(iface)
        return rc
