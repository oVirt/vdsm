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
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA  02110-1301  USA
#
# Refer to the README and COPYING files for full details of the license
#

import os
import traceback
import time
import threading
import pickle
from xml.dom import minidom
import uuid

from storage.dispatcher import Dispatcher
from storage.hsm import HSM
import storage.misc
import storage.hba
from config import config
import ksm
import netinfo
from define import doneCode, errCode
import libvirt
import libvirtconnection
import vm
import constants
import utils
import configNetwork
import caps
from BindingXMLRPC import BindingXMLRPC

class clientIF:
    """
    The client interface of vdsm.

    Exposes vdsm verbs as xml-rpc functions.
    """
    def __init__ (self, log):
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
        self._createLibvirtNetworks()
        self._generationID = str(uuid.uuid4())
        self._initIRS()
        try:
            self.vmContainer = {}
            ifids = netinfo.nics() + netinfo.bondings()
            ifrates = map(netinfo.speed, ifids)
            self._hostStats = utils.HostStatsThread(cif=self, log=log, ifids=ifids,
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
            self.threadLocal = threading.local()
            self.threadLocal.client = ''
        except:
            self.log.error('failed to init clientIF, shutting down storage dispatcher')
            if self.irs:
                self.irs.prepareForShutdown()
            raise
        self._prepareBindings()

    def _prepareBindings(self):
        self.bindings = {}
        xmlrpc_params = {
            'ip': config.get('addresses', 'management_ip'),
            'port': config.get('addresses', 'management_port'),
            'ssl': config.getboolean('vars', 'ssl'),
            'vds_responsiveness_timeout':
                config.getint('vars', 'vds_responsiveness_timeout'),
            'trust_store_path': config.get('vars', 'trust_store_path'),
            'default_bridge': config.get("vars", "default_bridge"), }
        self.bindings['xmlrpc'] = BindingXMLRPC(self, self.log, xmlrpc_params)

    def _createLibvirtNetworks(self):
        """
            function is mostly for upgrade from versions that did not
            have a libvirt network per vdsm network
        """
        nf = netinfo.NetInfo()
        lvNetworks = self._libvirt.listNetworks()
        for network in nf.networks.keys():
            lvNetwork = configNetwork.NETPREFIX + network
            if not lvNetwork in lvNetworks:
                configNetwork.createLibvirtNetwork(network)

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
            self._hostStats.stop()
            if self.irs:
                return self.irs.prepareForShutdown()
            else:
                return {'status': doneCode}
        finally:
            self._shutdownSemaphore.release()

    def serve(self):
        self.bindings['xmlrpc'].start()

    def _initIRS(self):
        self.irs = None
        if config.getboolean('irs', 'irs_enable'):
            try:
                self.irs = Dispatcher(HSM())
            except:
                self.log.error(traceback.format_exc())

    def _getUUIDSpecPath(self, uuid):
        rc, out, err  = storage.misc.execCmd([constants.EXT_BLKID, "-U", uuid], sudo=False)
        if not out or rc != 0:
            self.log.info("blkid failed for UUID: %s" % uuid)
            raise vm.VolumeError(uuid)
        else:
            path = out[0]
        return path

    def prepareVolumePath(self, drive, vmId=None):
        if type(drive) is dict:
            # PDIV drive format
            if vm.isVdsmImage(drive):
                res = self.irs.prepareImage(drive['domainID'], drive['poolID'],
                                            drive['imageID'], drive['volumeID'])

                if res['status']['code']:
                    raise vm.VolumeError(drive)

                volPath = res['path']
                drive['volumeChain'] = res['chain']

            # GUID drive format
            elif drive.has_key("GUID"):
                volPath = os.path.join("/dev/mapper", drive["GUID"])

                if not os.path.exists(volPath):
                    raise vm.VolumeError(drive)

                res = self.irs.appropriateDevice(drive["GUID"], vmId)

                if res['status']['code']:
                    raise vm.VolumeError(drive)

            # UUID drive format
            elif drive.has_key("UUID"):
                volPath = self._getUUIDSpecPath(drive["UUID"])

            # Path drive format
            elif drive.has_key("path"):
                volPath = drive['path']

        elif os.path.exists(drive):
            volPath = drive

        else:
            raise vm.VolumeError(drive)

        drive['blockDev'] = utils.isBlockDevice(volPath)
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

    def _recoverExistingVms(self):
        try:
            vdsmVms = self.getVDSMVms()
            #Recover
            for v in vdsmVms:
                vmId = v.UUIDString()
                if not self._recoverVm(vmId):
                    #RH qemu proc without recovery
                    self.log.info('loose qemu process with id: %s found, killing it.', vmId)
                    try:
                        v.destroy()
                    except libvirt.libvirtError:
                        self.log.error('failed to kill loose qemu process with id: %s', vmId, exc_info=True)

            while self._enabled and \
                  'WaitForLaunch' in [v.lastStatus for v in self.vmContainer.values()]:
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
                        vmObj.preparePaths(vmObj.getConfDevices()[vm.DISK_DEVICES])
                except:
                    self.log.error("Vm %s recovery failed", vmId, exc_info=True)
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
            pass #no sysinfo in xml
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
            if not self.create(params)['status']['code']:
                return recoveryFile
        except:
            self.log.debug(traceback.format_exc())
        return None

    def _cleanOldFiles(self):
        for f in os.listdir(constants.P_VDSM_RUN):
            try:
                vmId, fileType = f.split(".", 1)
                if fileType in ["guest.socket", "monitor.socket", "pid",
                                    "stdio.dump", "recovery"]:
                    if vmId in self.vmContainer: continue
                    if f == 'vdsmd.pid': continue
                    if f == 'respawn.pid': continue
                    if f == 'svdsm.pid': continue
                    if f == 'svdsm.sock': continue
                else:
                    continue
                self.log.debug("removing old file " + f)
                utils.rmFile(constants.P_VDSM_RUN + f)
            except:
                pass
