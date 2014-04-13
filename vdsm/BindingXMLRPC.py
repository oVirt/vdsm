#
# Copyright 2012 Red Hat, Inc.
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

from errno import EINTR
import SimpleXMLRPCServer
from vdsm import SecureXMLRPCServer
import json
import httplib
import logging
import libvirt
import threading
import sys

from vdsm import utils
from vdsm.define import doneCode, errCode
from vdsm.netinfo import getDeviceByIP
import API
from vdsm.exception import VdsmException

try:
    from gluster.api import getGlusterMethods

    _glusterEnabled = True
except ImportError:
    _glusterEnabled = False


class BindingXMLRPC(object):
    def __init__(self, cif, log, ip, port, ssl, vds_resp_timeout,
                 trust_store_path, default_bridge):
        self.cif = cif
        self.log = log
        self.serverIP = ip
        self.serverPort = port
        self.enableSSL = ssl
        self.serverRespTimeout = vds_resp_timeout
        self.trustStorePath = trust_store_path
        self.defaultBridge = default_bridge

        self._enabled = False
        self.server = self._createXMLRPCServer()

    def start(self):
        """
        Register xml-rpc functions and serve clients until stopped
        """
        @utils.traceback(on=self.log.name)
        def threaded_start():
            self._registerFunctions()
            self.server.timeout = 1
            self._enabled = True

            while self._enabled:
                try:
                    self.server.handle_request()
                except Exception as e:
                    if e[0] != EINTR:
                        self.log.error("xml-rpc handler exception",
                                       exc_info=True)
        self._thread = threading.Thread(target=threaded_start,
                                        name='BindingXMLRPC')
        self._thread.daemon = True
        self._thread.start()

    def prepareForShutdown(self):
        self._enabled = False
        self.server.server_close()
        self._thread.join()
        return {'status': doneCode}

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
        HTTP_HEADER_FLOWID = "FlowID"

        threadLocal = self.cif.threadLocal

        server_address = (self.serverIP, int(self.serverPort))
        if self.enableSSL:
            basehandler = SecureXMLRPCServer.SecureXMLRPCRequestHandler
        else:
            basehandler = SimpleXMLRPCServer.SimpleXMLRPCRequestHandler

        class RequestHandler(basehandler):

            # Timeout for the request socket
            timeout = 60
            log = logging.getLogger("BindingXMLRPC.RequestHandler")

            HEADER_POOL = 'Storage-Pool-Id'
            HEADER_DOMAIN = 'Storage-Domain-Id'
            HEADER_IMAGE = 'Image-Id'
            HEADER_VOLUME = 'Volume-Id'
            HEADER_TASK_ID = 'Task-Id'
            HEADER_SIZE = 'Size'
            HEADER_CONTENT_LENGTH = 'content-length'
            HEADER_CONTENT_TYPE = 'content-type'

            class RequestException():
                def __init__(self, httpStatusCode, errorMessage):
                    self.httpStatusCode = httpStatusCode
                    self.errorMessage = errorMessage

            def setup(self):
                threadLocal.client = self.client_address[0]
                threadLocal.server = self.request.getsockname()[0]
                return basehandler.setup(self)

            def do_GET(self):
                try:
                    length = self._getIntHeader(self.HEADER_SIZE,
                                                httplib.BAD_REQUEST)
                    img = self._createImage()
                    startEvent = threading.Event()
                    methodArgs = {'fileObj': self.wfile,
                                  'length': length}

                    uploadFinishedEvent, operationEndCallback = \
                        self._createEventWithCallback()

                    # Optional header
                    volUUID = self.headers.getheader(self.HEADER_VOLUME)

                    response = img.uploadToStream(methodArgs,
                                                  operationEndCallback,
                                                  startEvent, volUUID)

                    if response['status']['code'] == 0:
                        self.send_response(httplib.OK)
                        self.send_header(self.HEADER_CONTENT_TYPE,
                                         'application/octet-stream')
                        self.send_header(self.HEADER_CONTENT_LENGTH, length)
                        self.send_header(self.HEADER_TASK_ID, response['uuid'])
                        self.end_headers()
                        startEvent.set()
                        self._waitForEvent(uploadFinishedEvent)
                    else:
                        self._send_error_response(response)

                except self.RequestException as e:
                    # This is an expected exception, so traceback is unneeded
                    self.send_error(e.httpStatusCode, e.errorMessage)
                except Exception:
                    self.send_error(httplib.INTERNAL_SERVER_ERROR,
                                    "error during execution",
                                    exc_info=True)

            def do_PUT(self):
                try:
                    contentLength = self._getIntHeader(
                        self.HEADER_CONTENT_LENGTH,
                        httplib.LENGTH_REQUIRED)

                    img = self._createImage()

                    methodArgs = {'fileObj': self.rfile,
                                  'length': contentLength}

                    uploadFinishedEvent, operationEndCallback = \
                        self._createEventWithCallback()

                    # Optional header
                    volUUID = self.headers.getheader(self.HEADER_VOLUME)

                    response = img.downloadFromStream(methodArgs,
                                                      operationEndCallback,
                                                      volUUID)

                    if response['status']['code'] == 0:
                        while not uploadFinishedEvent.is_set():
                            uploadFinishedEvent.wait()
                        self.send_response(httplib.OK)
                        self.send_header(self.HEADER_TASK_ID, response['uuid'])
                        self.end_headers()
                    else:
                        self._send_error_response(response)

                except self.RequestException as e:
                    self.send_error(e.httpStatusCode, e.errorMessage)
                except Exception:
                    self.send_error(httplib.INTERNAL_SERVER_ERROR,
                                    "error during execution",
                                    exc_info=True)

            def _createImage(self):
                # Required headers
                spUUID = self.headers.getheader(self.HEADER_POOL)
                sdUUID = self.headers.getheader(self.HEADER_DOMAIN)
                imgUUID = self.headers.getheader(self.HEADER_IMAGE)
                if not all((spUUID, sdUUID, imgUUID)):
                    raise self.RequestException(
                        httplib.BAD_REQUEST,
                        "missing or empty required header(s):"
                        " spUUID=%s sdUUID=%s imgUUID=%s"
                        % (spUUID, sdUUID, imgUUID))

                return API.Image(imgUUID, spUUID, sdUUID)

            @staticmethod
            def _createEventWithCallback():
                operationFinishedEvent = threading.Event()

                def setCallback():
                    operationFinishedEvent.set()

                return operationFinishedEvent, setCallback

            @staticmethod
            def _waitForEvent(event):
                while not event.is_set():
                    event.wait()

            def _getIntHeader(self, headerName, missingError):
                value = self.headers.getheader(
                    headerName)
                if not value:
                    raise self.RequestException(
                        missingError,
                        "missing header %s" % headerName)

                try:
                    value = int(value)
                except ValueError:
                    raise self.RequestException(
                        httplib.BAD_REQUEST,
                        "invalid header value %r" % value)

                return value

            def send_error(self, error, message, exc_info=False):
                try:
                    self.log.error(message, exc_info=exc_info)
                    self.send_response(error)
                    self.end_headers()
                except Exception:
                    self.log.error("failed to return response",
                                   exc_info=True)

            def _send_error_response(self, response):
                self.send_response(httplib.INTERNAL_SERVER_ERROR)
                json_response = json.dumps(response)
                self.send_header(self.HEADER_CONTENT_TYPE,
                                 'application/json')
                self.send_header(self.HEADER_CONTENT_LENGTH,
                                 len(json_response))
                self.end_headers()
                self.wfile.write(json_response)

            def parse_request(self):
                r = basehandler.parse_request(self)
                threadLocal.flowID = self.headers.get(HTTP_HEADER_FLOWID)
                return r

            def finish(self):
                basehandler.finish(self)
                threadLocal.client = None
                threadLocal.server = None
                threadLocal.flowID = None

            if sys.version_info[:2] == (2, 6):
                # Override BaseHTTPServer.BaseRequestHandler implementation to
                # avoid pointless and slow attempt to get the fully qualified
                # host name from the client address. This method is not used
                # any more in Python 2.7.
                def address_string(self):
                    return self.client_address[0]

        if self.enableSSL:
            KEYFILE, CERTFILE, CACERT = self._getKeyCertFilenames()
            server = SecureXMLRPCServer.SecureThreadedXMLRPCServer(
                server_address,
                keyfile=KEYFILE, certfile=CERTFILE, ca_certs=CACERT,
                timeout=self.serverRespTimeout,
                requestHandler=RequestHandler)
        else:
            server = utils.SimpleThreadedXMLRPCServer(
                server_address,
                requestHandler=RequestHandler, logRequests=True)
        utils.closeOnExec(server.socket.fileno())

        return server

    def _registerFunctions(self):
        def wrapIrsMethod(f):
            def wrapper(*args, **kwargs):
                fmt = ""
                logargs = []

                if self.cif.threadLocal.client:
                    fmt += "client [%s]"
                    logargs.append(self.cif.threadLocal.client)

                if getattr(self.cif.threadLocal,
                           'flowID', None) is not None:
                    fmt += " flowID [%s]"
                    logargs.append(self.cif.threadLocal.flowID)

                self.log.debug(fmt, *logargs)

                try:
                    return f(*args, **kwargs)
                except:
                    self.log.error("Unexpected exception", exc_info=True)
                    return errCode['unexpected']

            wrapper.__name__ = f.__name__
            wrapper.__doc__ = f.__doc__
            return wrapper

        globalMethods = self.getGlobalMethods()
        irsMethods = self.getIrsMethods()
        if not self.cif.irs:
            err = errCode['recovery'].copy()
            err['status'] = err['status'].copy()
            err['status']['message'] = 'Failed to initialize storage'
            self.server._dispatch = lambda method, params: err

        self.server.register_introspection_functions()
        for (method, name) in globalMethods:
            self.server.register_function(wrapApiMethod(method), name)
        for (method, name) in irsMethods:
            self.server.register_function(wrapIrsMethod(method), name)
        if _glusterEnabled and self.cif.gluster:
            for (method, name) in getGlusterMethods(self.cif.gluster):
                self.server.register_function(wrapApiMethod(method), name)

    #
    # Callable methods:
    #
    def vmDestroy(self, vmId):
        vm = API.VM(vmId)
        return vm.destroy()

    def vmCreate(self, vmParams):
        vm = API.VM(vmParams['vmId'])
        return vm.create(vmParams)

    def getVMList(self, fullStatus=False, vmList=()):
        api = API.Global()
        return api.getVMList(fullStatus, vmList)

    def vmPause(self, vmId):
        vm = API.VM(vmId)
        return vm.pause()

    def vmCont(self, vmId):
        vm = API.VM(vmId)
        return vm.cont()

    def vmReset(self, vmId):
        vm = API.VM(vmId)
        return vm.reset()

    def vmShutdown(self, vmId, delay=None, message=None, reboot=False,
                   timeout=None, force=False):
        vm = API.VM(vmId)
        return vm.shutdown(delay, message, reboot, timeout, force)

    def vmSetTicket(self, vmId, password, ttl,
                    existingConnAction='disconnect', params={}):
        vm = API.VM(vmId)
        return vm.setTicket(password, ttl, existingConnAction, params)

    def vmChangeCD(self, vmId, driveSpec):
        vm = API.VM(vmId)
        return vm.changeCD(driveSpec)

    def vmChangeFloppy(self, vmId, driveSpec):
        vm = API.VM(vmId)
        return vm.changeFloppy(driveSpec)

    def vmSendKeys(self, vmId, keySequence):
        vm = API.VM(vmId)
        return vm.sendKeys(keySequence)

    def vmMigrate(self, params):
        vm = API.VM(params['vmId'])
        return vm.migrate(params)

    def vmGetMigrationStatus(self, vmId):
        vm = API.VM(vmId)
        return vm.getMigrationStatus()

    def vmMigrationCancel(self, vmId):
        vm = API.VM(vmId)
        return vm.migrateCancel()

    def vmHotplugDisk(self, params):
        vm = API.VM(params['vmId'])
        return vm.hotplugDisk(params)

    def vmHotunplugDisk(self, params):
        vm = API.VM(params['vmId'])
        return vm.hotunplugDisk(params)

    def vmHotplugNic(self, params):
        vm = API.VM(params['vmId'])
        return vm.hotplugNic(params)

    def vmHotunplugNic(self, params):
        vm = API.VM(params['vmId'])
        return vm.hotunplugNic(params)

    def vmUpdateDevice(self, vmId, params):
        vm = API.VM(vmId)
        return vm.vmUpdateDevice(params)

    def vmSetNumberOfCpus(self, vmId, numberOfCpus):
        vm = API.VM(vmId)
        return vm.setNumberOfCpus(vmId, numberOfCpus)

    def vmSnapshot(self, vmId, snapDrives, snapMemVolHandle=''):
        """
        Take snapshot of VM

        :param snapMemVolHandle:
            memory snapshots are not supported in cluster level: default value.
            vm snapshot should contain memory: a comma-separated string of IDs:
             domain,pool,image1,volume1,image2,volume2 (hibernation volumes
             representation).
            vm snapshot should not contain memory: empty string
        :type snapMemVolHandle: string
        """
        vm = API.VM(vmId)
        return vm.snapshot(snapDrives, snapMemVolHandle)

    def vmSetBalloonTarget(self, vmId, target):
        vm = API.VM(vmId)
        return vm.setBalloonTarget(target)

    def getCapabilities(self):
        api = API.Global()
        ret = api.getCapabilities()
        ret['info']['management_ip'] = self.serverIP
        ret['info']['lastClient'] = self.cif.threadLocal.client
        ret['info']['lastClientIface'] = getDeviceByIP(
            self.cif.threadLocal.server)
        return ret

    def getHardwareInfo(self):
        api = API.Global()
        return api.getHardwareInfo()

    def getStats(self):
        api = API.Global()
        return api.getStats()

    def vmGetStats(self, vmId):
        vm = API.VM(vmId)
        return vm.getStats()

    def getAllVmStats(self):
        api = API.Global()
        return api.getAllVmStats()

    def vmMigrationCreate(self, params):
        vm = API.VM(params['vmId'])
        return vm.migrationCreate(params)

    def vmDesktopLogin(self, vmId, domain, user, password):
        vm = API.VM(vmId)
        return vm.desktopLogin(domain, user, password)

    def vmDesktopLogoff(self, vmId, force):
        vm = API.VM(vmId)
        return vm.desktopLogoff(force)

    def vmDesktopLock(self, vmId):
        vm = API.VM(vmId)
        return vm.desktopLock()

    def vmDesktopSendHcCommand(self, vmId, message):
        vm = API.VM(vmId)
        return vm.desktopSendHcCommand(message)

    def vmHibernate(self, vmId, hiberVolHandle):
        vm = API.VM(vmId)
        return vm.hibernate(hiberVolHandle)

    def vmMonitorCommand(self, vmId, cmd):
        vm = API.VM(vmId)
        return vm.monitorCommand(cmd)

    def vmDiskReplicateStart(self, vmId, srcDisk, dstDisk):
        vm = API.VM(vmId)
        return vm.diskReplicateStart(srcDisk, dstDisk)

    def vmDiskReplicateFinish(self, vmId, srcDisk, dstDisk):
        vm = API.VM(vmId)
        return vm.diskReplicateFinish(srcDisk, dstDisk)

    def diskGetAlignment(self, vmId, driveSpecs):
        api = API.VM(vmId)
        return api.getDiskAlignment(driveSpecs)

    def diskSizeExtend(self, vmId, driveSpecs, newSize):
        if vmId == API.VM.BLANK_UUID:
            try:
                volume = API.Volume(
                    driveSpecs['volumeID'], driveSpecs['poolID'],
                    driveSpecs['domainID'], driveSpecs['imageID'])
            except KeyError:
                return errCode['imageErr']
            return volume.updateSize(newSize)
        else:
            vm = API.VM(vmId)
            return vm.diskSizeExtend(driveSpecs, newSize)

    def addNetwork(self, bridge, vlan=None, bond=None, nics=None,
                   options=None):
        api = API.Global()
        return api.addNetwork(bridge, vlan, bond, nics, options)

    def delNetwork(self, bridge, vlan=None, bond=None, nics=None,
                   options=None):
        api = API.Global()
        return api.delNetwork(bridge, vlan, bond, nics, options)

    def editNetwork(self, oldBridge, newBridge, vlan=None, bond=None,
                    nics=None, options=None):
        api = API.Global()
        return api.editNetwork(oldBridge, newBridge, vlan, bond, nics, options)

    def setupNetworks(self, networks, bondings, options):
        api = API.Global()
        return api.setupNetworks(networks, bondings, options)

    def ping(self):
        api = API.Global()
        return api.ping()

    def setSafeNetworkConfig(self):
        api = API.Global()
        return api.setSafeNetworkConfig()

    def fenceNode(self, addr, port, agent, username, password, action,
                  secure=False, options=''):
        api = API.Global()
        return api.fenceNode(addr, port, agent, username, password, action,
                             secure, options)

    def setLogLevel(self, level):
        api = API.Global()
        return api.setLogLevel(level)

    def setMOMPolicy(self, policy):
        api = API.Global()
        return api.setMOMPolicy(policy)

    def setMOMPolicyParameters(self, key_value_store):
        api = API.Global()
        return api.setMOMPolicyParameters(key_value_store)

    def setHaMaintenanceMode(self, mode, enabled):
        api = API.Global()
        return api.setHaMaintenanceMode(mode, enabled)

    def domainActivate(self, sdUUID, spUUID, options=None):
        domain = API.StorageDomain(sdUUID)
        return domain.activate(spUUID)

    def domainAttach(self, sdUUID, spUUID, options=None):
        domain = API.StorageDomain(sdUUID)
        return domain.attach(spUUID)

    def domainCreate(self, storageType, sdUUID, domainName,
                     typeSpecificArg, domClass,
                     domVersion=None, options=None):
        domain = API.StorageDomain(sdUUID)
        return domain.create(storageType, typeSpecificArg, domainName,
                             domClass, domVersion)

    def domainDeactivate(self, sdUUID, spUUID, msdUUID, masterVersion,
                         options=None):
        domain = API.StorageDomain(sdUUID)
        return domain.deactivate(spUUID, msdUUID, masterVersion)

    def domainDetach(self, sdUUID, spUUID, msdUUID, masterVersion,
                     options=None):
        domain = API.StorageDomain(sdUUID)
        return domain.detach(spUUID, msdUUID, masterVersion, force=False)

    def domainDetachForced(self, sdUUID, spUUID, options=None):
        domain = API.StorageDomain(sdUUID)
        return domain.detach(spUUID, None, None, force=True)

    def domainExtend(self, sdUUID, spUUID, devlist, force=False, options=None):
        domain = API.StorageDomain(sdUUID)
        return domain.extend(spUUID, devlist, force)

    def domainFormat(self, sdUUID,
                     autoDetach=False, options=None):
        domain = API.StorageDomain(sdUUID)
        return domain.format(autoDetach)

    def domainGetFileStats(self, sdUUID, pattern='*',
                           caseSensitive=False, options=None):
        domain = API.StorageDomain(sdUUID)
        return domain.getFileStats(pattern, caseSensitive)

    def domainGetImages(self, sdUUID, options=None):
        domain = API.StorageDomain(sdUUID)
        return domain.getImages()

    def domainGetInfo(self, sdUUID, options=None):
        domain = API.StorageDomain(sdUUID)
        return domain.getInfo()

    def domainGetStats(self, sdUUID, options=None):
        domain = API.StorageDomain(sdUUID)
        return domain.getStats()

    def domainGetVolumes(self, sdUUID, spUUID,
                         imgUUID=API.Image.BLANK_UUID):
        domain = API.StorageDomain(sdUUID)
        return domain.getVolumes(spUUID, imgUUID)

    def domainSetDescription(self, sdUUID, description, options=None):
        domain = API.StorageDomain(sdUUID)
        return domain.setDescription(description)

    def domainValidate(self, sdUUID, options=None):
        domain = API.StorageDomain(sdUUID)
        return domain.validate()

    def imageDelete(self, sdUUID, spUUID,
                    imgUUID, postZero=False, force=False):
        image = API.Image(imgUUID, spUUID, sdUUID)
        return image.delete(postZero, force)

    def imageDeleteVolumes(self, sdUUID, spUUID, imgUUID, volumes,
                           postZero=False, force=False):
        image = API.Image(imgUUID, spUUID, sdUUID)
        return image.deleteVolumes(volumes, postZero, force)

    def imageMergeSnapshots(self, sdUUID, spUUID, vmUUID, imgUUID,
                            ancestor, successor, postZero=False):
        image = API.Image(imgUUID, spUUID, sdUUID)
        return image.mergeSnapshots(ancestor, successor, postZero)

    def imageMove(self, spUUID, srcDomUUID, dstDomUUID, imgUUID, vmUUID,
                  op, postZero=False, force=False):
        image = API.Image(imgUUID, spUUID, srcDomUUID)
        return image.move(dstDomUUID, op, postZero, force)

    def imageCloneStructure(self, spUUID, sdUUID, imgUUID, dstSdUUID):
        image = API.Image(imgUUID, spUUID, sdUUID)
        return image.cloneStructure(dstSdUUID)

    def imageSyncData(self, spUUID, sdUUID, imgUUID, dstSdUUID, syncType):
        image = API.Image(imgUUID, spUUID, sdUUID)
        return image.syncData(dstSdUUID, syncType)

    def imageUpload(self, methodArgs, spUUID, sdUUID, imgUUID, volUUID=None):
        image = API.Image(imgUUID, spUUID, sdUUID)
        return image.upload(methodArgs, volUUID)

    def imageDownload(self, methodArgs, spUUID, sdUUID, imgUUID, volUUID=None):
        image = API.Image(imgUUID, spUUID, sdUUID)
        return image.download(methodArgs, volUUID)

    def poolConnect(self, spUUID, hostID, scsiKey, msdUUID, masterVersion,
                    domainsMap=None, options=None):
        pool = API.StoragePool(spUUID)
        return pool.connect(hostID, scsiKey, msdUUID, masterVersion,
                            domainsMap)

    def poolConnectStorageServer(self, domType, spUUID, conList, options=None):
        pool = API.StoragePool(spUUID)
        return pool.connectStorageServer(domType, conList)

    def poolCreate(self, poolType, spUUID, poolName, masterDom, domList,
                   masterVersion, lockPolicy=None, lockRenewalIntervalSec=None,
                   leaseTimeSec=None, ioOpTimeoutSec=None,
                   leaseRetries=None, options=None):
        pool = API.StoragePool(spUUID)
        return pool.create(poolName, masterDom, masterVersion, domList,
                           lockRenewalIntervalSec, leaseTimeSec,
                           ioOpTimeoutSec, leaseRetries)

    def poolDestroy(self, spUUID, hostID, scsiKey, options=None):
        pool = API.StoragePool(spUUID)
        return pool.destroy(hostID, scsiKey)

    def poolDisconnect(self, spUUID, hostID, scsiKey, remove=False,
                       options=None):
        pool = API.StoragePool(spUUID)
        return pool.disconnect(hostID, scsiKey, remove)

    def poolDisconnectStorageServer(self, domType, spUUID, conList,
                                    options=None):
        pool = API.StoragePool(spUUID)
        return pool.disconnectStorageServer(domType, conList)

    def poolFenceSPMStorage(self, spUUID, lastOwner, lastLver, options=None):
        pool = API.StoragePool(spUUID)
        return pool.fence()

    def poolGetBackedUpVmsInfo(self, spUUID, sdUUID,
                               vmList=None, options=None):
        pool = API.StoragePool(spUUID)
        return pool.getBackedUpVmsInfo(sdUUID, vmList)

    def poolGetBackedUpVmsList(self, spUUID, sdUUID=None, options=None):
        pool = API.StoragePool(spUUID)
        return pool.getBackedUpVmsList(sdUUID)

    def poolGetFloppyList(self, spUUID, options=None):
        pool = API.StoragePool(spUUID)
        return pool.getFloppyList()

    def poolGetDomainsContainingImage(self, spUUID, imgUUID, options=None):
        pool = API.StoragePool(spUUID)
        return pool.getDomainsContainingImage(imgUUID)

    def poolGetIsoList(self, spUUID, extension='iso', options=None):
        pool = API.StoragePool(spUUID)
        return pool.getIsoList(extension)

    def poolGetSpmStatus(self, spUUID, options=None):
        pool = API.StoragePool(spUUID)
        return pool.getSpmStatus()

    def poolGetInfo(self, spUUID, options=None):
        pool = API.StoragePool(spUUID)
        return pool.getInfo()

    def poolMoveMultipleImages(self, spUUID, srcDomUUID, dstDomUUID, imgDict,
                               vmUUID, force=False):
        pool = API.StoragePool(spUUID)
        return pool.moveMultipleImages(srcDomUUID, dstDomUUID, imgDict, force)

    def poolReconstructMaster(self, spUUID, poolName, masterDom, domDict,
                              masterVersion, lockPolicy=None,
                              lockRenewalIntervalSec=None, leaseTimeSec=None,
                              ioOpTimeoutSec=None, leaseRetries=None,
                              hostId=None, options=None):
        pool = API.StoragePool(spUUID)
        return pool.reconstructMaster(
            hostId, poolName, masterDom, masterVersion, domDict,
            lockRenewalIntervalSec, leaseTimeSec, ioOpTimeoutSec, leaseRetries)

    def poolRefresh(self, spUUID, msdUUID, masterVersion, options=None):
        pool = API.StoragePool(spUUID)
        return pool.refresh(msdUUID, masterVersion)

    def poolSetDescription(self, spUUID, description, options=None):
        pool = API.StoragePool(spUUID)
        return pool.setDescription(description)

    def poolSpmStart(self, spUUID, prevID, prevLVER, recoveryMode,
                     scsiFencing, maxHostID=None,
                     domVersion=None, options=None):
        pool = API.StoragePool(spUUID)
        return pool.spmStart(prevID, prevLVER, scsiFencing,
                             maxHostID, domVersion)

    def poolSpmStop(self, spUUID, options=None):
        pool = API.StoragePool(spUUID)
        return pool.spmStop()

    def poolUpgrade(self, spUUID, targetDomVersion):
        pool = API.StoragePool(spUUID)
        return pool.upgrade(targetDomVersion)

    def poolValidateStorageServerConnection(self, domType, spUUID, conList,
                                            options=None):
        pool = API.StoragePool(spUUID)
        return pool.validateStorageServerConnection(domType, conList)

    def poolUpdateVMs(self, spUUID, vmList, sdUUID=None, options=None):
        pool = API.StoragePool(spUUID)
        return pool.updateVMs(vmList, sdUUID)

    def poolRemoveVm(self, spUUID, vmUUID, sdUUID=None, options=None):
        pool = API.StoragePool(spUUID)
        return pool.removeVM(vmUUID, sdUUID)

    def volumeCopy(self, sdUUID, spUUID, vmUUID, srcImgUUID, srcVolUUID,
                   dstImgUUID, dstVolUUID, description='',
                   dstSdUUID=API.StorageDomain.BLANK_UUID,
                   volType=API.Volume.Roles.SHARED,
                   volFormat=API.Volume.Formats.UNKNOWN,
                   preallocate=API.Volume.Types.UNKNOWN, postZero=False,
                   force=False):
        volume = API.Volume(srcVolUUID, spUUID, sdUUID, srcImgUUID)
        return volume.copy(dstSdUUID, dstImgUUID, dstVolUUID, description,
                           volType, volFormat, preallocate, postZero, force)

    def volumeCreate(self, sdUUID, spUUID, imgUUID, size, volFormat,
                     preallocate, diskType, volUUID, desc,
                     srcImgUUID=API.Image.BLANK_UUID,
                     srcVolUUID=API.Volume.BLANK_UUID):
        volume = API.Volume(volUUID, spUUID, sdUUID, imgUUID)
        return volume.create(size, volFormat, preallocate, diskType, desc,
                             srcImgUUID, srcVolUUID)

    def volumeExtendSize(self, spUUID, sdUUID, imgUUID, volUUID, newSize):
        volume = API.Volume(volUUID, spUUID, sdUUID, imgUUID)
        return volume.extendSize(newSize)

    def volumeGetInfo(self, sdUUID, spUUID, imgUUID, volUUID):
        volume = API.Volume(volUUID, spUUID, sdUUID, imgUUID)
        return volume.getInfo()

    def volumeGetPath(self, sdUUID, spUUID, imgUUID, volUUID):
        volume = API.Volume(volUUID, spUUID, sdUUID, imgUUID)
        return volume.getPath()

    def volumeGetSize(self, sdUUID, spUUID, imgUUID, volUUID):
        volume = API.Volume(volUUID, spUUID, sdUUID, imgUUID)
        return volume.getSize()

    def volumeSetSize(self, sdUUID, spUUID, imgUUID, volUUID, newSize):
        volume = API.Volume(volUUID, spUUID, sdUUID, imgUUID)
        return volume.setSize(newSize)

    def volumePrepare(self, sdUUID, spUUID, imgUUID, volUUID, rw=True):
        volume = API.Volume(volUUID, spUUID, sdUUID, imgUUID)
        return volume.prepare(rw)

    def volumeRefresh(self, sdUUID, spUUID, imgUUID, volUUID):
        volume = API.Volume(volUUID, spUUID, sdUUID, imgUUID)
        return volume.refresh()

    def volumeSetDescription(self, sdUUID, spUUID, imgUUID, volUUID,
                             description):
        volume = API.Volume(volUUID, spUUID, sdUUID, imgUUID)
        return volume.setDescription(description)

    def volumeSetLegality(self, sdUUID, spUUID, imgUUID, volUUID, legality):
        volume = API.Volume(volUUID, spUUID, sdUUID, imgUUID)
        return volume.setLegality(legality)

    def volumeTearDown(self, sdUUID, spUUID, imgUUID, volUUID):
        volume = API.Volume(volUUID, spUUID, sdUUID, imgUUID)
        return volume.tearDown()

    def taskClear(self, taskId):
        task = API.Task(taskId)
        return task.clear()

    def taskGetInfo(self, taskId):
        task = API.Task(taskId)
        return task.getInfo()

    def taskGetStatus(self, taskId):
        task = API.Task(taskId)
        return task.getStatus()

    def taskRevert(self, taskId):
        task = API.Task(taskId)
        return task.revert()

    def taskStop(self, taskId):
        task = API.Task(taskId)
        return task.stop()

    # Global storage methods
    def tasksGetAllInfo(self):
        api = API.Global()
        return api.getAllTasksInfo()

    def tasksGetAllStatuses(self):
        api = API.Global()
        return api.getAllTasksStatuses()

    def tasksGetAll(self, options=None):
        api = API.Global()
        return api.getAllTasks()

    def iscsiDiscoverSendTargets(self, con, options=None):
        iscsiConn = API.ISCSIConnection(con['connection'], con['port'],
                                        con['user'], con['password'])
        return iscsiConn.discoverSendTargets()

    def vgCreate(self, name, devlist, force=False):
        vg = API.LVMVolumeGroup(self.cif)
        return vg.create(name, devlist, force)

    def vgGetInfo(self, vgUUID, options=None):
        vg = API.LVMVolumeGroup(vgUUID)
        return vg.getInfo()

    def vgRemove(self, vgUUID, options=None):
        vg = API.LVMVolumeGroup(vgUUID)
        return vg.remove()

    def domainsGetList(self, spUUID=None, domainClass=None, storageType=None,
                       remotePath=None, options=None):
        api = API.Global()
        return api.getStorageDomains(spUUID, domainClass, storageType,
                                     remotePath)

    def poolsGetConnectedList(self, options=None):
        api = API.Global()
        return api.getConnectedStoragePools()

    def storageRepoGetStats(self, options=None):
        api = API.Global()
        return api.getStorageRepoStats()

    def startMonitoringDomain(self, sdUUID, hostID, options=None):
        api = API.Global()
        return api.startMonitoringDomain(sdUUID, hostID)

    def stopMonitoringDomain(self, sdUUID, options=None):
        api = API.Global()
        return api.stopMonitoringDomain(sdUUID)

    def vgsGetList(self, storageType=None, options=None):
        api = API.Global()
        return api.getLVMVolumeGroups(storageType)

    def devicesGetList(self, storageType=None, options=None):
        api = API.Global()
        return api.getDeviceList(storageType)

    def devicesGetVisibility(self, guids, options=None):
        api = API.Global()
        return api.getDevicesVisibility(guids)

    def storageServerConnectionRefsAcquire(self, conRefArgs):
        return API.ConnectionRefs().acquire(conRefArgs)

    def storageServerConnectionRefsRelease(self, refIDs):
        return API.ConnectionRefs().release(refIDs)

    def storageServerConnectionRefsStatuses(self):
        return API.ConnectionRefs().statuses()

    def getGlobalMethods(self):
        return ((self.vmDestroy, 'destroy'),
                (self.vmCreate, 'create'),
                (self.getVMList, 'list'),
                (self.vmPause, 'pause'),
                (self.vmCont, 'cont'),
                (self.vmSnapshot, 'snapshot'),
                (self.vmSetBalloonTarget, 'setBalloonTarget'),
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
                (self.getHardwareInfo, 'getVdsHardwareInfo'),
                (self.diskGetAlignment, 'getDiskAlignment'),
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
                (self.vmDiskReplicateStart, 'diskReplicateStart'),
                (self.vmDiskReplicateFinish, 'diskReplicateFinish'),
                (self.diskSizeExtend, 'diskSizeExtend'),
                (self.addNetwork, 'addNetwork'),
                (self.delNetwork, 'delNetwork'),
                (self.editNetwork, 'editNetwork'),
                (self.setupNetworks, 'setupNetworks'),
                (self.ping, 'ping'),
                (self.setSafeNetworkConfig, 'setSafeNetworkConfig'),
                (self.fenceNode, 'fenceNode'),
                (self.prepareForShutdown, 'prepareForShutdown'),
                (self.setLogLevel, 'setLogLevel'),
                (self.setMOMPolicy, 'setMOMPolicy'),
                (self.setMOMPolicyParameters, 'setMOMPolicyParameters'),
                (self.setHaMaintenanceMode, 'setHaMaintenanceMode'),
                (self.vmHotplugDisk, 'hotplugDisk'),
                (self.vmHotunplugDisk, 'hotunplugDisk'),
                (self.vmHotplugNic, 'hotplugNic'),
                (self.vmHotunplugNic, 'hotunplugNic'),
                (self.vmUpdateDevice, 'vmUpdateDevice'),
                (self.vmSetNumberOfCpus, 'setNumberOfCpus'))

    def getIrsMethods(self):
        return ((self.domainActivate, 'activateStorageDomain'),
                (self.domainAttach, 'attachStorageDomain'),
                (self.domainCreate, 'createStorageDomain'),
                (self.domainDeactivate, 'deactivateStorageDomain'),
                (self.domainDetach, 'detachStorageDomain'),
                (self.domainDetachForced, 'forcedDetachStorageDomain'),
                (self.domainExtend, 'extendStorageDomain'),
                (self.domainFormat, 'formatStorageDomain'),
                (self.domainGetFileStats, 'getFileStats'),
                (self.domainGetImages, 'getImagesList'),
                (self.domainGetInfo, 'getStorageDomainInfo'),
                (self.domainGetStats, 'getStorageDomainStats'),
                (self.domainGetVolumes, 'getVolumesList'),
                (self.domainSetDescription, 'setStorageDomainDescription'),
                (self.domainValidate, 'validateStorageDomain'),
                (self.imageDelete, 'deleteImage'),
                (self.imageDeleteVolumes, 'deleteVolume'),
                (self.imageMergeSnapshots, 'mergeSnapshots'),
                (self.imageMove, 'moveImage'),
                (self.imageCloneStructure, 'cloneImageStructure'),
                (self.imageSyncData, 'syncImageData'),
                (self.imageUpload, 'uploadImage'),
                (self.imageDownload, 'downloadImage'),
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
                (self.poolGetInfo, 'getStoragePoolInfo'),
                (self.poolMoveMultipleImages, 'moveMultipleImages'),
                (self.poolReconstructMaster, 'reconstructMaster'),
                (self.poolRefresh, 'refreshStoragePool'),
                (self.poolSetDescription, 'setStoragePoolDescription'),
                (self.poolSpmStart, 'spmStart'),
                (self.poolSpmStop, 'spmStop'),
                (self.poolUpgrade, 'upgradeStoragePool'),
                (self.poolValidateStorageServerConnection,
                 'validateStorageServerConnection'),
                (self.poolUpdateVMs, 'updateVM'),
                (self.poolRemoveVm, 'removeVM'),
                (self.taskClear, 'clearTask'),
                (self.taskGetInfo, 'getTaskInfo'),
                (self.taskGetStatus, 'getTaskStatus'),
                (self.taskRevert, 'revertTask'),
                (self.taskStop, 'stopTask'),
                (self.volumeCopy, 'copyImage'),
                (self.volumeCreate, 'createVolume'),
                (self.volumeExtendSize, 'extendVolumeSize'),
                (self.volumeGetInfo, 'getVolumeInfo'),
                (self.volumeGetPath, 'getVolumePath'),
                (self.volumeGetSize, 'getVolumeSize'),
                (self.volumeSetSize, 'volumeSetSize'),
                (self.volumePrepare, 'prepareVolume'),
                (self.volumeRefresh, 'refreshVolume'),
                (self.volumeSetDescription, 'setVolumeDescription'),
                (self.volumeSetLegality, 'setVolumeLegality'),
                (self.volumeTearDown, 'teardownVolume'),
                (self.tasksGetAllInfo, 'getAllTasksInfo'),
                (self.tasksGetAllStatuses, 'getAllTasksStatuses'),
                (self.tasksGetAll, 'getAllTasks'),
                (self.iscsiDiscoverSendTargets, 'discoverSendTargets'),
                (self.vgCreate, 'createVG'),
                (self.vgGetInfo, 'getVGInfo'),
                (self.vgRemove, 'removeVG'),
                (self.domainsGetList, 'getStorageDomainsList'),
                (self.poolsGetConnectedList, 'getConnectedStoragePoolsList'),
                (self.storageRepoGetStats, 'repoStats'),
                (self.startMonitoringDomain, 'startMonitoringDomain'),
                (self.stopMonitoringDomain, 'stopMonitoringDomain'),
                (self.vgsGetList, 'getVGList'),
                (self.devicesGetList, 'getDeviceList'),
                (self.devicesGetVisibility, 'getDevicesVisibility'),
                (self.storageServerConnectionRefsAcquire,
                 'storageServer_ConnectionRefs_acquire'),
                (self.storageServerConnectionRefsRelease,
                 'storageServer_ConnectionRefs_release'),
                (self.storageServerConnectionRefsStatuses,
                 'storageServer_ConnectionRefs_statuses'),)


def wrapApiMethod(f):
    def wrapper(*args, **kwargs):
        try:
            logLevel = logging.DEBUG
            if f.__name__ in ('getVMList', 'getAllVmStats', 'getStats',
                              'fenceNode'):
                logLevel = logging.TRACE
            displayArgs = args
            if f.__name__ == 'vmDesktopLogin':
                assert 'password' not in kwargs
                if len(args) > 3:
                    displayArgs = args[:3] + ('****',) + args[4:]

            # Logging current call
            logStr = 'client [%s]::call %s with %s %s' % \
                (getattr(f.im_self.cif.threadLocal, 'client', ''),
                 f.__name__, displayArgs, kwargs)

            # if flowID exists
            if getattr(f.im_self.cif.threadLocal, 'flowID', None) is not None:
                logStr += " flowID [%s]" % f.im_self.cif.threadLocal.flowID

            # Ready to show the log into vdsm.log
            f.im_self.log.log(logLevel, logStr)

            if f.im_self.cif.ready:
                res = f(*args, **kwargs)
            else:
                res = errCode['recovery']
            f.im_self.cif.log.log(logLevel, 'return %s with %s',
                                  f.__name__, res)
            return res
        except libvirt.libvirtError as e:
            f.im_self.cif.log.error("libvirt error", exc_info=True)
            if e.get_error_code() == libvirt.VIR_ERR_NO_DOMAIN:
                return errCode['noVM']
            else:
                return errCode['unexpected']
        except VdsmException as e:
            f.im_self.cif.log.error("vdsm exception occured", exc_info=True)
            return e.response()
        except:
            f.im_self.cif.log.error("unexpected error", exc_info=True)
            return errCode['unexpected']
    wrapper.__name__ = f.__name__
    wrapper.__doc__ = f.__doc__
    return wrapper
