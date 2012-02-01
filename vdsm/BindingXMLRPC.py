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

import time
from errno import EINTR
import SimpleXMLRPCServer
import SecureXMLRPCServer
import logging
import traceback
import libvirt

import caps
import constants
import netinfo
import utils
from define import doneCode, errCode
import API
import storage.volume
import storage.safelease
import storage.sd

class BindingXMLRPC(object):
    def __init__(self, cif, log, params):
        """
        Initialize the XMLRPC Bindings.

        params must contain the following configuration parameters:
          'ip' : The IP address to listen on
          'port': The port number to listen on
          'ssl': Enable SSL?
          'vds_responsiveness_timeout': Server responsiveness timeout
          'trust_store_path': Location of the SSL certificates
          'default_bridge': The default bridge interface (for detecting the IP)
        """
        self.log = log
        self.cif = cif
        self._enabled = False

        self.serverPort = params['port']
        self.serverIP = self._getServerIP(params['ip'])
        self.enableSSL = params['ssl']
        self.serverRespTimeout = params['vds_responsiveness_timeout']
        self.trustStorePath = params['trust_store_path']
        self.defaultBridge = params['default_bridge']
        self.server = self._createXMLRPCServer()

    def start(self):
        """
        Register xml-rpc functions and serve clients until stopped
        """

        self._registerFunctions()
        self.server.timeout = 1
        self._enabled = True

        while self._enabled:
            try:
                self.server.handle_request()
            except Exception, e:
                if e[0] != EINTR:
                    self.log.error("xml-rpc handler exception", exc_info=True)

    def prepareForShutdown(self):
        self._enabled = False
        self.server.server_close()

    def getServerInfo(self):
        """
        Return the IP address and last client information
        """
        last = self.server.lastClient
        return { 'management_ip': self.serverIP,
                 'lastClient': last,
                 'lastClientIface': caps._getIfaceByIP(last) }

    def _getServerIP(self, addr=None):
        """Return the IP address we should listen on"""

        if addr:
            return addr
        try:
            addr = netinfo.ifconfig()[self.defaultBridge]['addr']
        except:
            pass
        return addr

    def _getKeyCertFilenames(self):
        """
        Get the locations of key and certificate files.
        """
        KEYFILE = self.trustStorePath + '/keys/vdsmkey.pem'
        CERTFILE = self.trustStorePath + '/certs/vdsmcert.pem'
        CACERT = self.trustStorePath + '/certs/cacert.pem'
        return KEYFILE, CERTFILE, CACERT

    def _createXMLRPCServer(self):
        """
        Create xml-rpc server over http or https.
        """
        threadLocal = self.cif.threadLocal
        class LoggingMixIn:
            def log_request(self, code='-', size='-'):
                """Track from where client connections are coming."""
                self.server.lastClient = self.client_address[0]
                self.server.lastClientTime = time.time()
                # FIXME: The editNetwork API uses this log file to
                # determine if this host is still accessible.  We use a
                # file (rather than an event) because editNetwork is
                # performed by a separate, root process.  To clean this
                # up we need to move this to an API wrapper that is only
                # run for real clients (not vdsm internal API calls).
                file(constants.P_VDSM_CLIENT_LOG, 'w')

        server_address = (self.serverIP, int(self.serverPort))
        if self.enableSSL:
            class LoggingHandler(LoggingMixIn, SecureXMLRPCServer.SecureXMLRPCRequestHandler):
                def setup(self):
                    threadLocal.client = self.client_address[0]
                    return SecureXMLRPCServer.SecureXMLRPCRequestHandler.setup(self)
            KEYFILE, CERTFILE, CACERT = self._getKeyCertFilenames()
            s = SecureXMLRPCServer.SecureThreadedXMLRPCServer(server_address,
                        keyfile=KEYFILE, certfile=CERTFILE, ca_certs=CACERT,
                        timeout=self.serverRespTimeout,
                        requestHandler=LoggingHandler)
        else:
            class LoggingHandler(LoggingMixIn, SimpleXMLRPCServer.SimpleXMLRPCRequestHandler):
                def setup(self):
                    threadLocal.client = self.client_address[0]
                    return SimpleXMLRPCServer.SimpleXMLRPCRequestHandler.setup(self)
            s = utils.SimpleThreadedXMLRPCServer(server_address,
                        requestHandler=LoggingHandler, logRequests=True)
        utils.closeOnExec(s.socket.fileno())

        return s

    def _registerFunctions(self):
        def wrapIrsMethod(f):
            def wrapper(*args, **kwargs):
                if self.cif.threadLocal.client:
                    f.im_self.log.debug('[%s]', self.cif.threadLocal.client)
                return f(*args, **kwargs)
            wrapper.__name__ = f.__name__
            wrapper.__doc__ = f.__doc__
            return wrapper

        globalMethods = self.getGlobalMethods()
        irsMethods = self.getIrsMethods()
        # XXX: Need another way to check if IRS init was okay
        if not irsMethods:
            err = errCode['recovery'].copy()
            err['status'] = err['status'].copy()
            err['status']['message'] = 'Failed to initialize storage'
            self.server._dispatch = lambda method, params: err

        self.server.register_introspection_functions()
        for (method, name) in globalMethods:
            self.server.register_function(wrapApiMethod(method), name)
        for (method, name) in irsMethods:
            self.server.register_function(wrapIrsMethod(method), name)

    #
    # Callable methods:
    #
    def vmDestroy(self, vmId):
        vm = API.VM(self.cif, vmId)
        return vm.destroy()

    def vmCreate(self, vmParams):
        vm = API.VM(self.cif, vmParams['vmId'])
        return vm.create(vmParams)

    def getVMList(self, fullStatus=False, vmList=[]):
        api = API.Global(self.cif)
        return api.getVMList(fullStatus, vmList)

    def vmPause(self, vmId):
        vm = API.VM(self.cif, vmId)
        return vm.pause()

    def vmCont(self, vmId):
        vm = API.VM(self.cif, vmId)
        return vm.cont()

    def vmReset(self, vmId):
        vm = API.VM(self.cif, vmId)
        return vm.reset()

    def vmShutdown(self, vmId, delay=None, message=None):
        vm = API.VM(self.cif, vmId)
        return vm.shutdown(delay, message)

    def vmSetTicket(self, vmId, password, ttl, existingConnAction='disconnect'):
        vm = API.VM(self.cif, vmId)
        return vm.setTicket(password, ttl, existingConnAction)

    def vmChangeCD(self, vmId, driveSpec):
        vm = API.VM(self.cif, vmId)
        return vm.changeCD(driveSpec)

    def vmChangeFloppy(self, vmId, driveSpec):
        vm = API.VM(self.cif, vmId)
        return vm.changeFloppy(driveSpec)

    def vmSendKeys(self, vmId, keySequence):
        vm = API.VM(self.cif, vmId)
        return vm.sendKeys(keySequence)

    def vmMigrate(self, params):
        vm = API.VM(self.cif, params['vmId'])
        return vm.migrate(params)

    def vmGetMigrationStatus(self, vmId):
        vm = API.VM(self.cif, vmId)
        return vm.getMigrationStatus()

    def vmMigrationCancel(self, vmId):
        vm = API.VM(self.cif, vmId)
        return vm.migrationCancel()

    def vmHotplugDisk(self, params):
        vm = API.VM(self.cif, params['vmId'])
        return vm.hotplugDisk(params)

    def vmHotunplugDisk(self, params):
        vm = API.VM(self.cif, params['vmId'])
        return vm.hotunplugDisk(params)

    def vmSnapshot(self, vmId, snapDrives):
        vm = API.VM(self.cif, snapDrives)
        return vm.snapshot(snapDrives)

    def getCapabilities(self):
        api = API.Global(self.cif)
        ret = api.getCapabilities()
        ret['info'].update(self.getServerInfo())
        return ret

    def getStats(self):
        api = API.Global(self.cif)
        return api.getStats()

    def vmGetStats(self, vmId):
        vm = API.VM(self.cif, vmId)
        return vm.getStats()

    def getAllVmStats(self):
        """
        Get statistics of all running VMs.
        """
        vms = self.getVMList()
        statsList = []
        for s in vms['vmList']:
            response = self.vmGetStats(s['vmId'])
            if response:
                statsList.append(response)
        return {'status': doneCode, 'statsList': statsList}

    def vmMigrationCreate(self, params):
        vm = API.VM(self.cif, params['vmId'])
        return vm.migrationCreate(params)

    def vmDesktopLogin(self, vmId, domain, user, password):
        vm = API.VM(self.cif, vmId)
        return vm.desktopLogin(domain, user, password)

    def vmDesktopLogoff(self, vmId, force):
        vm = API.VM(self.cif, vmId)
        return vm.desktopLogoff(force)

    def vmDesktopLock(self, vmId):
        vm = API.VM(self.cif, vmId)
        return vm.desktopLock()

    def vmDesktopSendHcCommand(self, vmId, message):
        vm = API.VM(self.cif, vmId)
        return vm.desktopSendHcCommand(message)

    def vmHibernate(self, vmId, hiberVolHandle=None):
        vm = API.VM(self.cif, vmId)
        return vm.hibernate(hiberVolHandle)

    def vmMonitorCommand(self, vmId, cmd):
        vm = API.VM(self.cif, vmId)
        return vm.monitorCommand(cmd)

    def addNetwork(self, bridge, vlan=None, bond=None, nics=None, options={}):
        api = API.Global(self.cif)
        return api.addNetwork(bridge, vlan, bond, nics, options={})

    def delNetwork(self, bridge, vlan=None, bond=None, nics=None, options={}):
        api = API.Global(self.cif)
        return api.delNetwork(bridge, vlan, bond, nics, options)

    def editNetwork(self, oldBridge, newBridge, vlan=None, bond=None,
                    nics=None, options={}):
        api = API.Global(self.cif)
        return api.editNetwork(oldBridge, newBridge, vlan, bond, nics,
                options)

    def setupNetworks(self, networks={}, bondings={}, options={}):
        api = API.Global(self.cif)
        return api.setupNetworks(networks, bondings, options)

    def ping(self):
        api = API.Global(self.cif)
        return api.ping()

    def setSafeNetworkConfig(self):
        api = API.Global(self.cif)
        return api.setSafeNetworkConfig()

    def fenceNode(self, addr, port, agent, username, password, action,
                  secure=False, options=''):
        api = API.Global(self.cif)
        return api.fenceNode(addr, port, agent, username, password,
                action, secure)

    def setLogLevel(self, level):
        api = API.Global(self.cif)
        return api.setLogLevel(level)

    def domainActivate(self, sdUUID, spUUID, options=None):
        domain = API.StorageDomain(self.cif, sdUUID, spUUID)
        return domain.activate()

    def domainAttach(self, sdUUID, spUUID, options=None):
        domain = API.StorageDomain(self.cif, sdUUID, spUUID)
        return domain.attach(spUUID)

    def domainCreate(self, storageType, sdUUID, domainName,
                     typeSpecificArg, domClass,
                     domVersion=constants.SUPPORTED_DOMAIN_VERSIONS[0],
                     options=None):
        domain = API.StorageDomain(self.cif, sdUUID, spUUID=None)
        return domain.create(storageType, typeSpecificArg, domainName,
                             domClass, domVersion)

    def domainDeactivate(self, sdUUID, spUUID, msdUUID, masterVersion,
                         options=None):
        domain = API.StorageDomain(self.cif, sdUUID, spUUID)
        return domain.deactivate(msdUUID, masterVersion)

    def domainDetach(self, sdUUID, spUUID, msdUUID, masterVersion,
                     options=None):
        domain = API.StorageDomain(self.cif, sdUUID, spUUID)
        return domain.detach(msdUUID, masterVersion, force=False)

    def domainDetachForced(self, sdUUID, spUUID, options=None):
        domain = API.StorageDomain(self.cif, sdUUID, spUUID)
        return domain.detach(None, None, force=True)

    def domainExtend(self, sdUUID, spUUID, devlist, options=None):
        domain = API.StorageDomain(self.cif, sdUUID, spUUID)
        return domain.extend(devlist)

    def domainFormat(self, sdUUID, autoDetach = False, options=None):
        domain = API.StorageDomain(self.cif, sdUUID, spUUID=None)
        return domain.format(autoDetach)

    def domainGetFileList(self, sdUUID, pattern='*', options=None):
        domain = API.StorageDomain(self.cif, sdUUID, spUUID=None)
        return domain.getFileList(pattern)

    def domainGetImages(self, sdUUID, options=None):
        domain = API.StorageDomain(self.cif, sdUUID, spUUID=None)
        return domain.getImages()

    def domainGetInfo(self, sdUUID, options=None):
        domain = API.StorageDomain(self.cif, sdUUID, spUUID=None)
        return domain.getInfo()

    def domainGetStats(self, sdUUID, options=None):
        domain = API.StorageDomain(self.cif, sdUUID, spUUID=None)
        return domain.getStats()

    def domainGetVolumes(self, sdUUID, spUUID,
                         imgUUID=storage.volume.BLANK_UUID):
        domain = API.StorageDomain(self.cif, sdUUID, spUUID)
        return domain.getVolumes(imgUUID)

    def domainSetDescription(self, sdUUID, description, options=None):
        domain = API.StorageDomain(self.cif, sdUUID, spUUID=None)
        return domain.setDescription(description)

    def domainUploadVolume(self, sdUUID, spUUID, imgUUID, volUUID,
                           srcPath, size, method="rsync", options=None):
        domain = API.StorageDomain(self.cif, sdUUID, spUUID)
        return domain.uploadVolume(imgUUID, volUUID, srcPath, size, method)

    def domainValidate(self, sdUUID, options=None):
        domain = API.StorageDomain(self.cif, sdUUID, spUUID=None)
        return domain.validate()

    def imageDelete(self, sdUUID, spUUID, imgUUID, postZero=False, force=False):
        image = API.Image(self.cif, imgUUID, spUUID, sdUUID)
        return image.delete(postZero, force)

    def imageDeleteVolumes(self, sdUUID, spUUID, imgUUID, volumes,
                           postZero=False, force=False):
        image = API.Image(self.cif, imgUUID, spUUID, sdUUID)
        return image.deleteVolumes(volumes, postZero, force)

    def imageMergeSnapshots(self, sdUUID, spUUID, vmUUID, imgUUID,
                            ancestor, successor, postZero=False):
        image = API.Image(self.cif, imgUUID, spUUID, sdUUID)
        return image.mergeSnapshots(ancestor, successor, postZero)

    def imageMove(self, spUUID, srcDomUUID, dstDomUUID, imgUUID, vmUUID,
                  op, postZero=False, force=False):
        image = API.Image(self.cif, imgUUID, spUUID, srcDomUUID)
        return image.move(dstDomUUID, op, postZero, force)

    def poolConnect(self, spUUID, hostID, scsiKey, msdUUID, masterVersion,
                    options=None):
        pool = API.StoragePool(self.cif, spUUID)
        return pool.connect(hostID, scsiKey, msdUUID, masterVersion)

    def poolConnectStorageServer(self, domType, spUUID, conList, options=None):
        pool = API.StoragePool(self.cif, spUUID)
        return pool.connectStorageServer(domType, conList)

    def poolCreate(self, poolType, spUUID, poolName, masterDom, domList,
                   masterVersion, lockPolicy=None, lockRenewalIntervalSec=None,
                   leaseTimeSec=None, ioOpTimeoutSec=None,
                   leaseRetries=None, options=None):
        pool = API.StoragePool(self.cif, spUUID)
        return pool.create(poolName, masterDom, masterVersion, domList,
               lockRenewalIntervalSec, leaseTimeSec, ioOpTimeoutSec,
               leaseRetries)

    def poolDestroy(self, spUUID, hostID, scsiKey, options=None):
        pool = API.StoragePool(self.cif, spUUID)
        return pool.destroy(hostID, scsiKey)

    def poolDisconnect(self, spUUID, hostID, scsiKey, remove=False,
                       options=None):
        pool = API.StoragePool(self.cif, spUUID)
        return pool.disconnect(hostID, scsiKey, remove)

    def poolDisconnectStorageServer(self, domType, spUUID, conList,
                                    options=None):
        pool = API.StoragePool(self.cif, spUUID)
        return pool.disconnectStorageServer(domType, conList)

    def poolFenceSPMStorage(self, spUUID, lastOwner, lastLver, options=None):
        pool = API.StoragePool(self.cif, spUUID)
        return pool.fence()

    def poolGetBackedUpVmsInfo(self, spUUID, sdUUID, vmList=None, options=None):
        pool = API.StoragePool(self.cif, spUUID)
        return pool.getBackedUpVmsInfo(sdUUID, vmList)

    def poolGetBackedUpVmsList(self, spUUID, sdUUID=None, options=None):
        pool = API.StoragePool(self.cif, spUUID)
        return pool.getBackedUpVmsList(sdUUID)

    def poolGetFloppyList(self, spUUID, options=None):
        pool = API.StoragePool(self.cif, spUUID)
        return pool.getFloppyList()

    def poolGetDomainsContainingImage(self, spUUID, imgUUID, datadomains=True,
                                      options=None):
        pool = API.StoragePool(self.cif, spUUID)
        return pool.getDomainsContainingImage(imgUUID, datadomains)

    def poolGetIsoList(self, spUUID, extension='iso', options=None):
        pool = API.StoragePool(self.cif, spUUID)
        return pool.getIsoList(extension)

    def poolGetSpmStatus(self, spUUID, options=None):
        pool = API.StoragePool(self.cif, spUUID)
        return pool.getSpmStatus()

    def poolGetStorageConnections(self, spUUID, options=None):
        pool = API.StoragePool(self.cif, spUUID)
        return pool.getStorageConnections()

    def poolGetInfo(self, spUUID, options=None):
        pool = API.StoragePool(self.cif, spUUID)
        return pool.getInfo()

    def poolMoveMultipleImages(self, spUUID, srcDomUUID, dstDomUUID, imgDict,
                               vmUUID, force=False):
        pool = API.StoragePool(self.cif, spUUID)
        return pool.moveMultipleImages(srcDomUUID, dstDomUUID, imgDict, force)

    def poolReconstructMaster(self, spUUID, poolName, masterDom, domDict,
                              masterVersion, lockPolicy=None,
                              lockRenewalIntervalSec=None, leaseTimeSec=None,
                              ioOpTimeoutSec=None, leaseRetries=None,
                              options=None):
        pool = API.StoragePool(self.cif, spUUID)
        return pool.reconstructMaster(poolName, masterDom, masterVersion,
            domDict, lockRenewalIntervalSec, leaseTimeSec, ioOpTimeoutSec,
            leaseRetries)

    def poolRefresh(self, spUUID, msdUUID, masterVersion, options=None):
        pool = API.StoragePool(self.cif, spUUID)
        return pool.refresh(msdUUID, masterVersion)

    def poolSetDescription(self, spUUID, description, options=None):
        pool = API.StoragePool(self.cif, spUUID)
        return pool.setDescription(description)

    def poolSpmStart(self, spUUID, prevID, prevLVER, recoveryMode,
                     scsiFencing, maxHostID=storage.safelease.MAX_HOST_ID,
                     domVersion=None, options=None):
        pool = API.StoragePool(self.cif, spUUID)
        return pool.spmStart(prevID, prevLVER, scsiFencing,
                             maxHostID, domVersion)

    def poolSpmStop(self, spUUID, options=None):
        pool = API.StoragePool(self.cif, spUUID)
        return pool.spmStop()

    def poolUpgrade(self, spUUID, targetDomVersion):
        pool = API.StoragePool(self.cif, spUUID)
        return pool.upgrade(targetDomVersion)

    def poolValidateStorageServerConnection(self, domType, spUUID, conList,
                                            options=None):
        pool = API.StoragePool(self.cif, spUUID)
        return pool.validateStorageServerConnection(domType, conList)

    def poolUpdateVMs(self, spUUID, vmList, sdUUID=None, options=None):
        pool = API.StoragePool(self.cif, spUUID)
        return pool.updateVMs(vmList, sdUUID)

    def poolRemoveVms(self, spUUID, vmList, sdUUID=None, options=None):
        pool = API.StoragePool(self.cif, spUUID)
        return pool.removeVMs(vmList, sdUUID)

    def volumeCopy(self, sdUUID, spUUID, vmUUID, srcImgUUID, srcVolUUID,
                   dstImgUUID, dstVolUUID, description='',
                   dstSdUUID=storage.sd.BLANK_UUID,
                   volType=storage.volume.SHARED_VOL,
                   volFormat=storage.volume.UNKNOWN_VOL,
                   preallocate=storage.volume.UNKNOWN_VOL, postZero=False,
                   force=False):
        volume = API.Volume(self.cif, srcVolUUID, spUUID, sdUUID, srcImgUUID)
        return volume.copy(dstSdUUID, dstImgUUID, dstVolUUID,
            description, volType, volFormat, preallocate, postZero,
            force)

    def volumeCreate(self, sdUUID, spUUID, imgUUID, size, volFormat,
                     preallocate, diskType, volUUID, desc,
                     srcImgUUID=storage.volume.BLANK_UUID,
                     srcVolUUID=storage.volume.BLANK_UUID):
        volume = API.Volume(self.cif, volUUID, spUUID, sdUUID, imgUUID)
        return volume.create(size, volFormat, preallocate, diskType,
                desc, srcImgUUID, srcVolUUID)

    def volumeExtend(self, sdUUID, spUUID, imgUUID, volUUID, size,
                     isShuttingDown=None):
        volume = API.Volume(self.cif, volUUID, spUUID, sdUUID, imgUUID)
        return volume.extend(size, isShuttingDown)

    def volumeGetInfo(self, sdUUID, spUUID, imgUUID, volUUID):
        volume = API.Volume(self.cif, volUUID, spUUID, sdUUID, imgUUID)
        return volume.getInfo()

    def volumeGetPath(self, sdUUID, spUUID, imgUUID, volUUID):
        volume = API.Volume(self.cif, volUUID, spUUID, sdUUID, imgUUID)
        return volume.getPath()

    def volumeGetSize(self, sdUUID, spUUID, imgUUID, volUUID):
        volume = API.Volume(self.cif, volUUID, spUUID, sdUUID, imgUUID)
        return volume.getSize()

    def volumePrepare(self, sdUUID, spUUID, imgUUID, volUUID, rw=True):
        volume = API.Volume(self.cif, volUUID, spUUID, sdUUID, imgUUID)
        return volume.prepare(rw)

    def volumeRefresh(self, sdUUID, spUUID, imgUUID, volUUID):
        volume = API.Volume(self.cif, volUUID, spUUID, sdUUID, imgUUID)
        return volume.refresh()

    def volumeSetDescription(self, sdUUID, spUUID, imgUUID, volUUID,
                             description):
        volume = API.Volume(self.cif, volUUID, spUUID, sdUUID, imgUUID)
        return volume.setDescription(description)

    def volumeSetLegality(self, sdUUID, spUUID, imgUUID, volUUID, legality):
        volume = API.Volume(self.cif, volUUID, spUUID, sdUUID, imgUUID)
        return volume.setLegality(legality)

    def volumeTearDown(self, sdUUID, spUUID, imgUUID, volUUID):
        volume = API.Volume(self.cif, volUUID, spUUID, sdUUID, imgUUID)
        return volume.tearDown()

    def taskClear(self, taskId):
        task = API.Task(self.cif, taskId)
        return task.clear()

    def taskGetInfo(self, taskId):
        task = API.Task(self.cif, taskId)
        return task.getInfo()

    def taskGetStatus(self, taskId):
        task = API.Task(self.cif, taskId)
        return task.getStatus()

    def taskRevert(self, taskId):
        task = API.Task(self.cif, taskId)
        return task.revert()

    def taskStop(self, taskId):
        task = API.Task(self.cif, taskId)
        return task.stop()

    # Global storage methods
    def tasksGetAllInfo(self):
        api = API.Global(self.cif)
        return api.getAllTasksInfo()

    def tasksGetAllStatuses(self):
        api = API.Global(self.cif)
        return api.getAllTasksStatuses()

    def iscsiDiscoverSendTargets(self, con, options=None):
        iscsiConn = API.ISCSIConnection(self.cif, con['connection'],
            con['port'], con['user'], con['password'])
        return iscsiConn.discoverSendTargets()

    def vgCreate(self, name, devlist):
        vg = API.LVMVolumeGroup(self.cif)
        return vg.create(name, devlist)

    def vgGetInfo(self, vgUUID, options=None):
        vg = API.LVMVolumeGroup(self.cif, vgUUID)
        return vg.getInfo()

    def vgRemove(self, vgUUID, options=None):
        vg = API.LVMVolumeGroup(self.cif, vgUUID)
        return vg.remove()

    def domainsGetList(self, spUUID=None, domainClass=None, storageType=None,
                       remotePath=None, options=None):
        api = API.Global(self.cif)
        return api.getStorageDomains(spUUID, domainClass, storageType,
                                     remotePath)

    def poolsGetConnectedList(self, options=None):
        api = API.Global(self.cif)
        return api.getConnectedStoragePools()

    def storageRepoGetStats(self, options=None):
        api = API.Global(self.cif)
        return api.getStorageRepoStats()

    def vgsGetList(self, storageType=None, options=None):
        api = API.Global(self.cif)
        return api.getLVMVolumeGroups(storageType)

    def devicesGetList(self, storageType=None, options=None):
        api = API.Global(self.cif)
        return api.getDeviceList(storageType)

    def devicesGetVisibility(self, guids, options=None):
        api = API.Global(self.cif)
        return api.getDevicesVisibility(guids)

    def deviceGetInfo(self, guid, options=None):
        api = API.Global(self.cif)
        return api.getDeviceInfo(guid)

    def getGlobalMethods(self):
        return ((self.vmDestroy, 'destroy'),
                (self.vmCreate, 'create'),
                (self.getVMList, 'list'),
                (self.vmPause, 'pause'),
                (self.vmCont, 'cont'),
                (self.vmSnapshot, 'snapshot'),
                (self.vmReset, 'reset'),
                (self.vmShutdown, 'shutdown'),
                (self.vmSetTicket, 'setVmTicket'),
                (self.vmChangeCD, 'changeCD'),
                (self.vmChangeFloppy, 'changeFloppy'),
                (self.vmSendKeys, 'sendkeys'),
                (self.vmMigrate, 'migrate'),
                (self.vmGetMigrationStatus, 'migrateStatus'),
                (self.vmMigrationCancel, 'migrateCancel'),
                (self.getCapabilities, 'getVdsCapabilities'),
                (self.getStats, 'getVdsStats'),
                (self.vmGetStats, 'getVmStats'),
                (self.getAllVmStats, 'getAllVmStats'),
                (self.vmMigrationCreate, 'migrationCreate'),
                (self.vmDesktopLogin, 'desktopLogin'),
                (self.vmDesktopLogoff, 'desktopLogoff'),
                (self.vmDesktopLock, 'desktopLock'),
                (self.vmDesktopSendHcCommand, 'sendHcCmdToDesktop'),
                (self.vmHibernate, 'hibernate'),
                (self.vmMonitorCommand, 'monitorCommand'),
                (self.addNetwork, 'addNetwork'),
                (self.delNetwork, 'delNetwork'),
                (self.editNetwork, 'editNetwork'),
                (self.setupNetworks, 'setupNetworks'),
                (self.ping, 'ping'),
                (self.setSafeNetworkConfig, 'setSafeNetworkConfig'),
                (self.fenceNode, 'fenceNode'),
                (self.cif.prepareForShutdown, 'prepareForShutdown'),
                (self.setLogLevel, 'setLogLevel'),
                (self.vmHotplugDisk, 'hotplugDisk'),
                (self.vmHotunplugDisk, 'hotunplugDisk'))

    def getIrsMethods(self):
        return ((self.domainActivate, 'activateStorageDomain'),
                (self.domainAttach, 'attachStorageDomain'),
                (self.domainCreate, 'createStorageDomain'),
                (self.domainDeactivate, 'deactivateStorageDomain'),
                (self.domainDetach, 'detachStorageDomain'),
                (self.domainDetachForced, 'forcedDetachStorageDomain'),
                (self.domainExtend, 'extendStorageDomain'),
                (self.domainFormat, 'formatStorageDomain'),
                (self.domainGetFileList, 'getFileList'),
                (self.domainGetImages, 'getImagesList'),
                (self.domainGetInfo, 'getStorageDomainInfo'),
                (self.domainGetStats, 'getStorageDomainStats'),
                (self.domainGetVolumes, 'getVolumesList'),
                (self.domainSetDescription, 'setStorageDomainDescription'),
                (self.domainUploadVolume, 'uploadVolume'),
                (self.domainValidate, 'validateStorageDomain'),
                (self.imageDelete, 'deleteImage'),
                (self.imageDeleteVolumes, 'deleteVolume'),
                (self.imageMergeSnapshots, 'mergeShapshots'),
                (self.imageMove, 'moveImage'),
                (self.poolConnect, 'connectStoragePool'),
                (self.poolConnectStorageServer, 'connectStorageServer'),
                (self.poolCreate, 'createStoragePool'),
                (self.poolDestroy, 'destroyStoragePool'),
                (self.poolDisconnect, 'disconnectStoragePool'),
                (self.poolDisconnectStorageServer, 'disconnectStorageServer'),
                (self.poolFenceSPMStorage, 'fenceSpmStorage'),
                (self.poolGetBackedUpVmsInfo, 'getVmsInfo'),
                (self.poolGetBackedUpVmsList, 'getVmsList'),
                (self.poolGetFloppyList, 'getFloppyList'),
                (self.poolGetDomainsContainingImage, 'getImageDomainsList'),
                (self.poolGetIsoList, 'getIsoList'),
                (self.poolGetSpmStatus, 'getSpmStatus'),
                (self.poolGetStorageConnections, 'getStorageConnectionsList'),
                (self.poolGetInfo, 'getStoragePoolInfo'),
                (self.poolMoveMultipleImages, 'moveMultipleImages'),
                (self.poolReconstructMaster, 'reconstructMaster'),
                (self.poolRefresh, 'refreshStoragePool'),
                (self.poolSetDescription, 'setStoragePoolDescription'),
                (self.poolSpmStart, 'spmStart'),
                (self.poolSpmStop, 'spmStop'),
                (self.poolUpgrade, 'upgradeStoragePool'),
                (self.poolValidateStorageServerConnection, 'validateStorageServerConnection'),
                (self.poolUpdateVMs, 'updateVM'),
                (self.poolRemoveVms, 'removeVM'),
                (self.taskClear, 'clearTask'),
                (self.taskGetInfo, 'getTaskInfo'),
                (self.taskGetStatus, 'getTaskStatus'),
                (self.taskRevert, 'revertTask'),
                (self.taskStop, 'stopTask'),
                (self.volumeCopy, 'copyImage'),
                (self.volumeCreate, 'createVolume'),
                (self.volumeExtend, 'extendVolume'),
                (self.volumeGetInfo, 'getVolumeInfo'),
                (self.volumeGetPath, 'getVolumePath'),
                (self.volumeGetSize, 'getVolumeSize'),
                (self.volumePrepare, 'prepareVolume'),
                (self.volumeRefresh, 'refreshVolume'),
                (self.volumeSetDescription, 'setVolumeDescription'),
                (self.volumeSetLegality, 'setVolumeLegality'),
                (self.volumeTearDown, 'teardownVolume'),
                (self.tasksGetAllInfo, 'getAllTasksInfo'),
                (self.tasksGetAllStatuses, 'getAllTasksStatuses'),
                (self.iscsiDiscoverSendTargets, 'discoverSendTargets'),
                (self.vgCreate, 'createVG'),
                (self.vgGetInfo, 'getVGInfo'),
                (self.vgRemove, 'removeVG'),
                (self.domainsGetList, 'getStorageDomainsList'),
                (self.poolsGetConnectedList, 'getConnectedStoragePoolsList'),
                (self.storageRepoGetStats, 'repoStats'),
                (self.vgsGetList, 'getVGList'),
                (self.devicesGetList, 'getDeviceList'),
                (self.devicesGetVisibility, 'getDevicesVisibility'),
                (self.deviceGetInfo, 'getDeviceInfo'),)

def wrapApiMethod(f):
    def wrapper(*args, **kwargs):
        try:
            logLevel = logging.DEBUG
            if f.__name__ in ('list', 'getAllVmStats', 'getVdsStats',
                              'fenceNode'):
                logLevel = logging.TRACE
            displayArgs = args
            if f.__name__ == 'desktopLogin':
                assert 'password' not in kwargs
                if len(args) > 3:
                    displayArgs = args[:3] + ('****',) + args[4:]
            f.im_self.cif.log.log(logLevel, '[%s]::call %s with %s %s',
                              getattr(f.im_self.cif.threadLocal, 'client', ''),
                              f.__name__, displayArgs, kwargs)
            if f.im_self.cif._recovery:
                res = errCode['recovery']
            else:
                res = f(*args, **kwargs)
            f.im_self.cif.log.log(logLevel, 'return %s with %s', f.__name__, res)
            return res
        except libvirt.libvirtError, e:
            f.im_self.cif.log.error(traceback.format_exc())
            if e.get_error_code() == libvirt.VIR_ERR_NO_DOMAIN:
                return errCode['noVM']
            else:
                return errCode['unexpected']
        except:
            f.im_self.cif.log.error(traceback.format_exc())
            return errCode['unexpected']
    wrapper.__name__ = f.__name__
    wrapper.__doc__ = f.__doc__
    return wrapper
