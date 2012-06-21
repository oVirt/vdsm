#
# Copyright 2011 Red Hat, Inc.
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
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA  02110-1301 USA
#
# Refer to the README and COPYING files for full details of the license
#

import os
import time
import threading
import pickle
from xml.dom import minidom
import uuid

from storage.dispatcher import Dispatcher
from storage.hsm import HSM
from vdsm.config import config
import ksm
from vdsm import netinfo
from vdsm.define import doneCode, errCode
import libvirt
from vdsm import libvirtconnection
import vm
from vdsm import constants
from vdsm import utils
import configNetwork
import caps
from vmChannels import Listener
from libvirtvm import LibvirtVm
import blkid
import supervdsm
try:
    import gluster.api as gapi
    _glusterEnabled = True
except ImportError:
    _glusterEnabled = False


class clientIF:
    """
    The client interface of vdsm.

    Exposes vdsm verbs as xml-rpc functions.
    """
    _instance = None
    _instanceLock = threading.Lock()

    def __init__(self, log):
        """
        Initialize the (single) clientIF instance

        :param log: a log object to be used for this object's logging.
        :type log: :class:`logging.Logger`
        """
        self.vmContainerLock = threading.Lock()
        self._networkSemaphore = threading.Semaphore()
        self._shutdownSemaphore = threading.Semaphore()
        self.log = log
        self._recovery = True
        self._libvirt = libvirtconnection.get()
        self._syncLibvirtNetworks()
        self.channelListener = Listener(self.log)
        self._generationID = str(uuid.uuid4())
        self._initIRS()
        if _glusterEnabled:
            self.gluster = gapi.GlusterApi(self, log)
        else:
            self.gluster = None
        try:
            self.vmContainer = {}
            ifids = netinfo.nics() + netinfo.bondings()
            ifrates = map(netinfo.speed, ifids)
            self._hostStats = utils.HostStatsThread(
                                        cif=self, log=log, ifids=ifids,
                                        ifrates=ifrates)
            self._hostStats.start()
            mog = min(config.getint('vars', 'max_outgoing_migrations'),
                      caps.CpuInfo().cores())
            vm.MigrationSourceThread.setMaxOutgoingMigrations(mog)

            self.lastRemoteAccess = 0
            self._memLock = threading.Lock()
            self._enabled = True
            self.ksmMonitor = ksm.KsmMonitorThread(self)
            self._netConfigDirty = False
            threading.Thread(target=self._recoverExistingVms,
                             name='clientIFinit').start()
            self.channelListener.settimeout(
                    config.getint('vars', 'guest_agent_timeout'))
            self.channelListener.start()
            self.threadLocal = threading.local()
            self.threadLocal.client = ''
        except:
            self.log.error('failed to init clientIF, '
                           'shutting down storage dispatcher')
            if self.irs:
                self.irs.prepareForShutdown()
            raise
        self._prepareBindings()

    @classmethod
    def getInstance(cls, log=None):
        if cls._instance == None:
            if log == None:
                raise Exception("Logging facility is required to create \
                                the single clientIF instance")
        with cls._instanceLock:
            if cls._instance == None:
                cls._instance = clientIF(log)
        return cls._instance

    def _getServerIP(self, addr=None):
        """Return the IP address we should listen on"""

        if addr:
            return addr
        try:
            addr = netinfo.getaddr(self.defaultBridge)
        except:
            pass
        return addr

    def _loadBindingXMLRPC(self):
        from BindingXMLRPC import BindingXMLRPC
        ip = self._getServerIP(config.get('addresses', 'management_ip'))
        xmlrpc_port = config.get('addresses', 'management_port')
        use_ssl = config.getboolean('vars', 'ssl')
        resp_timeout = config.getint('vars', 'vds_responsiveness_timeout')
        truststore_path = config.get('vars', 'trust_store_path')
        default_bridge = config.get("vars", "default_bridge")
        self.bindings['xmlrpc'] = BindingXMLRPC(self, self.log, ip,
                                                xmlrpc_port, use_ssl,
                                                resp_timeout, truststore_path,
                                                default_bridge)

    def _loadBindingREST(self):
        from rest.BindingREST import BindingREST
        ip = self._getServerIP(config.get('addresses', 'management_ip'))
        rest_port = config.getint('addresses', 'rest_port')
        templatePath = "%s/rest/templates" % constants.P_VDSM
        self.bindings['rest'] = BindingREST(self, self.log, ip, rest_port,
                                            templatePath)

    def _prepareBindings(self):
        self.bindings = {}
        if config.getboolean('vars', 'xmlrpc_enable'):
            try:
                self._loadBindingXMLRPC()
            except ImportError:
                self.log.error('Unable to load the xmlrpc server module. '
                               'Please make sure it is installed.')
        if config.getboolean('vars', 'rest_enable'):
            try:
                self._loadBindingREST()
            except ImportError:
                self.log.warn('Unable to load the rest server module. '
                              'Please make sure it is installed.')

    def _syncLibvirtNetworks(self):
        """
            function is mostly for upgrade from versions that did not
            have a libvirt network per vdsm network
        """
        # add libvirt networks
        nets = netinfo.networks()
        bridges = netinfo.bridges()
        for bridge in bridges:
            if not bridge in nets:
                configNetwork.createLibvirtNetwork(network=bridge,
                                                   bridged=True)
        # remove bridged networks that their bridge not exists
        #TODO:
        # this should probably go into vdsm-restore-net script
        for network in nets:
            if nets[network]['bridged'] and network not in bridges:
                configNetwork.removeLibvirtNetwork(network)

    def prepareForShutdown(self):
        """
        Prepare server for shutdown.

        Should be called before taking server down.
        """
        if not self._shutdownSemaphore.acquire(blocking=False):
            self.log.debug('cannot run prepareForShutdown concurrently')
            return errCode['unavail']
        try:
            if not self._enabled:
                self.log.debug('cannot run prepareForShutdown twice')
                return errCode['unavail']
            for binding in self.bindings.values():
                binding.prepareForShutdown()
            self._enabled = False
            self.channelListener.stop()
            self._hostStats.stop()
            if self.irs:
                return self.irs.prepareForShutdown()
            else:
                return {'status': doneCode}
        finally:
            self._shutdownSemaphore.release()

    def serve(self):
        for binding in self.bindings.values():
            binding.start()
        while self._enabled:
            time.sleep(3)

    def _initIRS(self):
        self.irs = None
        if config.getboolean('irs', 'irs_enable'):
            try:
                self.irs = Dispatcher(HSM())
            except:
                self.log.error("Error initializing IRS", exc_info=True)

    def _getUUIDSpecPath(self, uuid):
        try:
            return blkid.getDeviceByUuid(uuid)
        except blkid.BlockIdException:
            self.log.info('Error finding path for device', exc_info=True)
            raise vm.VolumeError(uuid)

    def prepareVolumePath(self, drive, vmId=None):
        if type(drive) is dict:
            # PDIV drive format
            if drive['device'] == 'disk' and vm.isVdsmImage(drive):
                res = self.irs.prepareImage(
                                drive['domainID'], drive['poolID'],
                                drive['imageID'], drive['volumeID'])

                if res['status']['code']:
                    raise vm.VolumeError(drive)

                volPath = res['path']
                drive['volumeChain'] = res['chain']

            # GUID drive format
            elif "GUID" in drive:
                volPath = os.path.join("/dev/mapper", drive["GUID"])

                if not os.path.exists(volPath):
                    raise vm.VolumeError(drive)

                res = self.irs.appropriateDevice(drive["GUID"], vmId)
                if res['status']['code']:
                    raise vm.VolumeError(drive)

            # UUID drive format
            elif "UUID" in drive:
                volPath = self._getUUIDSpecPath(drive["UUID"])

            elif 'specParams' in drive and 'vmPayload' in drive['specParams']:
                '''
                vmPayload is a key in specParams
                'vmPayload': {'file': {'filename': 'content'}}
                '''
                for key, files in drive['specParams']['vmPayload'].iteritems():
                    if key == 'file':
                        if drive['device'] == 'cdrom':
                            volPath = supervdsm.getProxy().mkIsoFs(vmId, files)
                        elif drive['device'] == 'floppy':
                            volPath = \
                                   supervdsm.getProxy().mkFloppyFs(vmId, files)

            elif "path" in drive:
                volPath = drive['path']

        # For BC sake: None as argument
        elif not drive:
            volPath = drive

        #  For BC sake: path as a string.
        elif os.path.exists(drive):
            volPath = drive

        else:
            raise vm.VolumeError(drive)

        self.log.info("prepared volume path: %s", volPath)
        return volPath

    def teardownVolumePath(self, drive):
        res = {'status': doneCode}
        if type(drive) == dict:
            try:
                res = self.irs.teardownImage(drive['domainID'],
                                             drive['poolID'], drive['imageID'])
            except KeyError:
                #This drive is not a vdsm image (quartet)
                self.log.info("Avoiding tear down drive %s", str(drive))

        return res['status']['code']

    def createVm(self, vmParams):
        self.vmContainerLock.acquire()
        self.log.info("vmContainerLock acquired by vm %s",
                          vmParams['vmId'])
        try:
            if 'recover' not in vmParams:
                if vmParams['vmId'] in self.vmContainer:
                    self.log.warning('vm %s already exists' %
                                     vmParams['vmId'])
                    return errCode['exist']
            vm = LibvirtVm(self, vmParams)
            self.vmContainer[vmParams['vmId']] = vm
        finally:
            container_len = len(self.vmContainer)
            self.vmContainerLock.release()
        vm.run()
        self.log.debug("Total desktops after creation of %s is %d" %
                       (vmParams['vmId'], container_len))
        return {'status': doneCode, 'vmList': vm.status()}

    def _recoverExistingVms(self):
        try:
            vdsmVms = self.getVDSMVms()
            #Recover
            for v in vdsmVms:
                vmId = v.UUIDString()
                if not self._recoverVm(vmId):
                    #RH qemu proc without recovery
                    self.log.info('loose qemu process with id: '
                                  '%s found, killing it.', vmId)
                    try:
                        v.destroy()
                    except libvirt.libvirtError:
                        self.log.error('failed to kill loose qemu '
                                       'process with id: %s',
                                       vmId, exc_info=True)

            while (self._enabled and
                  'WaitForLaunch' in [v.lastStatus for v in
                                      self.vmContainer.values()]):
                time.sleep(1)
            self._cleanOldFiles()
            self._recovery = False

            # Now if we have VMs to restore we should wait pool connection
            # and then prepare all volumes.
            # Actually, we need it just to get the resources for future
            # volumes manipulations
            while self._enabled and self.vmContainer and \
                  not self.irs.getConnectedStoragePoolsList()['poollist']:
                time.sleep(5)

            for vmId, vmObj in self.vmContainer.items():
                # Let's recover as much VMs as possible
                try:
                    # Do not prepare volumes when system goes down
                    if self._enabled:
                        vmObj.preparePaths(
                                vmObj.getConfDevices()[vm.DISK_DEVICES])
                except:
                    self.log.error("Vm %s recovery failed",
                                   vmId, exc_info=True)
        except:
            self.log.error("Vm's recovery failed", exc_info=True)

    def isVDSMVm(self, vm):
        """
        Return True if vm seems as if it was created by vdsm.
        """
        try:
            vmdom = minidom.parseString(vm.XMLDesc(0))
            sysinfo = vmdom.getElementsByTagName("sysinfo")[0]
        except libvirt.libvirtError, e:
            if e.get_error_code() == libvirt.VIR_ERR_NO_DOMAIN:
                self.log.error("domId: %s is dead", vm.UUIDString())
            else:
                raise
        except IndexError:
            pass  # no sysinfo in xml
        else:
            systype = sysinfo.getAttribute("type")
            if systype == "smbios":
                entries = sysinfo.getElementsByTagName("entry")
                for entry in entries:
                    if entry.getAttribute("name") == "product":
                        prod = entry.firstChild.data
                        if prod in (caps.OSName.RHEL, caps.OSName.OVIRT,
                                caps.OSName.RHEVH, caps.OSName.FEDORA,
                                caps.OSName.DEBIAN):
                            return True
        return False

    def getVDSMVms(self):
        """
        Return a list of vdsm created VM's.
        """
        domIds = self._libvirt.listDomainsID()
        vms = []
        for domId in domIds:
            try:
                vm = self._libvirt.lookupByID(domId)
            except libvirt.libvirtError, e:
                if e.get_error_code() == libvirt.VIR_ERR_NO_DOMAIN:
                    self.log.error("domId: %s is dead", domId, exc_info=True)
                else:
                    self.log.error("Can't look for domId: %s, code: %s",
                                   domId, e.get_error_code(), exc_info=True)
                    raise
            else:
                vms.append(vm)
        return [vm for vm in vms if self.isVDSMVm(vm)]

    def _recoverVm(self, vmid):
        try:
            recoveryFile = constants.P_VDSM_RUN + vmid + ".recovery"
            params = pickle.load(file(recoveryFile))
            params['recover'] = True
            now = time.time()
            pt = float(params.pop('startTime', now))
            params['elapsedTimeOffset'] = now - pt
            self.log.debug("Trying to recover " + params['vmId'])
            if not self.createVm(params)['status']['code']:
                return recoveryFile
        except:
            self.log.debug("Error recovering VM", exc_info=True)
        return None

    def _cleanOldFiles(self):
        for f in os.listdir(constants.P_VDSM_RUN):
            try:
                vmId, fileType = f.split(".", 1)
                if fileType in ["guest.socket", "monitor.socket", "pid",
                                    "stdio.dump", "recovery"]:
                    if vmId in self.vmContainer:
                        continue
                    if f == 'vdsmd.pid':
                        continue
                    if f == 'respawn.pid':
                        continue
                    if f == 'svdsm.pid':
                        continue
                    if f == 'svdsm.sock':
                        continue
                else:
                    continue
                self.log.debug("removing old file " + f)
                utils.rmFile(constants.P_VDSM_RUN + f)
            except:
                pass
