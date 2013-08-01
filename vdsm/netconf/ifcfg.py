# Copyright 2011-2013 Red Hat, Inc.
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

import glob
import libvirt
import logging
import os
import pipes
import pwd
import re
import selinux
import shutil
import threading

import dsaversion
from netconf import Configurator
from neterrors import ConfigNetworkError
from netmodels import Nic, Bridge
from sourceRoute import DynamicSourceRoute
from storage.misc import execCmd
from vdsm import constants
from vdsm import netinfo
from vdsm import utils
import libvirtCfg
import neterrors as ne


class Ifcfg(Configurator):
    # TODO: Do all the configApplier interaction from here.
    def __init__(self):
        super(Ifcfg, self).__init__(ConfigWriter())

    def begin(self):
        if self.configApplier is None:
            self.configApplier = ConfigWriter()
            self._libvirtAdded = set()

    def rollback(self):
        self.configApplier.restoreBackups()

    def commit(self):
        if self.configApplier:
            self.configApplier = None
            self._libvirtAdded = set()

    def configureBridge(self, bridge, **opts):
        ipaddr, netmask, gateway, bootproto, async, _ = bridge.getIpConfig()
        self.configApplier.addBridge(bridge, **opts)
        ifdown(bridge.name)
        if bridge.port:
            bridge.port.configure(**opts)
        self._addSourceRoute(bridge, ipaddr, netmask, gateway, bootproto)
        ifup(bridge.name, async)

    def configureVlan(self, vlan, **opts):
        ipaddr, netmask, gateway, bootproto, async, _ = vlan.getIpConfig()
        self.configApplier.addVlan(vlan, **opts)
        vlan.device.configure(**opts)
        self._addSourceRoute(vlan, ipaddr, netmask, gateway, bootproto)
        ifup(vlan.name, async)

    def configureBond(self, bond, **opts):
        ipaddr, netmask, gateway, bootproto, async, _ = bond.getIpConfig()
        self.configApplier.addBonding(bond, **opts)
        if not netinfo.isVlanned(bond.name):
            for slave in bond.slaves:
                ifdown(slave.name)
        for slave in bond.slaves:
            slave.configure(**opts)
        self._addSourceRoute(bond, ipaddr, netmask, gateway, bootproto)
        ifup(bond.name, async)

    def editBonding(self, bond, _netinfo):
        ifdown(bond.name)
        for nic in _netinfo.getNicsForBonding(bond.name):
            Nic(nic, self, _netinfo=_netinfo).remove()
        bridgeName = _netinfo.getBridgedNetworkForIface(bond.name)
        if bridgeName:
            bond.master = Bridge(bridgeName, self, port=bond)
        self.configureBond(bond)

    def configureNic(self, nic, **opts):
        ipaddr, netmask, gateway, bootproto, async, _ = nic.getIpConfig()
        self.configApplier.addNic(nic, **opts)
        self._addSourceRoute(nic, ipaddr, netmask, gateway, bootproto)
        if nic.bond is None:
            if not netinfo.isVlanned(nic.name):
                ifdown(nic.name)
            ifup(nic.name, async)

    def removeBridge(self, bridge):
        DynamicSourceRoute.addInterfaceTracking(bridge)
        ifdown(bridge.name)
        self._removeSourceRoute(bridge)
        execCmd([constants.EXT_BRCTL, 'delbr', bridge.name])
        self.configApplier.removeBridge(bridge.name)
        if bridge.port:
            bridge.port.remove()

    def removeVlan(self, vlan):
        DynamicSourceRoute.addInterfaceTracking(vlan)
        ifdown(vlan.name)
        self._removeSourceRoute(vlan)
        self.configApplier.removeVlan(vlan.name)
        vlan.device.remove()

    def _ifaceDownAndCleanup(self, iface, _netinfo):
        """Returns True iff the iface is to be removed."""
        DynamicSourceRoute.addInterfaceTracking(iface)
        ifdown(iface.name)
        self._removeSourceRoute(iface)
        self.configApplier.removeIfaceCleanup(iface.name)
        return not _netinfo.ifaceUsers(iface.name)

    def removeBond(self, bonding):
        _netinfo = netinfo.NetInfo()
        to_be_removed = self._ifaceDownAndCleanup(bonding, _netinfo)
        if to_be_removed:
            if bonding.destroyOnMasterRemoval:
                self.configApplier.removeBonding(bonding.name)
                for slave in bonding.slaves:
                    slave.remove()
            else:
                self.configApplier.setBondingMtu(bonding.name,
                                                 netinfo.DEFAULT_MTU)
                ifup(bonding.name)
        else:
            self._setNewMtu(bonding,
                            _netinfo.getVlanDevsForIface(bonding.name))
            ifup(bonding.name)

    def removeNic(self, nic):
        _netinfo = netinfo.NetInfo()
        to_be_removed = self._ifaceDownAndCleanup(nic, _netinfo)
        if to_be_removed:
            self.configApplier.removeNic(nic.name)
        else:
            self._setNewMtu(nic, _netinfo.getVlanDevsForIface(nic.name))
        ifup(nic.name)

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


class ConfigWriter(object):
    CONFFILE_HEADER = '# Generated by VDSM version %s' %\
                      dsaversion.raw_version_revision
    DELETED_HEADER = '# original file did not exist'

    def __init__(self):
        self._backups = {}
        self._networksBackups = {}

    @staticmethod
    def _removeFile(filename):
        """Remove file, umounting ovirt config files if needed."""

        mounts = open('/proc/mounts').read()
        if ' /config ext3' in mounts and ' %s ext3' % filename in mounts:
            execCmd([constants.EXT_UMOUNT, '-n', filename])
        utils.rmFile(filename)
        logging.debug("Removed file %s", filename)

    def createLibvirtNetwork(self, network, bridged=True, iface=None,
                             skipBackup=False, qosInbound=None,
                             qosOutbound=None):
        netXml = libvirtCfg.createNetworkDef(network, bridged, iface,
                                             qosInbound=qosInbound,
                                             qosOutbound=qosOutbound)
        if not skipBackup:
            self._networkBackup(network)
        libvirtCfg.createNetwork(netXml)

    def removeLibvirtNetwork(self, network, skipBackup=False):
        if not skipBackup:
            self._networkBackup(network)
        libvirtCfg.removeNetwork(network)

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

        open(backup, 'w').write(content)
        os.chown(backup, vdsm_uid, 0)
        logging.debug("Persistently backed up %s "
                      "(until next 'set safe config')", backup)

    def _networkBackup(self, network):
        self._atomicNetworkBackup(network)
        self._persistentNetworkBackup(network)

    def _atomicNetworkBackup(self, network):
        """ In-memory backup libvirt networks """
        if network not in self._networksBackups:
            self._networksBackups[network] = libvirtCfg.getNetworkDef(network)
            logging.debug("Backed up %s", network)

    @classmethod
    def _persistentNetworkBackup(cls, network):
        """ Persistently backup libvirt networks """
        content = libvirtCfg.getNetworkDef(network)
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
                libvirtCfg.removeNetwork(network)
            except libvirt.libvirtError as e:
                if e.get_error_code() == libvirt.VIR_ERR_NO_NETWORK:
                    pass

            if content:
                libvirtCfg.createNetwork(content)

            logging.info('Restored %s', network)

    def _backup(self, filename):
        self._atomicBackup(filename)
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
        for confFile, content in self._backups.iteritems():
            if content is None:
                utils.rmFile(confFile)
                logging.debug('Removing empty configuration backup %s',
                              confFile)
            else:
                open(confFile, 'w').write(content)
            logging.info('Restored %s', confFile)

    def _devType(self, content):
        if re.search('^TYPE=Bridge$', content, re.MULTILINE):
            return "Bridge"
        elif re.search('^VLAN=yes$', content, re.MULTILINE):
            return "Vlan"
        elif re.search('^SLAVE=yes$', content, re.MULTILINE):
            return "Slave"
        else:
            return "Other"

    def _sortModifiedDeviceIfcfgs(self):
        devdict = {'Bridge': [],
                   'Vlan': [],
                   'Slave': [],
                   'Other': []}
        for confFile, _ in self._backups.iteritems():
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

    def _stopAtomicDevices(self):
        for dev in reversed(self._sortModifiedDeviceIfcfgs()):
            ifdown(dev)

    def _startAtomicDevices(self):
        for dev in self._sortModifiedDeviceIfcfgs():
            try:
                ifup(dev)
            except ConfigNetworkError:
                logging.error('Failed to ifup device %s during rollback.', dev,
                              exc_info=True)

    @classmethod
    def _persistentBackup(cls, filename):
        """ Persistently backup ifcfg-* config files """
        if os.path.exists('/usr/libexec/ovirt-functions'):
            execCmd([constants.EXT_SH, '/usr/libexec/ovirt-functions',
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

    def loadBackups(self):
        """ Load persistent backups into memory """
        # Load logical networks
        self._loadBackupFiles(netinfo.NET_LOGICALNET_CONF_BACK_DIR)
        # Load config files
        self._loadBackupFiles(netinfo.NET_CONF_BACK_DIR, netinfo.NET_CONF_DIR)

    def restoreBackups(self):
        """ Restore network backups """
        if not self._backups and not self._networksBackups:
            return

        self._stopAtomicDevices()

        self.restoreAtomicNetworkBackup()
        self.restoreAtomicBackup()

        self._startAtomicDevices()

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
        open(fileName, 'w').write(configuration)
        os.chmod(fileName, 0664)
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

    def _createConfFile(self, conf, name, ipaddr=None, netmask=None,
                        gateway=None, bootproto=None, mtu=None,
                        defaultRoute=None, onboot='yes', **kwargs):
        """ Create ifcfg-* file with proper fields per device """
        cfg = self.CONFFILE_HEADER + '\n'

        cfg += """DEVICE=%s\nONBOOT=%s\n""" % (pipes.quote(name),
                                               pipes.quote(onboot))
        cfg += conf
        if ipaddr:
            cfg = cfg + 'IPADDR=%s\nNETMASK=%s\n' % (pipes.quote(ipaddr),
                                                     pipes.quote(netmask))
            if gateway:
                cfg = cfg + 'GATEWAY=%s\n' % pipes.quote(gateway)
            # According to manual the BOOTPROTO=none should be set
            # for static IP
            cfg = cfg + 'BOOTPROTO=none\n'
        else:
            if bootproto:
                cfg = cfg + 'BOOTPROTO=%s\n' % pipes.quote(bootproto)
        if mtu:
            cfg = cfg + 'MTU=%d\n' % mtu
        if defaultRoute:
            cfg = cfg + 'DEFROUTE=%s\n' % defaultRoute
        cfg += 'NM_CONTROLLED=no\n'
        BLACKLIST = ['TYPE', 'NAME', 'DEVICE', 'bondingOptions',
                     'force', 'blockingdhcp',
                     'connectivityCheck', 'connectivityTimeout',
                     'implicitBonding']
        for k in set(kwargs.keys()).difference(set(BLACKLIST)):
            if re.match('^[a-zA-Z_]\w*$', k):
                cfg += '%s=%s\n' % (k.upper(), pipes.quote(kwargs[k]))
            else:
                logging.debug('ignoring variable %s', k)

        self.writeConfFile(netinfo.NET_CONF_PREF + name, cfg)

    def addBridge(self, bridge, **opts):
        """ Create ifcfg-* file with proper fields for bridge """
        ipaddr, netmask, gateway, bootproto, _, defaultRoute = \
            bridge.getIpConfig()
        conf = 'TYPE=Bridge\nDELAY=%s\n' % bridge.forwardDelay
        self._createConfFile(conf, bridge.name, ipaddr, netmask, gateway,
                             bootproto, bridge.mtu,
                             self._toIfcfgFormat(defaultRoute), **opts)

    def addVlan(self, vlan, **opts):
        """ Create ifcfg-* file with proper fields for VLAN """
        ipaddr, netmask, gateway, bootproto, _, defaultRoute = \
            vlan.getIpConfig()
        conf = 'VLAN=yes\n'
        if vlan.bridge:
            conf += 'BRIDGE=%s\n' % pipes.quote(vlan.bridge.name)

        self._createConfFile(conf, vlan.name, ipaddr, netmask, gateway,
                             bootproto, vlan.mtu,
                             self._toIfcfgFormat(defaultRoute), **opts)

    def addBonding(self, bond, **opts):
        """ Create ifcfg-* file with proper fields for bond """
        conf = 'BONDING_OPTS=%s\n' % pipes.quote(bond.options or '')
        if bond.bridge:
            conf += 'BRIDGE=%s\n' % pipes.quote(bond.bridge.name)

        ipaddr, netmask, gateway, bootproto, mtu, defaultRoute = \
            self._getIfaceConfValues(bond, netinfo.NetInfo())
        self._createConfFile(conf, bond.name, ipaddr, netmask, gateway,
                             bootproto, mtu, defaultRoute, **opts)

        # create the bonding device to avoid initscripts noise
        if bond.name not in open(netinfo.BONDING_MASTERS).read().split():
            open(netinfo.BONDING_MASTERS, 'w').write('+%s\n' % bond.name)

    def addNic(self, nic, **opts):
        """ Create ifcfg-* file with proper fields for NIC """
        _netinfo = netinfo.NetInfo()
        hwaddr = (_netinfo.nics[nic.name].get('permhwaddr') or
                  _netinfo.nics[nic.name]['hwaddr'])

        conf = 'HWADDR=%s\n' % pipes.quote(hwaddr)
        if nic.bridge:
            conf += 'BRIDGE=%s\n' % pipes.quote(nic.bridge.name)
        if nic.bond:
            conf += 'MASTER=%s\nSLAVE=yes\n' % pipes.quote(nic.bond.name)

        ipaddr, netmask, gateway, bootproto, mtu, defaultRoute = \
            self._getIfaceConfValues(nic, _netinfo)
        self._createConfFile(conf, nic.name, ipaddr, netmask, gateway,
                             bootproto, mtu, defaultRoute, **opts)

    @staticmethod
    def _getIfaceConfValues(iface, _netinfo):
        ipaddr, netmask, gateway, bootproto, _, defaultRoute = \
            iface.getIpConfig()
        defaultRoute = ConfigWriter._toIfcfgFormat(defaultRoute)
        mtu = iface.mtu
        if _netinfo.ifaceUsers(iface.name):
            confParams = netinfo.getIfaceCfg(iface.name)
            if not ipaddr and bootproto != 'dhcp':
                ipaddr = confParams.get('IPADDR', None)
                netmask = confParams.get('NETMASK', None)
                gateway = confParams.get('GATEWAY', None)
                bootproto = confParams.get('BOOTPROTO', None)
            if defaultRoute is None:
                defaultRoute = confParams.get('DEFROUTE', None)
            if not iface.mtu:
                mtu = confParams.get('MTU', None)
                if mtu:
                    mtu = int(mtu)
        return ipaddr, netmask, gateway, bootproto, mtu, defaultRoute

    def removeNic(self, nic):
        cf = netinfo.NET_CONF_PREF + nic
        self._backup(cf)
        try:
            hwlines = [line for line in open(cf).readlines()
                       if line.startswith('HWADDR=')]
            l = ['DEVICE=%s\n' % nic, 'ONBOOT=yes\n',
                 'MTU=%s\n' % netinfo.DEFAULT_MTU] + hwlines
            open(cf, 'w').writelines(l)
        except IOError:
            pass

    def removeVlan(self, vlan):
        self._backup(netinfo.NET_CONF_PREF + vlan)
        self._removeFile(netinfo.NET_CONF_PREF + vlan)

    def removeBonding(self, bonding):
        self._backup(netinfo.NET_CONF_PREF + bonding)
        self._removeFile(netinfo.NET_CONF_PREF + bonding)
        if bonding not in netinfo.REQUIRED_BONDINGS:
            with open(netinfo.BONDING_MASTERS, 'w') as f:
                f.write("-%s\n" % bonding)

    def removeBridge(self, bridge):
        self._backup(netinfo.NET_CONF_PREF + bridge)
        self._removeFile(netinfo.NET_CONF_PREF + bridge)

    def removeIfaceCleanup(self, iface):
        cf = netinfo.NET_CONF_PREF + iface
        self._removeConfigValues(cf, ('IPADDR', 'NETMASK', 'GATEWAY',
                                      'BOOTPROTO', 'BRIDGE'))

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

    def _removeConfigValues(self, conffile, entries):
        """Updates conffile by removing the specified entries."""
        removal_pattern = re.compile(r'=|'.join(entries) + '=')
        with open(conffile) as f:
            entries = [line for line in f.readlines()
                       if not removal_pattern.match(line)]

        self._backup(conffile)
        with open(conffile, 'w') as f:
            f.writelines(entries)
            f.close()

    def _updateConfigValue(self, conffile, entry, value):
        """
        Set value for network configuration file

        :param entry: entry to update (entry=value)
        :type entry: string

        :param value: value to update (entry=value)
        :type value: string

        Update conffile entry with the given value.
        """
        with open(conffile) as f:
            entries = [line for line in f.readlines()
                       if not line.startswith(entry + '=')]

        entries.append('\n' + entry + '=' + value)
        self._backup(conffile)
        with open(conffile, 'w') as f:
            f.writelines(entries)
            f.close()

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
    rc, out, err = execCmd([constants.EXT_IFDOWN, iface], raw=True)
    return rc


def ifup(iface, async=False):
    "Bring up an interface"
    def _ifup(netIf):
        rc, out, err = execCmd([constants.EXT_IFUP, netIf], raw=False)

        if rc != 0:
            # In /etc/sysconfig/network-scripts/ifup* the last line usually
            # contains the error reason.
            raise ConfigNetworkError(ne.ERR_FAILED_IFUP,
                                     out[-1] if out else '')
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
