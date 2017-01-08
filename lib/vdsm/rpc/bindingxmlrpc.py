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

from __future__ import absolute_import
from errno import EINTR
import json
import httplib
import logging
import libvirt
import threading
import re

from vdsm.common.exception import VdsmException
from vdsm.password import (ProtectedPassword,
                           protect_passwords,
                           unprotect_passwords)
from vdsm import concurrent
from vdsm import utils
from vdsm import xmlrpc
from vdsm.common.define import doneCode, errCode
from vdsm.logUtils import Suppressed
from vdsm.network.netinfo.addresses import getDeviceByIP
import API

try:
    from vdsm.gluster.api import getGlusterMethods

    _glusterEnabled = True
except ImportError:
    _glusterEnabled = False


class BindingXMLRPC(object):
    def __init__(self, cif, log):
        self.cif = cif
        self.log = log

        self._enabled = False
        self.server = self._createXMLRPCServer()

    def start(self):
        """
        Register xml-rpc functions and serve clients until stopped
        """
        def threaded_start():
            self.log.info("XMLRPC server running")
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
            self.log.info("XMLRPC server stopped")

        self._thread = concurrent.thread(threaded_start, name='BindingXMLRPC',
                                         log=self.log)
        self._thread.start()

    def add_socket(self, connected_socket, socket_address):
        self.server.add(connected_socket, socket_address)

    def stop(self):
        self.log.info("Stopping XMLRPC server")
        self._enabled = False
        self.server.server_close()
        self._thread.join()
        return {'status': doneCode}

    def _createXMLRPCServer(self):
        """
        Create xml-rpc server over http
        """
        HTTP_HEADER_FLOWID = "FlowID"

        threadLocal = self.cif.threadLocal

        class RequestHandler(xmlrpc.IPXMLRPCRequestHandler):

            # Timeout for the request socket
            timeout = 60
            log = logging.getLogger("BindingXMLRPC.RequestHandler")

            HEADER_POOL = 'Storage-Pool-Id'
            HEADER_DOMAIN = 'Storage-Domain-Id'
            HEADER_IMAGE = 'Image-Id'
            HEADER_VOLUME = 'Volume-Id'
            HEADER_TASK_ID = 'Task-Id'
            HEADER_RANGE = 'Range'
            HEADER_CONTENT_LENGTH = 'content-length'
            HEADER_CONTENT_TYPE = 'content-type'
            HEADER_CONTENT_RANGE = 'content-range'

            class RequestException(Exception):
                def __init__(self, httpStatusCode, errorMessage):
                    self.httpStatusCode = httpStatusCode
                    self.errorMessage = errorMessage

            def setup(self):
                threadLocal.client = self.client_address[0]
                threadLocal.server = self.request.getsockname()[0]
                return xmlrpc.IPXMLRPCRequestHandler.setup(self)

            def do_GET(self):
                try:
                    length = self._getLength()
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
                        self.send_response(httplib.PARTIAL_CONTENT)
                        self.send_header(self.HEADER_CONTENT_TYPE,
                                         'application/octet-stream')
                        self.send_header(self.HEADER_CONTENT_LENGTH, length)
                        self.send_header(self.HEADER_CONTENT_RANGE,
                                         "bytes 0-%d" % (length - 1))
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
                value = self._getRequiredHeader(headerName, missingError)

                return self._getInt(value)

            def _getRequiredHeader(self, headerName, missingError):
                value = self.headers.getheader(
                    headerName)
                if not value:
                    raise self.RequestException(
                        missingError,
                        "missing header %s" % headerName)
                return value

            def _getInt(self, value):
                try:
                    return int(value)
                except ValueError:
                    raise self.RequestException(
                        httplib.BAD_REQUEST,
                        "not int value %r" % value)

            def _getLength(self):
                value = self._getRequiredHeader(self.HEADER_RANGE,
                                                httplib.BAD_REQUEST)

                m = re.match(r'^bytes=0-(\d+)$', value)
                if m is None:
                    raise self.RequestException(
                        httplib.BAD_REQUEST,
                        "Unsupported range: %r , expected: bytes=0-last_byte" %
                        value)

                last_byte = m.group(1)
                return self._getInt(last_byte) + 1

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
                r = xmlrpc.IPXMLRPCRequestHandler.parse_request(self)
                threadLocal.flowID = self.headers.get(HTTP_HEADER_FLOWID)
                return r

            def finish(self):
                xmlrpc.IPXMLRPCRequestHandler.finish(self)
                threadLocal.client = None
                threadLocal.server = None
                threadLocal.flowID = None

        server = xmlrpc.SimpleThreadedXMLRPCServer(
            requestHandler=RequestHandler,
            logRequests=False)

        return server

    def _registerFunctions(self):
        def wrapIrsMethod(f):
            def wrapper(*args, **kwargs):
                start_time = utils.monotonic_time()

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

                res = {}
                try:
                    res = f(*args, **kwargs)
                except:
                    self.log.error("Unexpected exception", exc_info=True)
                    res = errCode['unexpected']
                self.log.info("RPC call %s finished (code=%s) in "
                              "%.2f seconds",
                              f.__name__,
                              res.get('status', {}).get('code'),
                              utils.monotonic_time() - start_time)
                return res

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
            self.server.register_function(_wrap_api_method(method, self.cif),
                                          name)
        for (method, name) in irsMethods:
            self.server.register_function(wrapIrsMethod(method), name)
        if _glusterEnabled and self.cif.gluster:
            for (method, name) in getGlusterMethods(self.cif.gluster):
                self.server.register_function(
                    _wrap_api_method(method, self.cif), name)

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
        API.updateTimestamp()  # required for setupNetworks flow
        api = API.Global()
        return api.getVMList(fullStatus, vmList, False)

    def hostGetJobs(self, job_type=None, job_ids=()):
        api = API.Global()
        job_type = None if job_type == '' else job_type
        return api.getJobs(job_type=job_type, job_ids=job_ids)

    def getExternalVMs(self, uri, username, password, vm_names=None):
        password = ProtectedPassword(password)
        api = API.Global()
        return api.getExternalVMs(uri, username, password, vm_names)

    def getExternalVMNames(self, uri, username, password):
        password = ProtectedPassword(password)
        api = API.Global()
        return api.getExternalVMNames(uri, username, password)

    def getExternalVmFromOva(self, ova_path):
        api = API.Global()
        return api.getExternalVmFromOva(ova_path)

    def convertExternalVm(self, uri, username, password, vminfo, jobid):
        password = ProtectedPassword(password)
        api = API.Global()
        return api.convertExternalVm(uri, username, password, vminfo, jobid)

    def convertExternalVmFromOva(self, ova_path, vminfo, jobid):
        api = API.Global()
        return api.convertExternalVmFromOva(ova_path, vminfo, jobid)

    def getConvertedVm(self, jobid):
        api = API.Global()
        return api.getConvertedVm(jobid)

    def abortV2VJob(self, jobid):
        api = API.Global()
        return api.abortV2VJob(jobid)

    def deleteV2VJob(self, jobid):
        api = API.Global()
        return api.deleteV2VJob(jobid)

    def registerSecrets(self, secrets, clear=False):
        secrets = protect_passwords(secrets)
        api = API.Global()
        return api.registerSecrets(secrets, clear=clear)

    def unregisterSecrets(self, uuids):
        api = API.Global()
        return api.unregisterSecrets(uuids)

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
        password = ProtectedPassword(password)
        vm = API.VM(vmId)
        return vm.setTicket(password, ttl, existingConnAction, params)

    def vmChangeCD(self, vmId, driveSpec):
        vm = API.VM(vmId)
        return vm.changeCD(driveSpec)

    def vmChangeFloppy(self, vmId, driveSpec):
        vm = API.VM(vmId)
        return vm.changeFloppy(driveSpec)

    def vmMigrate(self, params):
        vm = API.VM(params['vmId'])
        return vm.migrate(params)

    def vmGetMigrationStatus(self, vmId):
        vm = API.VM(vmId)
        return vm.getMigrationStatus()

    def vmMigrationCancel(self, vmId):
        vm = API.VM(vmId)
        return vm.migrateCancel()

    def vmMigrateChangeParams(self, vmId, params):
        vm = API.VM(vmId)
        return vm.migrateChangeParams(params)

    def vmHotplugDisk(self, params):
        vm = API.VM(params['vmId'])
        return vm.hotplugDisk(params)

    def vmHotunplugDisk(self, params):
        vm = API.VM(params['vmId'])
        return vm.hotunplugDisk(params)

    def vmHotplugNic(self, params):
        vm = API.VM(params['vmId'])
        return vm.hotplugNic(params)

    def vmHostdevHotplug(self, vmId, devices):
        vm = API.VM(vmId)
        return vm.hostdevHotplug(devices)

    def vmHostdevHotunplug(self, vmId, devices):
        vm = API.VM(vmId)
        return vm.hostdevHotunplug(devices)

    def vmHotunplugNic(self, params):
        vm = API.VM(params['vmId'])
        return vm.hotunplugNic(params)

    def vmUpdateDevice(self, vmId, params):
        params = protect_passwords(params)
        vm = API.VM(vmId)
        return vm.updateDevice(params)

    def vmHotplugMemory(self, params):
        vm = API.VM(params['vmId'])
        return vm.hotplugMemory(params)

    def vmSetNumberOfCpus(self, vmId, numberOfCpus):
        vm = API.VM(vmId)
        return vm.setNumberOfCpus(numberOfCpus)

    def vmUpdateVmPolicy(self, params):
        vm = API.VM(params['vmId'])
        return vm.updateVmPolicy(params)

    def vmFreeze(self, vmId):
        vm = API.VM(vmId)
        return vm.freeze()

    def vmThaw(self, vmId):
        vm = API.VM(vmId)
        return vm.thaw()

    def vmSnapshot(self, vmId, snapDrives, snapMemVolHandle='', frozen=False):
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
        return vm.snapshot(snapDrives, snapMemVolHandle, frozen=frozen)

    def merge(self, vmId, drive, baseVolUUID, topVolUUID, bandwidth=0,
              jobUUID=None):
        vm = API.VM(vmId)
        return vm.merge(drive, baseVolUUID, topVolUUID, bandwidth, jobUUID)

    def vmSetBalloonTarget(self, vmId, target):
        vm = API.VM(vmId)
        return vm.setBalloonTarget(target)

    def getCapabilities(self):
        api = API.Global()
        ret = api.getCapabilities()
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

    def getAllVmIoTunePolicies(self):
        api = API.Global()
        return api.getAllVmIoTunePolicies()

    def hostdevListByCaps(self, caps=None):
        api = API.Global()
        return api.hostdevListByCaps(caps)

    def hostdevChangeNumvfs(self, device_name, numvfs):
        api = API.Global()
        return api.hostdevChangeNumvfs(device_name, numvfs)

    def hostdevReattach(self, device_name):
        api = API.Global()
        return api.hostdevReattach(device_name)

    def vmGetIoTunePolicy(self, vmId):
        vm = API.VM(vmId)
        return vm.getIoTunePolicy()

    def vmGetIoTune(self, vmId):
        vm = API.VM(vmId)
        return vm.getIoTune()

    def vmSetIoTune(self, vmId, tunables):
        vm = API.VM(vmId)
        return vm.setIoTune(tunables)

    def vmSetCpuTuneQuota(self, vmId, quota):
        vm = API.VM(vmId)
        return vm.setCpuTuneQuota(quota)

    def vmSetCpuTunePeriod(self, vmId, period):
        vm = API.VM(vmId)
        return vm.setCpuTunePeriod(period)

    def vmMigrationCreate(self, params, incomingLimit=None):
        vm = API.VM(params['vmId'])
        return vm.migrationCreate(params, incomingLimit)

    def vmDesktopLogin(self, vmId, domain, user, password):
        password = ProtectedPassword(password)
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
        api = API.VM(vmId)
        return api.diskSizeExtend(driveSpecs, newSize)

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
                  secure=False, options='', policy=None):
        password = ProtectedPassword(password)
        api = API.Global()
        return api.fenceNode(addr, port, agent, username, password, action,
                             secure, options, policy)

    def setLogLevel(self, level, name=''):
        api = API.Global()
        return api.setLogLevel(level, name)

    def setMOMPolicy(self, policy):
        api = API.Global()
        return api.setMOMPolicy(policy)

    def setMOMPolicyParameters(self, key_value_store):
        api = API.Global()
        return api.setMOMPolicyParameters(key_value_store)

    def setKsmTune(self, tuningParams):
        api = API.Global()
        return api.setKsmTune(tuningParams)

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

    def resizePV(self, sdUUID, spUUID, guid, options=None):
        domain = API.StorageDomain(sdUUID)
        return domain.resizePV(spUUID, guid)

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

    def imageSparsify(self, spUUID, sdUUID, imgUUID, tmpVolUUID, dstSdUUID,
                      dstImgUUID, dstVolUUID):
        image = API.Image(imgUUID, spUUID, sdUUID)
        return image.sparsify(tmpVolUUID, dstSdUUID, dstImgUUID, dstVolUUID)

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

    def imagePrepare(self, spUUID, sdUUID, imgUUID, volUUID,
                     allowIllegal=False):
        image = API.Image(imgUUID, spUUID, sdUUID)
        return image.prepare(volUUID, allowIllegal)

    def imageTeardown(self, spUUID, sdUUID, imgUUID, volUUID=None):
        image = API.Image(imgUUID, spUUID, sdUUID)
        return image.teardown(volUUID)

    def imageReconcileVolumeChain(self, spUUID, sdUUID, imgUUID, leafUUID):
        image = API.Image(imgUUID, spUUID, sdUUID)
        return image.reconcileVolumeChain(leafUUID)

    def poolConnect(self, spUUID, hostID, scsiKey, msdUUID, masterVersion,
                    domainsMap=None, options=None):
        pool = API.StoragePool(spUUID)
        return pool.connect(hostID, scsiKey, msdUUID, masterVersion,
                            domainsMap)

    def poolConnectStorageServer(self, domType, spUUID, conList, options=None):
        conList = protect_passwords(conList)
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
        conList = protect_passwords(conList)
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

    def poolGetDomainsContainingImage(self, spUUID, imgUUID, options=None):
        pool = API.StoragePool(spUUID)
        return pool.getDomainsContainingImage(imgUUID)

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
                     srcVolUUID=API.Volume.BLANK_UUID, initialSize=None):
        volume = API.Volume(volUUID, spUUID, sdUUID, imgUUID)
        return volume.create(size, volFormat, preallocate, diskType, desc,
                             srcImgUUID, srcVolUUID, initialSize=initialSize)

    def volumeExtendSize(self, spUUID, sdUUID, imgUUID, volUUID, newSize):
        volume = API.Volume(volUUID, spUUID, sdUUID, imgUUID)
        return volume.extendSize(newSize)

    def volumeGetInfo(self, sdUUID, spUUID, imgUUID, volUUID):
        volume = API.Volume(volUUID, spUUID, sdUUID, imgUUID)
        return volume.getInfo()

    def volumeGetSize(self, sdUUID, spUUID, imgUUID, volUUID):
        volume = API.Volume(volUUID, spUUID, sdUUID, imgUUID)
        return volume.getSize()

    def volumeSetSize(self, sdUUID, spUUID, imgUUID, volUUID, newSize):
        volume = API.Volume(volUUID, spUUID, sdUUID, imgUUID)
        return volume.setSize(newSize)

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
        con = protect_passwords(con)
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

    def devicesGetList(self, storageType=None, guids=(),
                       checkStatus=True, options=None):
        api = API.Global()
        res = api.getDeviceList(storageType, guids, checkStatus)
        return unprotect_passwords(res)

    def devicesGetVisibility(self, guids, options=None):
        api = API.Global()
        return api.getDevicesVisibility(guids)

    def sdm_create_volume(self, job_id, vol_info):
        sdm = API.SDM()

        # As a workaround for the 32bit signed integer limitation of xmlrpc,
        # allow large integers to be passed as strings.  We convert them back
        # to the correct type here.
        for param in 'virtual_size', 'initial_size':
            if param in vol_info:
                vol_info[param] = int(vol_info[param])

        return sdm.create_volume(job_id, vol_info)

    def getGlobalMethods(self):
        return ((self.vmDestroy, 'destroy'),
                (self.vmCreate, 'create'),
                (self.getVMList, 'list'),
                (self.vmPause, 'pause'),
                (self.vmCont, 'cont'),
                (self.vmFreeze, 'freeze'),
                (self.vmThaw, 'thaw'),
                (self.vmSnapshot, 'snapshot'),
                (self.vmSetBalloonTarget, 'setBalloonTarget'),
                (self.vmReset, 'reset'),
                (self.vmShutdown, 'shutdown'),
                (self.vmSetTicket, 'setVmTicket'),
                (self.vmChangeCD, 'changeCD'),
                (self.vmChangeFloppy, 'changeFloppy'),
                (self.vmMigrate, 'migrate'),
                (self.vmGetMigrationStatus, 'migrateStatus'),
                (self.vmMigrationCancel, 'migrateCancel'),
                (self.vmMigrateChangeParams, 'migrateChangeParams'),
                (self.getCapabilities, 'getVdsCapabilities'),
                (self.getHardwareInfo, 'getVdsHardwareInfo'),
                (self.diskGetAlignment, 'getDiskAlignment'),
                (self.getStats, 'getVdsStats'),
                (self.vmGetStats, 'getVmStats'),
                (self.getAllVmStats, 'getAllVmStats'),
                (self.getAllVmIoTunePolicies, 'getAllVmIoTunePolicies'),
                (self.hostdevListByCaps, 'hostdevListByCaps'),
                (self.hostdevChangeNumvfs, 'hostdevChangeNumvfs'),
                (self.hostdevReattach, 'hostdevReattach'),
                (self.vmMigrationCreate, 'migrationCreate'),
                (self.vmDesktopLogin, 'desktopLogin'),
                (self.vmDesktopLogoff, 'desktopLogoff'),
                (self.vmDesktopLock, 'desktopLock'),
                (self.vmDesktopSendHcCommand, 'sendHcCmdToDesktop'),
                (self.vmHibernate, 'hibernate'),
                (self.vmDiskReplicateStart, 'diskReplicateStart'),
                (self.vmDiskReplicateFinish, 'diskReplicateFinish'),
                (self.diskSizeExtend, 'diskSizeExtend'),
                (self.setupNetworks, 'setupNetworks'),
                (self.ping, 'ping'),
                (self.setSafeNetworkConfig, 'setSafeNetworkConfig'),
                (self.fenceNode, 'fenceNode'),
                (self.stop, 'prepareForShutdown'),
                (self.setLogLevel, 'setLogLevel'),
                (self.setMOMPolicy, 'setMOMPolicy'),
                (self.setMOMPolicyParameters, 'setMOMPolicyParameters'),
                (self.setKsmTune, 'setKsmTune'),
                (self.setHaMaintenanceMode, 'setHaMaintenanceMode'),
                (self.vmHotplugDisk, 'hotplugDisk'),
                (self.vmHotunplugDisk, 'hotunplugDisk'),
                (self.vmHotplugNic, 'hotplugNic'),
                (self.vmHotunplugNic, 'hotunplugNic'),
                (self.vmHostdevHotplug, 'hostdevHotplug'),
                (self.vmHostdevHotunplug, 'hostdevHotunplug'),
                (self.vmUpdateDevice, 'vmUpdateDevice'),
                (self.vmSetNumberOfCpus, 'setNumberOfCpus'),
                (self.vmHotplugMemory, 'hotplugMemory'),
                (self.merge, 'merge'),
                (self.vmUpdateVmPolicy, 'updateVmPolicy'),
                (self.vmSetIoTune, 'setIoTune'),
                (self.vmGetIoTune, 'getIoTune'),
                (self.vmGetIoTunePolicy, 'getIoTunePolicy'),
                (self.vmSetCpuTuneQuota, 'vmSetCpuTuneQuota'),
                (self.vmSetCpuTunePeriod, 'vmSetCpuTunePeriod'),
                (self.hostGetJobs, 'getHostJobs'),
                (self.getExternalVMs, 'getExternalVMs'),
                (self.getExternalVMNames, 'getExternalVMNames'),
                (self.getExternalVmFromOva, 'getExternalVmFromOva'),
                (self.convertExternalVm, 'convertExternalVm'),
                (self.convertExternalVmFromOva, 'convertExternalVmFromOva'),
                (self.getConvertedVm, 'getConvertedVm'),
                (self.abortV2VJob, 'abortV2VJob'),
                (self.deleteV2VJob, 'deleteV2VJob'),
                (self.registerSecrets, 'registerSecrets'),
                (self.unregisterSecrets, 'unregisterSecrets'))

    def getIrsMethods(self):
        return ((self.domainActivate, 'activateStorageDomain'),
                (self.domainAttach, 'attachStorageDomain'),
                (self.domainCreate, 'createStorageDomain'),
                (self.domainDeactivate, 'deactivateStorageDomain'),
                (self.domainDetach, 'detachStorageDomain'),
                (self.domainDetachForced, 'forcedDetachStorageDomain'),
                (self.domainExtend, 'extendStorageDomain'),
                (self.resizePV, 'resizePV'),
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
                (self.imageSparsify, 'sparsifyImage'),
                (self.imageCloneStructure, 'cloneImageStructure'),
                (self.imageSyncData, 'syncImageData'),
                (self.imageUpload, 'uploadImage'),
                (self.imageDownload, 'downloadImage'),
                (self.imagePrepare, 'prepareImage'),
                (self.imageTeardown, 'teardownImage'),
                (self.imageReconcileVolumeChain, 'reconcileVolumeChain'),
                (self.poolConnect, 'connectStoragePool'),
                (self.poolConnectStorageServer, 'connectStorageServer'),
                (self.poolCreate, 'createStoragePool'),
                (self.poolDestroy, 'destroyStoragePool'),
                (self.poolDisconnect, 'disconnectStoragePool'),
                (self.poolDisconnectStorageServer, 'disconnectStorageServer'),
                (self.poolFenceSPMStorage, 'fenceSpmStorage'),
                (self.poolGetBackedUpVmsInfo, 'getVmsInfo'),
                (self.poolGetBackedUpVmsList, 'getVmsList'),
                (self.poolGetDomainsContainingImage, 'getImageDomainsList'),
                (self.poolGetSpmStatus, 'getSpmStatus'),
                (self.poolGetInfo, 'getStoragePoolInfo'),
                (self.poolMoveMultipleImages, 'moveMultipleImages'),
                (self.poolReconstructMaster, 'reconstructMaster'),
                (self.poolSetDescription, 'setStoragePoolDescription'),
                (self.poolSpmStart, 'spmStart'),
                (self.poolSpmStop, 'spmStop'),
                (self.poolUpgrade, 'upgradeStoragePool'),
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
                (self.volumeGetSize, 'getVolumeSize'),
                (self.volumeSetSize, 'volumeSetSize'),
                (self.volumeRefresh, 'refreshVolume'),
                (self.volumeSetDescription, 'setVolumeDescription'),
                (self.volumeSetLegality, 'setVolumeLegality'),
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
                (self.sdm_create_volume, 'sdm_create_volume'))


def _wrap_api_method(f, cif):
    def wrapper(*args, **kwargs):
        start_time = utils.monotonic_time()
        res = {}
        try:
            logLevel = logging.DEBUG
            suppress_args = f.__name__ in ('fenceNode',)

            # TODO: This password protection code is fragile and ugly. Password
            # protection should be done in the wrapped methods, and logging
            # shold be done in the next layers, similar to storage logs.

            displayArgs = args
            if suppress_args:
                displayArgs = '(suppressed)'
            elif f.__name__ == 'vmDesktopLogin':
                if 'password' in kwargs:
                    raise TypeError("Got an unexpected keyword argument: "
                                    "'password'")
                if len(args) > 3:
                    displayArgs = args[:3] + ('****',) + args[4:]
            elif f.__name__ == 'getExternalVMs':
                if len(args) >= 3:
                    displayArgs = args[:2] + ('****',) + args[3:]
            elif f.__name__ == 'getExternalVMNames':
                if len(args) == 3:
                    displayArgs = args[:2] + ('****',)
            elif f.__name__ == 'convertExternalVm':
                if len(args) > 3:
                    displayArgs = args[:2] + ('****',) + args[3:]
            elif f.__name__ == 'registerSecrets':
                secrets = protect_passwords(utils.picklecopy(args[0]))
                displayArgs = (secrets,) + args[1:]
            elif f.__name__ == 'vmUpdateDevice':
                if len(args) >= 2 and args[1].get(
                   'deviceType', '') == 'graphics':
                    params = protect_passwords(utils.picklecopy(args[1]))
                    displayArgs = (args[0],) + (params,) + args[2:]

            # Logging current call
            logStr = 'client [%s]::call %s with %s %s' % \
                (getattr(cif.threadLocal, 'client', ''),
                 f.__name__, displayArgs, kwargs)

            # if flowID exists
            if getattr(cif.threadLocal, 'flowID', None) is not None:
                logStr += " flowID [%s]" % cif.threadLocal.flowID

            # Ready to show the log into vdsm.log
            cif.log.log(logLevel, logStr)

            if cif.ready:
                res = f(*args, **kwargs)
            else:
                res = errCode['recovery']

            cif.log.log(logLevel, 'return %s with %s', f.__name__, res)

            # Ugly hack, but this code is going to be deleted soon.
            if isinstance(res.get('statsList'), Suppressed):
                res['statsList'] = res['statsList'].value

            return res
        except libvirt.libvirtError as e:
            cif.log.error("libvirt error", exc_info=True)
            if e.get_error_code() == libvirt.VIR_ERR_NO_DOMAIN:
                res = errCode['noVM']
            else:
                res = errCode['unexpected']
            return res
        except VdsmException as e:
            cif.log.error("vdsm exception occured", exc_info=True)
            res = e.response()
            return res
        except:
            cif.log.error("unexpected error", exc_info=True)
            res = errCode['unexpected']
            return res
        finally:
            cif.log.info("RPC call %s finished (code=%s) in "
                         "%.2f seconds",
                         f.__name__,
                         res.get('status', {}).get('code'),
                         utils.monotonic_time() - start_time)
    wrapper.__name__ = f.__name__
    wrapper.__doc__ = f.__doc__
    return wrapper


class XmlDetector():
    log = logging.getLogger("XmlDetector")
    NAME = "xml"
    REQUIRED_SIZE = 6

    def __init__(self, xml_binding):
        self.xml_binding = xml_binding

    def detect(self, data):
        return (data.startswith("PUT /") or data.startswith("GET /") or
                data.startswith("POST /"))

    def handle_socket(self, client_socket, socket_address):
        self.xml_binding.add_socket(client_socket, socket_address)
        self.log.debug("xml over http detected from %s", socket_address)
