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
import alignmentScan
from vdsm.config import config
import ksm
from momIF import MomThread, isMomAvailable
from vdsm import netinfo
from vdsm.define import doneCode, errCode
import libvirt
from vdsm import libvirtconnection
import vm
from vdsm import constants
from vdsm import utils
from netconf import ifcfg
import caps
from vmChannels import Listener
from vm import Vm
import blkid
import supervdsm
import sampling
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
        self.channelListener = Listener(self.log)
        self._generationID = str(uuid.uuid4())
        self._initIRS()
        self.mom = None
        if _glusterEnabled:
            self.gluster = gapi.GlusterApi(self, log)
        else:
            self.gluster = None
        try:
            self.vmContainer = {}
            ifids = netinfo.nics() + netinfo.bondings()
            ifrates = map(netinfo.speed, ifids)
            self._hostStats = sampling.HostStatsThread(
                cif=self, log=log, ifids=ifids,
                ifrates=ifrates)
            self._hostStats.start()
            self.lastRemoteAccess = 0
            self._memLock = threading.Lock()
            self._enabled = True
            self._netConfigDirty = False
            self._prepareMOM()
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
            if self.mom:
                self.mom.stop()
            raise
        self._prepareBindings()

    @classmethod
    def getInstance(cls, log=None):
        with cls._instanceLock:
            if cls._instance is None:
                if log is None:
                    raise Exception("Logging facility is required to create "
                                    "the single clientIF instance")
                else:
                    cls._instance = clientIF(log)
        return cls._instance

    def _loadBindingXMLRPC(self):
        from BindingXMLRPC import BindingXMLRPC
        ip = config.get('addresses', 'management_ip')
        xmlrpc_port = config.get('addresses', 'management_port')
        use_ssl = config.getboolean('vars', 'ssl')
        resp_timeout = config.getint('vars', 'vds_responsiveness_timeout')
        truststore_path = config.get('vars', 'trust_store_path')
        default_bridge = config.get("vars", "default_bridge")
        self.bindings['xmlrpc'] = BindingXMLRPC(self, self.log, ip,
                                                xmlrpc_port, use_ssl,
                                                resp_timeout, truststore_path,
                                                default_bridge)

    def _loadBindingJsonRpc(self):
        from BindingJsonRpc import BindingJsonRpc
        from Bridge import DynamicBridge
        ip = config.get('addresses', 'management_ip')
        port = config.getint('addresses', 'json_port')
        conf = [('tcp', {"ip": ip, "port": port})]
        self.bindings['json'] = BindingJsonRpc(DynamicBridge(), conf)

    def _prepareBindings(self):
        self.bindings = {}
        if config.getboolean('vars', 'xmlrpc_enable'):
            try:
                self._loadBindingXMLRPC()
            except ImportError:
                self.log.error('Unable to load the xmlrpc server module. '
                               'Please make sure it is installed.')

        if config.getboolean('vars', 'jsonrpc_enable'):
            try:
                self._loadBindingJsonRpc()
            except ImportError:
                self.log.warn('Unable to load the json rpc server module. '
                              'Please make sure it is installed.')

    def _prepareMOM(self):
        momconf = config.get("mom", "conf")

        if isMomAvailable():
            try:
                self.mom = MomThread(momconf)
                return
            except:
                self.log.warn("MOM initialization failed and fall "
                              "back to KsmMonitor", exc_info=True)

        else:
            self.log.warn("MOM is not available, fallback to KsmMonitor")

        self.ksmMonitor = ksm.KsmMonitorThread(self)

    def _syncLibvirtNetworks(self):
        """
            function is mostly for upgrade from versions that did not
            have a libvirt network per vdsm network
        """
        # add libvirt networks
        nets = netinfo.networks()
        bridges = netinfo.bridges()
        configWriter = ifcfg.ConfigWriter()
        for bridge in bridges:
            if not bridge in nets:
                configWriter.createLibvirtNetwork(network=bridge,
                                                  bridged=True,
                                                  skipBackup=True)
        # remove bridged networks that their bridge not exists
        #TODO:
        # this should probably go into vdsm-restore-net script
        for network in nets:
            if nets[network]['bridged'] and network not in bridges:
                configWriter.removeLibvirtNetwork(network, skipBackup=True)

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
            if self.mom:
                self.mom.stop()
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
                drive['volumeInfo'] = res['info']

            # GUID drive format
            elif "GUID" in drive:
                visible = self.irs.scanDevicesVisibility([drive["GUID"]])
                if visible[drive["GUID"]] is False:
                    self.log.error("GUID: %s is not visible", drive["GUID"])
                    raise vm.VolumeError(drive)

                volPath = os.path.join("/dev/mapper", drive["GUID"])
                res = self.irs.appropriateDevice(drive["GUID"], vmId)
                if res['status']['code']:
                    self.log.error("Change ownership on device %s failed",
                                   drive["GUID"])
                    raise vm.VolumeError(drive)

            # UUID drive format
            elif "UUID" in drive:
                volPath = self._getUUIDSpecPath(drive["UUID"])

            # leave path == '' for empty cdrom and floppy drives ...
            elif drive['device'] in ('cdrom', 'floppy') and \
                    'specParams' in drive and \
                    'path' in drive['specParams'] and \
                    drive['specParams']['path'] == '':
                        volPath = ''

            # ... or load the drive from vmPayload:
            elif drive['device'] in ('cdrom', 'floppy') and \
                    'specParams' in drive and \
                    'vmPayload' in drive['specParams']:
                '''
                vmPayload is a key in specParams
                'vmPayload': {'volId': 'volume id',   # volId is optional
                              'file': {'filename': 'content', ...}}
                '''
                mkFsNames = {'cdrom': 'mkIsoFs', 'floppy': 'mkFloppyFs'}
                try:
                    mkFsFunction = getattr(supervdsm.getProxy(),
                                           mkFsNames[drive['device']])
                except AttributeError:
                    raise vm.VolumeError("Unsupported 'device': %s in "
                                         "drive: %" % (drive['device'], drive))
                else:
                    files = drive['specParams']['vmPayload']['file']
                    volId = drive['specParams']['vmPayload'].get('volId')
                    volPath = mkFsFunction(vmId, files, volId)

            elif "path" in drive:
                volPath = drive['path']

            else:
                raise vm.VolumeError(drive)

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
        try:
            res = self.irs.teardownImage(drive['domainID'],
                                         drive['poolID'], drive['imageID'])
        except (KeyError, TypeError):
            # paths (strings) are not deactivated
            if not isinstance(drive, basestring):
                self.log.warning("Drive is not a vdsm image: %s",
                                 drive, exc_info=True)

        return res['status']['code']

    def getDiskAlignment(self, drive):
        """
        Returns the alignment of the disk partitions

        param drive:
        is either {"poolID": , "domainID": , "imageID": , "volumeID": }
        or {"GUID": }

        Return type: a dictionary with partition names as keys and
        True for aligned partitions and False for unaligned as values
        """
        aligning = {}
        volPath = self.prepareVolumePath(drive)
        try:
            out = alignmentScan.scanImage(volPath)
            for line in out:
                aligning[line.partitionName] = line.alignmentScanResult
        finally:
            self.teardownVolumePath(drive)

        return {'status': 0, 'alignment': aligning}

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
            vm = Vm(self, vmParams)
            self.vmContainer[vmParams['vmId']] = vm
        finally:
            container_len = len(self.vmContainer)
            self.vmContainerLock.release()
        vm.run()
        self.log.debug("Total desktops after creation of %s is %d" %
                       (vmParams['vmId'], container_len))
        return {'status': doneCode, 'vmList': vm.status()}

    def _initializingLibvirt(self):
        self._syncLibvirtNetworks()
        mog = min(config.getint('vars', 'max_outgoing_migrations'),
                  caps.CpuTopology().cores())
        vm.MigrationSourceThread.setMaxOutgoingMigrations(mog)

    def _recoverExistingVms(self):
        # Starting up libvirt might take long when host under high load,
        # we prefer running this code in external thread to avoid blocking
        # API response.
        self._initializingLibvirt()

        try:
            vdsmVms = self._getVDSMVms()
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
        except libvirt.libvirtError as e:
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

    def _getVDSMVms(self):
        """
        Return a list of vdsm created VM's.
        """
        libvirtCon = libvirtconnection.get()
        domIds = libvirtCon.listDomainsID()
        vms = []
        for domId in domIds:
            try:
                vm = libvirtCon.lookupByID(domId)
            except libvirt.libvirtError as e:
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
                    if f == 'supervdsmd.pid':
                        continue
                    if f == 'supervdsm_respawn.pid':
                        continue
                else:
                    continue
                self.log.debug("removing old file " + f)
                utils.rmFile(constants.P_VDSM_RUN + f)
            except:
                pass
