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
import os.path
import time
import threading
from xml.dom import minidom
import uuid

import alignmentScan
from vdsm.config import config
import ksm
from momIF import MomThread, isMomAvailable
from vdsm.compat import pickle
from vdsm.define import doneCode, errCode
import libvirt
from vdsm import libvirtconnection
from vdsm import constants
from vdsm import utils
import caps
import blkid
import supervdsm

from virt import migration
from virt import sampling
from virt import vm
from virt import vmstatus
from virt.vm import Vm
from virt.vmchannels import Listener
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

    def __init__(self, irs, log):
        """
        Initialize the (single) clientIF instance

        :param irs: a Dispatcher object to be used as this object's irs.
        :type irs: :class:`storage.dispatcher.Dispatcher`
        :param log: a log object to be used for this object's logging.
        :type log: :class:`logging.Logger`
        """
        self.vmContainerLock = threading.Lock()
        self._networkSemaphore = threading.Semaphore()
        self._shutdownSemaphore = threading.Semaphore()
        self.irs = irs
        if self.irs:
            self.irs.registerDomainStateChangeCallback(self.contEIOVms)
        self.log = log
        self._recovery = True
        self.channelListener = Listener(self.log)
        self._generationID = str(uuid.uuid4())
        self.mom = None
        if _glusterEnabled:
            self.gluster = gapi.GlusterApi(self, log)
        else:
            self.gluster = None
        try:
            self.vmContainer = {}
            self._hostStats = sampling.HostStatsThread(log=log)
            self._hostStats.start()
            self.lastRemoteAccess = 0
            self._enabled = True
            self._netConfigDirty = False
            self._prepareMOM()
            threading.Thread(target=self._recoverThread,
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

    @property
    def ready(self):
        return (self.irs is None or self.irs.ready) and not self._recovery

    def contEIOVms(self, sdUUID, isDomainStateValid):
        # This method is called everytime the onDomainStateChange
        # event is emitted, this event is emitted even when a domain goes
        # INVALID if this happens there is nothing to do
        if not isDomainStateValid:
            return

        libvirtCon = libvirtconnection.get()
        libvirtVms = libvirtCon.listAllDomains(
            libvirt.VIR_CONNECT_LIST_DOMAINS_PAUSED)

        with self.vmContainerLock:
            self.log.info("vmContainerLock acquired")
            for libvirtVm in libvirtVms:
                state = libvirtVm.state(0)
                if state[1] == libvirt.VIR_DOMAIN_PAUSED_IOERROR:
                    vmId = libvirtVm.UUIDString()
                    vmObj = self.vmContainer[vmId]
                    if sdUUID in vmObj.sdIds:
                        self.log.info("Cont vm %s in EIO", vmId)
                        vmObj.cont()

    @classmethod
    def getInstance(cls, irs=None, log=None):
        with cls._instanceLock:
            if cls._instance is None:
                if log is None:
                    raise Exception("Logging facility is required to create "
                                    "the single clientIF instance")
                else:
                    cls._instance = clientIF(irs, log)
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
        truststore_path = None
        if config.getboolean('vars', 'ssl'):
            truststore_path = config.get('vars', 'trust_store_path')
        conf = [('tcp', {"ip": ip, "port": port})]
        self.bindings['json'] = BindingJsonRpc(DynamicBridge(), conf,
                                               truststore_path)

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

    def start(self):
        for binding in self.bindings.values():
            binding.start()

    def _getUUIDSpecPath(self, uuid):
        try:
            return blkid.getDeviceByUuid(uuid)
        except blkid.BlockIdException:
            self.log.info('Error finding path for device', exc_info=True)
            raise vm.VolumeError(uuid)

    def prepareVolumePath(self, drive, vmId=None):
        if type(drive) is dict:
            device = drive['device']
            # PDIV drive format
            if device == 'disk' and vm.isVdsmImage(drive):
                res = self.irs.prepareImage(
                    drive['domainID'], drive['poolID'],
                    drive['imageID'], drive['volumeID'])

                if res['status']['code']:
                    raise vm.VolumeError(drive)

                volPath = res['path']
                # The order of imgVolumesInfo is not guaranteed
                drive['volumeChain'] = res['imgVolumesInfo']
                drive['volumeInfo'] = res['info']

            # GUID drive format
            elif "GUID" in drive:
                res = self.irs.getDevicesVisibility([drive["GUID"]])
                if not res["visible"][drive["GUID"]]:
                    raise vm.VolumeError(drive)

                res = self.irs.appropriateDevice(drive["GUID"], vmId)
                if res['status']['code']:
                    raise vm.VolumeError(drive)

                # Update size for LUN volume
                drive["truesize"] = res['truesize']
                drive["apparentsize"] = res['apparentsize']

                volPath = res['path']

            # UUID drive format
            elif "UUID" in drive:
                volPath = self._getUUIDSpecPath(drive["UUID"])

            # cdrom and floppy drives
            elif (device in ('cdrom', 'floppy') and 'specParams' in drive):
                params = drive['specParams']
                if 'vmPayload' in params:
                    volPath = self._prepareVolumePathFromPayload(
                        vmId, device, params['vmPayload'])
                # next line can be removed in future, when < 3.3 engine
                # is not supported
                elif (params.get('path', '') == '' and
                      drive.get('path', '') == ''):
                    volPath = ''
                else:
                    volPath = drive.get('path', '')

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

    def _prepareVolumePathFromPayload(self, vmId, device, payload):
        """
        param vmId:
            VM UUID or None
        param device:
            either 'floppy' or 'cdrom'
        param payload:
            a dict formed like this:
            {'volId': 'volume id',   # volId is optional
             'file': {'filename': 'content', ...}}
        """
        funcs = {'cdrom': 'mkIsoFs', 'floppy': 'mkFloppyFs'}
        if device not in funcs:
            raise vm.VolumeError("Unsupported 'device': %s" % device)
        func = getattr(supervdsm.getProxy(), funcs[device])
        return func(vmId, payload['file'], payload.get('volId'))

    def teardownVolumePath(self, drive):
        res = {'status': doneCode}
        try:
            if vm.isVdsmImage(drive):
                res = self.irs.teardownImage(drive['domainID'],
                                             drive['poolID'], drive['imageID'])
        except TypeError:
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

        return {'status': doneCode, 'alignment': aligning}

    def createVm(self, vmParams, vmRecover=False):
        with self.vmContainerLock:
            self.log.info("vmContainerLock acquired by vm %s",
                          vmParams['vmId'])
            try:
                if not vmRecover:
                    if vmParams['vmId'] in self.vmContainer:
                        self.log.warning('vm %s already exists' %
                                         vmParams['vmId'])
                        return errCode['exist']
                vm = Vm(self, vmParams, vmRecover)
                self.vmContainer[vmParams['vmId']] = vm
            finally:
                container_len = len(self.vmContainer)
        vm.run()
        self.log.debug("Total desktops after creation of %s is %d" %
                       (vmParams['vmId'], container_len))
        return {'status': doneCode, 'vmList': vm.status()}

    @utils.traceback()
    def _recoverThread(self):
        # Trying to run recover process until it works. During that time vdsm
        # stays in recovery mode (_recover=True), means all api requests
        # returns with "vdsm is in initializing process" message.
        utils.retry(self._recoverExistingVms, sleep=5)

    def _recoverExistingVms(self):
        try:
            # Starting up libvirt might take long when host under high load,
            # we prefer running this code in external thread to avoid blocking
            # API response.
            mog = min(config.getint('vars', 'max_outgoing_migrations'),
                      caps.CpuTopology().cores())
            migration.SourceThread.setMaxOutgoingMigrations(mog)

            vdsmVms = self._getVDSMVms()
            # Recover
            for v in vdsmVms:
                vmId = v.UUIDString()
                if not self._recoverVm(vmId):
                    # RH qemu proc without recovery
                    self.log.info('loose qemu process with id: '
                                  '%s found, killing it.', vmId)
                    try:
                        v.destroy()
                    except libvirt.libvirtError:
                        self.log.error('failed to kill loose qemu '
                                       'process with id: %s',
                                       vmId, exc_info=True)

            # we do this to safely handle VMs which disappeared
            # from the host while VDSM was down/restarting
            recVms = self._getVDSMVmsFromRecovery()
            if recVms:
                self.log.warning('Found %i VMs from recovery files not'
                                 ' reported by libvirt.'
                                 ' This should not happen!'
                                 ' Will try to recover them.', len(recVms))
            for vmId in recVms:
                if not self._recoverVm(vmId):
                    self.log.warning('VM %s failed to recover from recovery'
                                     ' file, reported as Down', vmId)

            while (self._enabled and
                   vmstatus.WAIT_FOR_LAUNCH in [v.lastStatus for v in
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
                            vmObj.buildConfDevices()[vm.DISK_DEVICES])
                except:
                    self.log.error("Vm %s recovery failed",
                                   vmId, exc_info=True)
        except:
            self.log.error("Vm's recovery failed", exc_info=True)
            raise

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
                if self.isVDSMVm(vm):
                    vms.append(vm)
        return vms

    def _getVDSMVmsFromRecovery(self):
        vms = []
        for f in os.listdir(constants.P_VDSM_RUN):
            vmId, fileType = os.path.splitext(f)
            if fileType == ".recovery":
                if vmId not in self.vmContainer:
                    vms.append(vmId)
        return vms

    def _recoverVm(self, vmid):
        try:
            recoveryFile = constants.P_VDSM_RUN + vmid + ".recovery"
            params = pickle.load(file(recoveryFile))
            now = time.time()
            pt = float(params.pop('startTime', now))
            params['elapsedTimeOffset'] = now - pt
            self.log.debug("Trying to recover " + params['vmId'])
            if not self.createVm(params, vmRecover=True)['status']['code']:
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

    def dispatchLibvirtEvents(self, conn, dom, *args):
        try:
            eventid = args[-1]
            vmid = dom.UUIDString()
            v = self.vmContainer.get(vmid)

            if not v:
                self.log.debug('unknown vm %s eventid %s args %s',
                               vmid, eventid, args)
                return

            if eventid == libvirt.VIR_DOMAIN_EVENT_ID_LIFECYCLE:
                event, detail = args[:-1]
                v._onLibvirtLifecycleEvent(event, detail, None)
            elif eventid == libvirt.VIR_DOMAIN_EVENT_ID_REBOOT:
                v.onReboot()
            elif eventid == libvirt.VIR_DOMAIN_EVENT_ID_RTC_CHANGE:
                utcoffset, = args[:-1]
                v._rtcUpdate(utcoffset)
            elif eventid == libvirt.VIR_DOMAIN_EVENT_ID_IO_ERROR_REASON:
                srcPath, devAlias, action, reason = args[:-1]
                v._onIOError(devAlias, reason, action)
            elif eventid == libvirt.VIR_DOMAIN_EVENT_ID_GRAPHICS:
                phase, localAddr, remoteAddr, authScheme, subject = args[:-1]
                v.log.debug('graphics event phase '
                            '%s localAddr %s remoteAddr %s'
                            'authScheme %s subject %s',
                            phase, localAddr, remoteAddr, authScheme, subject)
                if phase == libvirt.VIR_DOMAIN_EVENT_GRAPHICS_INITIALIZE:
                    v.onConnect(remoteAddr['node'])
                elif phase == libvirt.VIR_DOMAIN_EVENT_GRAPHICS_DISCONNECT:
                    v.onDisconnect()
            elif eventid == libvirt.VIR_DOMAIN_EVENT_ID_WATCHDOG:
                action, = args[:-1]
                v._onWatchdogEvent(action)
            else:
                v.log.warning('unknown eventid %s args %s', eventid, args)
        except:
            self.log.error("Error running VM callback", exc_info=True)
