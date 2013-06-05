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

from xml.sax.saxutils import escape
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

from neterrors import ConfigNetworkError
from storage.misc import execCmd
from vdsm import constants
from vdsm import libvirtconnection
from vdsm import netinfo
from vdsm import utils
from netmodels import Bridge
import neterrors as ne


class Ifcfg(object):
    # TODO: Do all the configWriter interaction from here.
    def __init__(self, configWriter=None):
        self.configWriter = configWriter
        self._libvirtAdded = set()

    def begin(self):
        if self.configWriter is None:
            self.configWriter = ConfigWriter()
            self._libvirtAdded = set()

    def rollback(self):
        if self.configWriter:
            self.configWriter.restoreBackups()
            for network in self._libvirtAdded:
                # TODO: Add meaningful logging for failure to remove the added
                # networks.
                self.configWriter.removeLibvirtNetwork(network)

            self.configWriter = None

    def commit(self):
        if self.configWriter:
            self.configWriter = None
            self._libvirtAdded = set()

    def configureBridge(self, bridge, **opts):
        ipaddr, netmask, gateway, bootproto, async = bridge.getIpConfig()
        self.configWriter.addBridge(bridge.name, ipaddr=ipaddr,
                                    netmask=netmask, mtu=bridge.mtu,
                                    gateway=gateway, bootproto=bootproto,
                                    **opts)
        ifdown(bridge.name)
        if bridge.port:
            bridge.port.configure(bridge=bridge.name, **opts)
        ifup(bridge.name, async)

    def configureVlan(self, vlan, bridge=None, **opts):
        ipaddr, netmask, gateway, bootproto, async = vlan.getIpConfig()
        self.configWriter.addVlan(vlan.name, bridge=bridge, mtu=vlan.mtu,
                                  ipaddr=ipaddr, netmask=netmask,
                                  gateway=gateway, bootproto=bootproto, **opts)
        vlan.device.configure(**opts)
        ifup(vlan.name, async)

    def configureBond(self, bond, bridge=None, **opts):
        ipaddr, netmask, gateway, bootproto, async = bond.getIpConfig()
        self.configWriter.addBonding(bond.name, bridge=bridge,
                                     bondingOptions=bond.options,
                                     mtu=bond.mtu, ipaddr=ipaddr,
                                     netmask=netmask, gateway=gateway,
                                     bootproto=bootproto, **opts)
        if not netinfo.isVlanned(bond.name):
            for slave in bond.slaves:
                ifdown(slave.name)
        for slave in bond.slaves:
            slave.configure(bonding=bond.name, **opts)
        ifup(bond.name, async)

    def configureNic(self, nic, bridge=None, bonding=None, **opts):
        ipaddr, netmask, gateway, bootproto, async = nic.getIpConfig()
        self.configWriter.addNic(nic.name, bonding=bonding, bridge=bridge,
                                 mtu=nic.mtu, ipaddr=ipaddr,
                                 netmask=netmask, gateway=gateway,
                                 bootproto=bootproto, **opts)
        if not bonding:
            if not netinfo.isVlanned(nic.name):
                ifdown(nic.name)
            ifup(nic.name, async)

    def configureLibvirtNetwork(self, network, iface):
        self.configWriter.createLibvirtNetwork(network,
                                               isinstance(iface, Bridge),
                                               iface.name)
        self._libvirtAdded.add(network)

    def configureBonding(self, bond, nics, bridge=None, mtu=None,
                         bondingOptions=None):
        self.configWriter.addBonding(bond, bridge=bridge, mtu=mtu,
                                     bondingOptions=bondingOptions)
        for nic in nics:
            self.configWriter.addNic(nic, bonding=bond, mtu=mtu)
        ifup(bond)

    def editBonding(self, bond, bondAttrs, bridge, _netinfo):
        # Save MTU for future set on NICs
        confParams = netinfo.getIfaceCfg(bond)
        mtu = confParams.get('MTU', None)
        if mtu:
            mtu = int(mtu)

        ifdown(bond)
        # Take down all bond's NICs.
        for nic in _netinfo.getNicsForBonding(bond):
            ifdown(nic)
            self.configWriter.removeNic(nic)
            if nic not in bondAttrs['nics']:
                ifup(nic)

        # Note! In case we have bridge up and connected to the bond
        # we will get error in log:
        #   (ifdown) bridge XXX is still up; can't delete it
        # But, we prefer this behaviour instead of taking bridge down
        # Anyway, we will not be able to take it down with connected VMs
        self.configureBonding(bond, bondAttrs['nics'], bridge, mtu,
                              bondAttrs.get('options', None))

    def removeBonding(self, bond, nics):
        ifdown(bond)
        self.configWriter.removeBonding(bond)

        for nic in nics:
            ifdown(nic)
            self.configWriter.removeNic(nic)
            ifup(nic)


class ConfigWriter(object):
    CONFFILE_HEADER = '# automatically generated by vdsm'
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

    def _createNetwork(self, netXml):
        conn = libvirtconnection.get()
        net = conn.networkDefineXML(netXml)
        net.create()
        net.setAutostart(1)

    def createLibvirtNetwork(self, network, bridged=True, iface=None,
                             skipBackup=False):
        netName = netinfo.LIBVIRT_NET_PREFIX + network
        if bridged:
            netXml = '''<network><name>%s</name><forward mode='bridge'/>
                        <bridge name='%s'/></network>''' % (escape(netName),
                                                            escape(network))
        else:
            netXml = (
                '''<network><name>%s</name><forward mode='passthrough'>'''
                '''<interface dev='%s'/></forward></network>''' %
                (escape(netName), escape(iface)))
        if not skipBackup:
            self._networkBackup(network)
        self._createNetwork(netXml)

    def _removeNetwork(self, network):
        netName = netinfo.LIBVIRT_NET_PREFIX + network
        conn = libvirtconnection.get()

        net = conn.networkLookupByName(netName)
        if net.isActive():
            net.destroy()
        if net.isPersistent():
            net.undefine()

    def removeLibvirtNetwork(self, network, skipBackup=False):
        if not skipBackup:
            self._networkBackup(network)
        self._removeNetwork(network)

    @classmethod
    def getLibvirtNetwork(cls, network):
        netName = netinfo.LIBVIRT_NET_PREFIX + network
        conn = libvirtconnection.get()
        try:
            net = conn.networkLookupByName(netName)
            return net.XMLDesc(0)
        except libvirt.libvirtError as e:
            if e.get_error_code() == libvirt.VIR_ERR_NO_NETWORK:
                return

            raise

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
            self._networksBackups[network] = self.getLibvirtNetwork(network)
            logging.debug("Backed up %s", network)

    @classmethod
    def _persistentNetworkBackup(cls, network):
        """ Persistently backup libvirt networks """
        content = cls.getLibvirtNetwork(network)
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
                self._removeNetwork(network)
            except libvirt.libvirtError as e:
                if e.get_error_code() == libvirt.VIR_ERR_NO_NETWORK:
                    pass

            if content:
                self._createNetwork(content)

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

    def _sortModifiedIfcfgs(self):
        devdict = {'Bridge': [],
                   'Vlan': [],
                   'Slave': [],
                   'Other': []}
        for confFile, _ in self._backups.iteritems():
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
        for dev in reversed(self._sortModifiedIfcfgs()):
            ifdown(dev)

    def _startAtomicDevices(self):
        for dev in self._sortModifiedIfcfgs():
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

    def _createConfFile(self, conf, name, ipaddr=None, netmask=None,
                        gateway=None, bootproto=None, mtu=None, onboot='yes',
                        **kwargs):
        """ Create ifcfg-* file with proper fields per device """

        cfg = """DEVICE=%s\nONBOOT=%s\n""" % (pipes.quote(name),
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

    def addBridge(self, name, ipaddr=None, netmask=None, mtu=None,
                  gateway=None, bootproto=None, delay='0', onboot='yes',
                  **kwargs):
        """ Create ifcfg-* file with proper fields for bridge """
        conf = 'TYPE=Bridge\nDELAY=%s\n' % pipes.quote(delay)
        self._createConfFile(conf, name, ipaddr, netmask, gateway,
                             bootproto, mtu, onboot, **kwargs)

    def addVlan(self, vlan, bridge=None, mtu=None, ipaddr=None,
                netmask=None, gateway=None, bootproto=None,
                onboot='yes', **kwargs):
        """ Create ifcfg-* file with proper fields for VLAN """
        conf = 'VLAN=yes\n'
        if bridge:
            conf += 'BRIDGE=%s\n' % pipes.quote(bridge)

        self._createConfFile(conf, vlan, ipaddr, netmask, gateway,
                             bootproto, mtu, onboot, **kwargs)

    def addBonding(self, bonding, bridge=None, bondingOptions=None, mtu=None,
                   ipaddr=None, netmask=None, gateway=None, bootproto=None,
                   onboot='yes', **kwargs):
        """ Create ifcfg-* file with proper fields for bond """
        if not bondingOptions:
            bondingOptions = 'mode=802.3ad miimon=150'

        conf = 'BONDING_OPTS=%s\n' % pipes.quote(bondingOptions or '')
        if bridge:
            conf += 'BRIDGE=%s\n' % pipes.quote(bridge)

        if netinfo.NetInfo().ifaceUsers(bonding):
            confParams = netinfo.getIfaceCfg(bonding)
            if not ipaddr:
                ipaddr = confParams.get('IPADDR', None)
                netmask = confParams.get('NETMASK', None)
                gateway = confParams.get('GATEWAY', None)
                bootproto = confParams.get('BOOTPROTO', None)
            if not mtu:
                mtu = confParams.get('MTU', None)
                if mtu:
                    mtu = int(mtu)

        self._createConfFile(conf, bonding, ipaddr, netmask, gateway,
                             bootproto, mtu, onboot, **kwargs)

        # create the bonding device to avoid initscripts noise
        if bonding not in open(netinfo.BONDING_MASTERS).read().split():
            open(netinfo.BONDING_MASTERS, 'w').write('+%s\n' % bonding)

    def addNic(self, nic, bonding=None, bridge=None, mtu=None,
               ipaddr=None, netmask=None, gateway=None, bootproto=None,
               onboot='yes', **kwargs):
        """ Create ifcfg-* file with proper fields for NIC """
        _netinfo = netinfo.NetInfo()
        hwaddr = (_netinfo.nics[nic].get('permhwaddr') or
                  _netinfo.nics[nic]['hwaddr'])

        conf = 'HWADDR=%s\n' % pipes.quote(hwaddr)
        if bridge:
            conf += 'BRIDGE=%s\n' % pipes.quote(bridge)
        if bonding:
            conf += 'MASTER=%s\nSLAVE=yes\n' % pipes.quote(bonding)

        if _netinfo.ifaceUsers(nic):
            confParams = netinfo.getIfaceCfg(nic)
            if not ipaddr:
                ipaddr = confParams.get('IPADDR', None)
                netmask = confParams.get('NETMASK', None)
                gateway = confParams.get('GATEWAY', None)
                bootproto = confParams.get('BOOTPROTO', None)
            if not mtu:
                mtu = confParams.get('MTU', None)
                if mtu:
                    mtu = int(mtu)

        self._createConfFile(conf, nic, ipaddr, netmask, gateway,
                             bootproto, mtu, onboot, **kwargs)

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

    def removeVlan(self, vlan, iface):
        vlandev = iface + '.' + vlan
        ifdown(vlandev)
        self._backup(netinfo.NET_CONF_PREF + vlandev)
        self._removeFile(netinfo.NET_CONF_PREF + vlandev)

    def removeBonding(self, bonding):
        self._backup(netinfo.NET_CONF_PREF + bonding)
        self._removeFile(netinfo.NET_CONF_PREF + bonding)
        if bonding not in netinfo.REQUIRED_BONDINGS:
            with open(netinfo.BONDING_MASTERS, 'w') as f:
                f.write("-%s\n" % bonding)

    def removeBridge(self, bridge):
        ifdown(bridge)
        execCmd([constants.EXT_BRCTL, 'delbr', bridge])
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

    def _updateConfigValue(self, conffile, entry, value, delete=False):
        """
        Set value for network configuration file

        :param entry: entry to update (entry=value)
        :type entry: string

        :param value: value to update (entry=value)
        :type value: string

        :param delete: delete entry
        :type delete: boolean

        Search for entry in conffile and return
        its value or None if not found,
        if delete is True the entry will be deleted from
        the configuration file
        """
        with open(conffile) as f:
            entries = [line for line in f.readlines()
                       if not line.startswith(entry + '=')]

        if not delete:
            entries.append('\n' + entry + '=' + value)

        self._backup(conffile)
        with open(conffile, 'w') as f:
            f.writelines(entries)
            f.close()

    def setIfaceMtu(self, iface, newmtu):
        cf = netinfo.NET_CONF_PREF + iface
        self._updateConfigValue(cf, 'MTU', str(newmtu), False)

    def setBondingMtu(self, bonding, newmtu):
        self.setIfaceMtu(bonding, newmtu)
        slaves = netinfo.slaves(bonding)
        for slave in slaves:
            self.setIfaceMtu(slave, newmtu)

    def setNewMtu(self, network, bridged, _netinfo=None):
        """
        Set new MTU value to network and its interfaces

        :param network: network name
        :type network: string
        :param bridged: network type (bridged or bridgeless)
        :type bridged: bool

        Update MTU to devices (vlans, bonds and nics)
        or added a new value
        """
        if _netinfo is None:
            _netinfo = netinfo.NetInfo()
        currmtu = None
        if bridged:
            try:
                currmtu = netinfo.getMtu(network)
            except IOError as e:
                if e.errno != os.errno.ENOENT:
                    raise

        nics, delvlan, bonding = \
            _netinfo.getNicsVlanAndBondingForNetwork(network)
        if delvlan is None:
            return

        iface = bonding if bonding else nics[0]
        vlans = _netinfo.getVlansForIface(iface)

        newmtu = None
        for vlan in vlans:
            cf = netinfo.NET_CONF_PREF + iface + '.' + vlan
            mtu = self._getConfigValue(cf, 'MTU')
            if mtu:
                mtu = int(mtu)

            if vlan == delvlan:
                # For VLANed bridgeless networks use MTU of delvlan
                # as current MTU
                if not bridged and mtu:
                    currmtu = mtu
                continue

            newmtu = max(newmtu, mtu)

        # Optimization: if network hasn't custom MTU (currmtu), do nothing
        if currmtu and newmtu != currmtu:
            if bonding:
                self.setBondingMtu(bonding, newmtu)
            else:
                self.setIfaceMtu(nics[0], newmtu)


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
