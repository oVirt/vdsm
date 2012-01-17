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
import signal
from errno import EINTR
import threading
import logging
import subprocess
import pickle
import copy
import SimpleXMLRPCServer
from xml.dom import minidom
import uuid

from storage.dispatcher import Dispatcher
from storage.hsm import HSM
import storage.misc
import storage.hba
from config import config
import ksm
import netinfo
import SecureXMLRPCServer
from define import doneCode, errCode, Kbytes, Mbytes
import libvirt
import libvirtconnection
import vm
import libvirtvm
import constants
import utils
import configNetwork
import caps

import supervdsm

# default message for system shutdown, will be displayed in guest
USER_SHUTDOWN_MESSAGE = 'System going down'

PAGE_SIZE_BYTES = os.sysconf('SC_PAGESIZE')

DEFAULT_BRIDGE = config.get("vars", "default_bridge")

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
            f.im_self.log.log(logLevel, '[%s]::call %s with %s %s',
                              getattr(f.im_self.threadLocal, 'client', ''),
                              f.__name__, displayArgs, kwargs)
            if f.im_self._recovery:
                res = errCode['recovery']
            else:
                res = f(*args, **kwargs)
            f.im_self.log.log(logLevel, 'return %s with %s', f.__name__, res)
            return res
        except libvirt.libvirtError, e:
            f.im_self.log.error(traceback.format_exc())
            if e.get_error_code() == libvirt.VIR_ERR_NO_DOMAIN:
                return errCode['noVM']
            else:
                return errCode['unexpected']
        except:
            f.im_self.log.error(traceback.format_exc())
            return errCode['unexpected']
    wrapper.__name__ = f.__name__
    wrapper.__doc__ = f.__doc__
    return wrapper

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
        self.serverPort = config.get('addresses', 'management_port')
        self.serverIP = self._getServerIP()
        self.server = self._createXMLRPCServer()
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

    def _getServerIP(self):
        """Return the IP address we should listen on"""

        addr = config.get('addresses', 'management_ip')
        if addr:
            return addr
        try:
            addr = netinfo.ifconfig()[DEFAULT_BRIDGE]['addr']
        except:
            pass
        return addr

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
            # stop listening ASAP
            self.server.server_close()
            self._enabled = False
            self._hostStats.stop()
            if self.irs:
                return self.irs.prepareForShutdown()
            else:
                return {'status': doneCode}
        finally:
            self._shutdownSemaphore.release()


    def setLogLevel(self, level):
        """
        Set verbosity level of vdsm's log.

        params
            level: requested logging level. `logging.DEBUG` `logging.ERROR`

        Doesn't survive a restart
        """
        logging.getLogger('clientIF.setLogLevel').info('Setting loglevel to %s' % level)
        handlers = logging.getLogger().handlers
        [fileHandler] = [h for h in handlers if isinstance(h, logging.FileHandler)]
        fileHandler.setLevel(int(level))

        return dict(status=doneCode)

    def getKeyCertFilenames(self):
        """
        Get the locations of key and certificate files.
        """
        tsPath = config.get('vars', 'trust_store_path')
        KEYFILE = tsPath + '/keys/vdsmkey.pem'
        CERTFILE = tsPath + '/certs/vdsmcert.pem'
        CACERT = tsPath + '/certs/cacert.pem'
        return KEYFILE, CERTFILE, CACERT

    def _createXMLRPCServer(self):
        """
        Create xml-rpc server over http or https.
        """
        cif = self
        class LoggingMixIn:
            def log_request(self, code='-', size='-'):
                """Track from where client connections are coming."""
                self.server.lastClient = self.client_address[0]
                self.server.lastClientTime = time.time()
                file(constants.P_VDSM_CLIENT_LOG, 'w')

        server_address = (self.serverIP, int(self.serverPort))
        if config.getboolean('vars', 'ssl'):
            class LoggingHandler(LoggingMixIn, SecureXMLRPCServer.SecureXMLRPCRequestHandler):
                def setup(self):
                    cif.threadLocal.client = self.client_address[0]
                    return SecureXMLRPCServer.SecureXMLRPCRequestHandler.setup(self)
            KEYFILE, CERTFILE, CACERT = self.getKeyCertFilenames()
            s = SecureXMLRPCServer.SecureThreadedXMLRPCServer(server_address,
                        keyfile=KEYFILE, certfile=CERTFILE, ca_certs=CACERT,
                        timeout=config.getint('vars',
                                              'vds_responsiveness_timeout'),
                        requestHandler=LoggingHandler)
        else:
            class LoggingHandler(LoggingMixIn, SimpleXMLRPCServer.SimpleXMLRPCRequestHandler):
                def setup(self):
                    cif.threadLocal.client = self.client_address[0]
                    return SimpleXMLRPCServer.SimpleXMLRPCRequestHandler.setup(self)
            s = utils.SimpleThreadedXMLRPCServer(server_address,
                        requestHandler=LoggingHandler, logRequests=True)
        utils.closeOnExec(s.socket.fileno())

        return s

    def _initIRS(self):
        def wrapIrsMethod(f):
            def wrapper(*args, **kwargs):
                if self.threadLocal.client:
                    f.im_self.log.debug('[%s]', self.threadLocal.client)
                return f(*args, **kwargs)
            wrapper.__name__ = f.__name__
            wrapper.__doc__ = f.__doc__
            return wrapper
        self.irs = None
        if config.getboolean('irs', 'irs_enable'):
            try:
                self.irs = Dispatcher(HSM())
                for name in dir(self.irs):
                    method = getattr(self.irs, name)
                    if callable(method) and name[0] != '_':
                        self.server.register_function(wrapIrsMethod(method), name)
            except:
                self.log.error(traceback.format_exc())
        if not self.irs:
            err = errCode['recovery'].copy()
            err['status'] = err['status'].copy()
            err['status']['message'] = 'Failed to initialize storage'
            self.server._dispatch = lambda method, params: err


    def _registerFunctions(self):
        self.server.register_introspection_functions()
        for method, name in (
                (self.destroy, 'destroy'),
                (self.create, 'create'),
                (self.list, 'list'),
                (self.pause, 'pause'),
                (self.cont, 'cont'),
                (self.sysReset, 'reset'),
                (self.shutdown, 'shutdown'),
                (self.setVmTicket, 'setVmTicket'),
                (self.changeCD, 'changeCD'),
                (self.changeFloppy, 'changeFloppy'),
                (self.sendkeys, 'sendkeys')    ,
                (self.migrate, 'migrate'),
                (self.migrateStatus, 'migrateStatus'),
                (self.migrateCancel, 'migrateCancel'),
                (self.getVdsCapabilities, 'getVdsCapabilities'),
                (self.getVdsStats, 'getVdsStats'),
                (self.getVmStats, 'getVmStats'),
                (self.getAllVmStats, 'getAllVmStats'),
                (self.migrationCreate, 'migrationCreate'),
                (self.desktopLogin, 'desktopLogin'),
                (self.desktopLogoff, 'desktopLogoff'),
                (self.desktopLock, 'desktopLock'),
                (self.sendHcCmdToDesktop, 'sendHcCmdToDesktop'),
                (self.hibernate, 'hibernate'),
                (self.monitorCommand, 'monitorCommand'),
                (self.addNetwork, 'addNetwork'),
                (self.delNetwork, 'delNetwork'),
                (self.editNetwork, 'editNetwork'),
                (self.setupNetworks, 'setupNetworks'),
                (self.ping, 'ping'),
                (self.setSafeNetworkConfig, 'setSafeNetworkConfig'),
                (self.fenceNode, 'fenceNode'),
                (self.prepareForShutdown, 'prepareForShutdown'),
                (self.setLogLevel, 'setLogLevel'),
                        ):
           self.server.register_function(wrapApiMethod(method), name)


    def serve(self):
        """
        Register xml-rpc functions and serve clients until stopped
        """

        try:
            self._registerFunctions()
            self.server.timeout = 1
        except:
            if self.irs:
                self.irs.prepareForShutdown()
            raise

        while self._enabled:
            try:
                self.server.handle_request()
            except Exception, e:
                if e[0] != EINTR:
                    self.log.error("xml-rpc handler exception", exc_info=True)

    #Global services

    def sendkeys(self, vmId, keySeq):
        """
        Send a string of keys to a guest's keyboard (OBSOLETE)

        Used only by QA and might be discontinued in next version.
        """
        return errCode['noimpl']

    def hibernate(self, vmId, hiberVolHandle=None):
        """
        Hibernate a VM.

        :param hiberVolHandle: opaque string, indicating the location of
                               hibernation images.
        """
        params = {'vmId': vmId, 'mode': 'file',
                  'hiberVolHandle': hiberVolHandle}
        response = self.migrate(params)
        if not response['status']['code']:
            response['status']['message'] = 'Hibernation process starting'
        return response

    def migrate(self, params):
        """
        Migrate a VM to a remote host.

        :param params: a dictionary containing:
            *dst* - remote host or hibernation image filname
            *dstparams* - hibernation image filname for vdsm parameters
            *mode* - ``remote``/``file``
            *method* - ``online``
            *downtime* - allowed down time during online migration
        """
        self.log.debug(params)
        try:
            vmId = params['vmId']
            vm = self.vmContainer[vmId]
        except KeyError:
            return errCode['noVM']

        vmParams = vm.status()
        if vmParams['status'] in ('WaitForLaunch', 'Down'):
            return errCode['noVM']
        if params.get('mode') == 'file':
            if 'dst' not in params:
                params['dst'], params['dstparams'] = \
                    self._getHibernationPaths(params['hiberVolHandle'])
        else:
            params['mode'] = 'remote'
        return vm.migrate(params)

    def migrateStatus(self, vmId):
        """
        Report status of a currently outgoing migration.
        """
        try:
            vm = self.vmContainer[vmId]
        except KeyError:
            return errCode['noVM']
        return vm.migrateStatus()

    def migrateCancel(self, vmId):
        """
        Cancel a currently outgoing migration process.
        """
        try:
            vm = self.vmContainer[vmId]
        except KeyError:
            return errCode['noVM']
        return vm.migrateCancel()

    def monitorCommand(self, vmId, cmd):
        """
        Send a monitor command to the specified VM and wait for the answer.

        :param vmId: uuid of the specified VM
        :type vmId: UUID
        :param command: a single monitor command (without terminating newline)
        :type command: string
        """
        return errCode['noimpl']

    def shutdown(self, vmId, timeout=None, message=None):
        """
        Shut a VM down politely.

        :param message: message to be shown to guest user before shutting down
                        his machine.
        :param timeout: grace period (seconds) to let guest user close his
                        applications.
        """
        try:
            vm = self.vmContainer[vmId]
        except KeyError:
            return errCode['noVM']
        if not timeout:
            timeout = config.get('vars', 'user_shutdown_timeout')
        if not message:
            message = USER_SHUTDOWN_MESSAGE
        return vm.shutdown(timeout, message)

    def setVmTicket(self, vmId, otp, seconds, connAct='disconnect'):
        """
        Set the ticket (password) to be used to connect to a VM display

        :param vmId: specify the VM whos ticket is to be changed.
        :param otp: new password
        :type otp: string
        :param seconds: ticket lifetime (seconds)
        :param connAct: what to do with a currently-connected client (SPICE only):
                ``disconnect`` - disconnect old client when a new client
                                 connects.
        """
        try:
            vm = self.vmContainer[vmId]
        except KeyError:
            return errCode['noVM']
        return vm.setTicket(otp, seconds, connAct)

    def sysReset(self, vmId):
        """
        Press the virtual reset button for the specified VM.
        """
        return errCode['noimpl']

    def destroy(self, vmId):
        """
        Destroy the specified VM.
        """
        self.vmContainerLock.acquire()
        self.log.info("vmContainerLock acquired by vm %s", vmId)
        try:
            v = self.vmContainer.get(vmId)
            if not v:
                return errCode['noVM']
            res = v.destroy()
            status = copy.deepcopy(res)
            if status['status']['code'] == 0:
                status['status']['message'] = "Machine destroyed"
            return status
        finally:
            self.vmContainerLock.release()

    def pause(self, vmId):
        v = self.vmContainer.get(vmId)
        if not v:
            return errCode['noVM']
        return v.pause()

    def cont(self, vmId):
        v = self.vmContainer.get(vmId)
        if not v:
            return errCode['noVM']
        return v.cont()

    def changeCD(self, vmId, path):
        """
        Change the CD in the specified VM.

        :param vmId: uuid of specific VM.
        :type vmId: UUID
        :param path: specfication of the new CD image. Either an image
                path or a `storage`-centric quartet.
        """
        vm = self.vmContainer.get(vmId)
        if not vm:
            return errCode['noVM']
        return vm.changeCD(path)

    def changeFloppy(self, vmId, path):
        """
        Change the floppy disk in the specified VM.

        :param vmId: uuid of specific VM.
        :type vmId: UUID
        :param path: specfication of the new floppy image. Either an image
                path or a `storage`-centric quartet.
        """
        vm = self.vmContainer.get(vmId)
        if not vm:
            return errCode['noVM']
        return vm.changeFloppy(path)

    def _createSysprepFloppyFromInf(self, infFileBinary, floppyImage):
        try:
            rc, out, err = utils.execCmd([constants.EXT_MK_SYSPREP_FLOPPY,
                                         floppyImage],
                                        sudo=True, data=infFileBinary.data)
            if rc:
                return False
            else:
                return True
        except:
            self.log.error(traceback.format_exc())
            return False

    def _getNetworkIp(self, bridge):
        try:
            ip = netinfo.ifconfig()[bridge]['addr']
        except:
            ip = config.get('addresses', 'guests_gateway_ip')
            if ip == '':
                ip = '0'
            self.log.info('network %s: using %s', bridge, ip)
        return ip

    def _getHibernationPaths(self, hiberVolHandle):
        """
        Break *hiberVolHandle* into the "quartets" of hibernation images.
        """
        domainID, poolID, stateImageID, stateVolumeID, \
            paramImageID, paramVolumeID = hiberVolHandle.split(',')

        return dict(domainID=domainID, poolID=poolID,
                    imageID=stateImageID, volumeID=stateVolumeID), \
               dict(domainID=domainID, poolID=poolID,
                    imageID=paramImageID, volumeID=paramVolumeID)


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

    def create(self, vmParams):
        """
        Start up a virtual machine.

        :param vmParams: required and optional VM parameters.
        :type vmParams: dict
        """
        try:
            if vmParams.get('vmId') in self.vmContainer:
                self.log.warning('vm %s already exists' % vmParams['vmId'])
                return errCode['exist']

            if 'hiberVolHandle' in vmParams:
                vmParams['restoreState'], paramFilespec = \
                         self._getHibernationPaths(vmParams.pop('hiberVolHandle'))
                try: # restore saved vm parameters
                # NOTE: pickled params override command-line params. this
                # might cause problems if an upgrade took place since the
                # parmas were stored.
                    fname = self.prepareVolumePath(paramFilespec)
                    try:
                        with file(fname) as f:
                            pickledMachineParams = pickle.load(f)

                        if type(pickledMachineParams) == dict:
                            self.log.debug('loaded pickledMachineParams '
                                                   + str(pickledMachineParams))
                            self.log.debug('former conf ' + str(vmParams))
                            vmParams.update(pickledMachineParams)
                    finally:
                        self.teardownVolumePath(paramFilespec)
                except:
                    self.log.error(traceback.format_exc())

            requiredParams = ['vmId', 'memSize', 'display']
            for param in requiredParams:
                if param not in vmParams:
                    self.log.error('Missing required parameter %s' % (param))
                    return {'status': {'code': errCode['MissParam']['status']['code'],
                                       'message': 'Missing required parameter %s' % (param)}}
            try:
                storage.misc.validateUUID(vmParams['vmId'])
            except:
                return {'status': {'code': errCode['MissParam']['status']['code'],
                                   'message': 'vmId must be a valid UUID'}}
            if vmParams['memSize'] == 0:
                return {'status': {'code': errCode['MissParam']['status']['code'],
                                   'message': 'Must specify nonzero memSize'}}

            if vmParams.get('boot') == 'c' and not 'hda' in vmParams \
                                           and not vmParams.get('drives'):
                return {'status': {'code': errCode['MissParam']['status']['code'],
                                   'message': 'missing boot disk'}}

            if 'vmType' not in vmParams:
                vmParams['vmType'] = 'kvm'
            elif vmParams['vmType'] == 'kvm':
                if 'kvmEnable' not in vmParams:
                    vmParams['kvmEnable'] = 'true'

            if 'sysprepInf' in vmParams:
                if not vmParams.get('floppy'):
                    vmParams['floppy'] = '%s%s.vfd' % (constants.P_VDSM_RUN,
                                                vmParams['vmId'])
                vmParams['volatileFloppy'] = True

            if caps.osversion()['name'] == caps.OSName.UNKNOWN:
                return {'status': {'code': errCode['createErr']
                                                  ['status']['code'],
                                   'message': 'Unknown host operating system'}}

            if 'sysprepInf' in vmParams:
                if not self._createSysprepFloppyFromInf(vmParams['sysprepInf'],
                                 vmParams['floppy']):
                    return {'status': {'code': errCode['createErr']
                                                      ['status']['code'],
                                       'message': 'Failed to create '
                                                  'sysprep floppy image. '
                                                  'No space on /tmp?'}}
                    return errCode['createErr']

            if vmParams.get('display') not in ('vnc', 'qxl', 'qxlnc', 'local'):
                return {'status': {'code': errCode['createErr']
                                                  ['status']['code'],
                                   'message': 'Unknown display type %s'
                                                % vmParams.get('display') }}
            if 'nicModel' not in vmParams:
                vmParams['nicModel'] = config.get('vars', 'nic_model')
            vmParams['displayIp'] = self._getNetworkIp(vmParams.get(
                                                        'displayNetwork'))
            self.vmContainerLock.acquire()
            self.log.info("vmContainerLock acquired by vm %s", vmParams['vmId'])
            try:
                if 'recover' not in vmParams:
                    if vmParams['vmId'] in self.vmContainer:
                        self.log.warning('vm %s already exists' % vmParams['vmId'])
                        return errCode['exist']
                vmParams['displayPort'] = '-1' # selected by libvirt
                vmParams['displaySecurePort'] = '-1'
                VmClass = libvirtvm.LibvirtVm
                self.vmContainer[vmParams['vmId']] = VmClass(self, vmParams)
            finally:
                self.vmContainerLock.release()
            self.vmContainer[vmParams['vmId']].run()
            self.log.debug("Total desktops after creation of %s is %d" % (vmParams['vmId'], len(self.vmContainer)))
            return {'status': doneCode, 'vmList': self.vmContainer[vmParams['vmId']].status()}
        except OSError, e:
            self.log.debug(traceback.format_exc())
            return {'status': {'code': errCode['createErr']['status']['code'],
                               'message': 'Failed to create VM. '
                                          'No space on /tmp? ' + e.message}}
        except:
            self.log.debug(traceback.format_exc())
            return errCode['unexpected']

    def list(self, full=False, vms=[]):
        """ return a list of known VMs with full (or partial) config each """
        def reportedStatus(vm, full):
            d = vm.status()
            if full:
                return d
            else:
                return {'vmId': d['vmId'], 'status': d['status']}
        # To improve complexity, convert 'vms' to set(vms)
        vms = set(vms)
        return {'status': doneCode,
                'vmList': [reportedStatus(vm, full) for vm in self.vmContainer.values()
                            if not vms or vm.id in vms]}

    def _getSingleVmStats (self, vmId):
        v = self.vmContainer.get(vmId)
        if not v:
            return None
        stats = v.getStats().copy()
        stats['vmId'] = vmId
        return stats

    def getVmStats(self, vmId):
        """
        Obtain statistics of the specified VM
        """
        response = self._getSingleVmStats(vmId)
        if response:
            return {'status': doneCode, 'statsList': [response]}
        else:
            return errCode['noVM']

    def getAllVmStats(self):
        """
        Get statistics of all running VMs.
        """
        statsList = []
        for vmId in self.vmContainer.keys():
            response = self._getSingleVmStats(vmId)
            if response:
                statsList.append(response)
        return {'status': doneCode, 'statsList': statsList}

    def getVdsCapabilities(self):
        """
        Report host capabilities.
        """
        c = caps.get()

        c['management_ip'] = self.serverIP

        if hasattr(self, 'server') and hasattr(self.server, 'lastClient'):
            c['lastClient'] = self.server.lastClient
            c['lastClientIface'] = caps._getIfaceByIP(self.server.lastClient)

        return {'status': doneCode, 'info': c}

    def getVdsStats(self):
        """
        Report host statistics.
        """
        def _readSwapTotalFree():
            meminfo = utils.readMemInfo()
            return meminfo['SwapTotal'] / 1024, meminfo['SwapFree'] / 1024

        stats = {}
        decStats = self._hostStats.get()
        for var in decStats:
            stats[var] = utils.convertToStr(decStats[var])
        stats['memAvailable'] = self._memAvailable() / Mbytes
        stats['memShared'] = self._memShared() / Mbytes
        stats['memCommitted'] = self._memCommitted() / Mbytes
        stats['swapTotal'], stats['swapFree'] = _readSwapTotalFree()
        stats['vmCount'], stats['vmActive'], stats['vmMigrating'] = self._countVms()
        (tm_year, tm_mon, tm_day, tm_hour, tm_min, tm_sec,
             dummy, dummy, dummy) = time.gmtime(time.time())
        stats['dateTime'] = '%02d-%02d-%02dT%02d:%02d:%02d GMT' % (
                tm_year, tm_mon, tm_day, tm_hour, tm_min, tm_sec)
        stats['ksmState'] = self.ksmMonitor.state
        stats['ksmPages'] = self.ksmMonitor.pages
        stats['ksmCpu'] = self.ksmMonitor.cpuUsage
        stats['netConfigDirty'] = str(self._netConfigDirty)
        stats['generationID'] = self._generationID
        return {'status': doneCode, 'info': stats}

    #Migration only methods
    def migrationCreate (self, params):
        """
        Start a migration-destination VM.

        :param params: parameters of new VM, to be passed to :meth:`~clientIF.create`.
        :type params: dict
        """
        self.log.debug('Migration create')

        response = self.create(params)
        if response['status']['code']:
            self.log.debug('Migration create - Failed')
            return response

        v = self.vmContainer.get(params['vmId'])

        if not v.waitForMigrationDestinationPrepare():
            return errCode['createErr']

        self.log.debug('Destination VM creation succeeded')
        return {'status': doneCode, 'migrationPort': 0, 'params': response['vmList']}

    #SSO
    def desktopLogin (self, vmId, domain, user, password):
        """
        Log into guest operating system using guest agent.
        """
        try:
            vm = self.vmContainer[vmId]
        except KeyError:
            return errCode['noVM']
        vm.guestAgent.desktopLogin(domain, user, password)
        if vm.guestAgent.isResponsive():
            return {'status': doneCode}
        else:
            return errCode['nonresp']

    def desktopLogoff (self, vmId, force):
        """
        Log out of guest operating system using guest agent.
        """
        try:
            vm = self.vmContainer[vmId]
        except KeyError:
            return errCode['noVM']
        vm.guestAgent.desktopLogoff(force)
        if vm.guestAgent.isResponsive():
            return {'status': doneCode}
        else:
            return errCode['nonresp']

    def desktopLock (self, vmId):
        """
        Lock user session in guest operating system using guest agent.
        """
        try:
            vm = self.vmContainer[vmId]
        except KeyError:
            return errCode['noVM']
        vm.guestAgent.desktopLock()
        if vm.guestAgent.isResponsive():
            return {'status': doneCode}
        else:
            return errCode['nonresp']

    def sendHcCmdToDesktop (self, vmId, message):
        """
        Send a command to the guest agent (depricated).
        """
        try:
            vm = self.vmContainer[vmId]
        except KeyError:
            return errCode['noVM']
        vm.guestAgent.sendHcCmdToDesktop(message)
        if vm.guestAgent.isResponsive():
            return {'status': doneCode}
        else:
            return errCode['nonresp']

    # take a rough estimate on how much free mem is available for new vm
    # memTotal = memFree + memCached + mem_used_by_non_qemu + resident  .
    # simply returning (memFree + memCached) is not good enough, as the
    # resident set size of qemu processes may grow - up to  memCommitted.
    # Thus, we deduct the growth potential of qemu processes, which is
    # (memCommitted - resident)
    def _memAvailable(self):
        """
        Return an approximation of available memory for new VMs.
        """
        memCommitted = self._memCommitted()
        resident = 0
        for vm in self.vmContainer.values():
            if vm.conf['pid'] == '0': continue
            try:
                statmfile = file('/proc/' + vm.conf['pid'] + '/statm')
                resident += int(statmfile.read().split()[1])
            except:
                pass
        resident *= PAGE_SIZE_BYTES
        meminfo = utils.readMemInfo()
        freeOrCached = (meminfo['MemFree'] +
                        meminfo['Cached'] + meminfo['Buffers']) * Kbytes
        return freeOrCached + resident - memCommitted - \
                config.getint('vars', 'host_mem_reserve') * Mbytes

    # take a rough estimate on how much memory is shared between VMs
    def _memShared(self):
        """
        Return an approximation of memory shared by VMs thanks to KSM.
        """
        shared = 0
        for vm in self.vmContainer.values():
            if vm.conf['pid'] == '0': continue
            try:
                statmfile = file('/proc/' + vm.conf['pid'] + '/statm')
                shared += int(statmfile.read().split()[2]) * PAGE_SIZE_BYTES
            except:
                pass
        return shared

    def _memCommitted(self):
        """
        Return the amount of memory (Mb) committed for VMs
        """
        committed = 0
        for vm in self.vmContainer.values():
            committed += vm.memCommitted
        return committed

    def _countVms(self):
        count = active = migrating = 0
        for vmId, vm in self.vmContainer.items():
            try:
                count += 1
                status = vm.lastStatus
                if status == 'Up':
                    active += 1
                elif 'Migration' in status:
                    migrating += 1
            except:
                self.log.error(vmId + ': Lost connection to VM')
        return count, active, migrating

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

    def _translateOptionsToNew(self, options):
        _translationMap = {
            'IPADDR': 'ipaddr',
            'NETMASK': 'netmask',
            'GATEWAY': 'gateway',
            'BOOTPROTO': 'bootproto',
            'DELAY': 'delay',
            'ONBOOT': 'onboot',
            'BONDING_OPTS': 'bondingOptions',
        }
        for k,v in options.items():
            if k in _translationMap:
                self.log.warn("options %s is deprecated. Use %s instead"%(k, _translationMap[k]))
                options[_translationMap[k]] = options.pop(k)

    def ping(self):
        "Ping the server. Useful for tests"
        return {'status':doneCode}

    def addNetwork(self, bridge, vlan=None, bond=None, nics=None, options={}):
        """Add a new network to this vds.

        Network topology is bridge--[vlan--][bond--]nics.
        vlan(number) and bond are optional - pass the empty string to discard
        them.  """

        self._translateOptionsToNew(options)
        if not self._networkSemaphore.acquire(blocking=False):
            self.log.warn('concurrent network verb already executing')
            return errCode['unavail']
        try:
            self._netConfigDirty = True
            if vlan:
                options['vlan'] = vlan
            if bond:
                options['bonding'] = bond
            if nics:
                options['nics'] = list(nics)

            try:
                supervdsm.getProxy().addNetwork(bridge, options)
            except configNetwork.ConfigNetworkError, e:
                self.log.error(e.message, exc_info=True)
                return {'status': {'code': e.errCode, 'message': e.message}}
            return {'status': doneCode}
        finally:
            self._networkSemaphore.release()

    def delNetwork(self, bridge, vlan=None, bond=None, nics=None, options={}):
        """Delete a network from this vds."""
        self._translateOptionsToNew(options)

        try:
            if not self._networkSemaphore.acquire(blocking=False):
                self.log.warn('concurrent network verb already executing')
                return errCode['unavail']

            if vlan or bond or nics:
                # Backwards compatibility
                self.log.warn('Specifying vlan, bond or nics to delNetwork is deprecated')
                _netinfo = netinfo.NetInfo()
                try:
                    if bond:
                        configNetwork.validateBondingName(bond)
                    if vlan:
                        configNetwork.validateVlanId(vlan)
                    if nics and bond and set(nics) != set(_netinfo.bondings[bond]["slaves"]):
                            self.log.error('delNetwork: not all nics specified are enslaved (%s != %s)'
                                    % (nics, _netinfo.bondings[bond]["slaves"])
                                )
                            raise configNetwork.ConfigNetworkError(configNetwork.ne.ERR_BAD_NIC, "not all nics are enslaved")
                except configNetwork.ConfigNetworkError, e:
                    self.log.error(e.message, exc_info=True)
                    return {'status': {'code': e.errCode, 'message': e.message}}

            self._netConfigDirty = True

            try:
                supervdsm.getProxy().delNetwork(bridge, options)
            except configNetwork.ConfigNetworkError, e:
                self.log.error(e.message, exc_info=True)
                return {'status': {'code': e.errCode, 'message': e.message}}
            return {'status': doneCode}
        finally:
            self._networkSemaphore.release()

    def editNetwork(self, oldBridge, newBridge, vlan=None, bond=None, nics=None, options={}):
        """Add a new network to this vds, replacing an old one."""

        self._translateOptionsToNew(options)
        if not self._networkSemaphore.acquire(blocking=False):
            self.log.warn('concurrent network verb already executing')
            return errCode['unavail']
        try:
            if vlan:
                options['vlan'] = vlan
            if bond:
                options['bonding'] = bond
            if nics:
                options['nics'] = list(nics)
            self._netConfigDirty = True

            try:
                supervdsm.getProxy().editNetwork(oldBridge, newBridge, options)
            except configNetwork.ConfigNetworkError, e:
                self.log.error(e.message, exc_info=True)
                return {'status': {'code': e.errCode, 'message': e.message}}
            return {'status': doneCode}
        finally:
            self._networkSemaphore.release()

    def setupNetworks(self, networks={}, bondings={}, options={}):
        """Add a new network to this vds, replacing an old one."""

        self._translateOptionsToNew(options)
        if not self._networkSemaphore.acquire(blocking=False):
            self.log.warn('concurrent network verb already executing')
            return errCode['unavail']
        try:
            self._netConfigDirty = True

            try:
                supervdsm.getProxy().setupNetworks(networks, bondings, options)
            except configNetwork.ConfigNetworkError, e:
                self.log.error(e.message, exc_info=True)
                return {'status': {'code': e.errCode, 'message': e.message}}
            return {'status': doneCode}
        finally:
            self._networkSemaphore.release()

    def setSafeNetworkConfig(self):
        """Declare current network configuration as 'safe'"""
        if not self._networkSemaphore.acquire(blocking=False):
            self.log.warn('concurrent network verb already executing')
            return errCode['unavail']
        try:
            self._netConfigDirty = False
            supervdsm.getProxy().setSafeNetworkConfig()
            return {'status': doneCode}
        finally:
            self._networkSemaphore.release()

    def fenceNode(self, addr, port, agent, user, passwd, action,
                  secure=False, options=''):
        """Send a fencing command to a remote node.

           agent is one of (rsa, ilo, drac5, ipmilan, etc)
           action can be one of (status, on, off, reboot)."""

        def waitForPid(p, inp):
            """ Wait until p.pid exits. Kill it if vdsm exists before. """
            try:
                p.stdin.write(inp)
                p.stdin.close()
                while p.poll() is None:
                    if not self._enabled:
                        self.log.debug('killing fence script pid %s', p.pid)
                        os.kill(p.pid, signal.SIGTERM)
                        time.sleep(1)
                        try:
                            # improbable race: p.pid may now belong to another
                            # process
                            os.kill(p.pid, signal.SIGKILL)
                        except:
                            pass
                        return
                    time.sleep(1)
                self.log.debug('rc %s inp %s out %s err %s', p.returncode,
                               hidePasswd(inp),
                               p.stdout.read(), p.stderr.read())
            except:
                self.log.error(traceback.format_exc())

        def hidePasswd(text):
            cleantext = ''
            for line in text.splitlines(True):
                if line.startswith('passwd='):
                    line = 'passwd=XXXX\n'
                cleantext += line
            return cleantext

        self.log.debug('fenceNode(addr=%s,port=%s,agent=%s,user=%s,' +
               'passwd=%s,action=%s,secure=%s,options=%s)', addr, port, agent,
               user, 'XXXX', action, secure, options)

        if action not in ('status', 'on', 'off', 'reboot'):
            raise ValueError('illegal action ' + action)

        script = constants.EXT_FENCE_PREFIX + agent

        try:
            p = subprocess.Popen([script], stdin=subprocess.PIPE,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                close_fds=True)
        except OSError, e:
            if e.errno == os.errno.ENOENT:
                return errCode['fenceAgent']
            raise

        inp = ('agent=fence_%s\nipaddr=%s\nlogin=%s\noption=%s\n' +
                      'passwd=%s\n') % (agent, addr, user, action, passwd)
        if port != '':
            inp += 'port=%s\n' % (port,)
        if utils.tobool(secure):
            inp += 'secure=yes\n'
        inp += options
        if action == 'status':
            out, err = p.communicate(inp)
            self.log.debug('rc %s in %s out %s err %s', p.returncode,
                           hidePasswd(inp), out, err)
            if not 0 <= p.returncode <= 2:
                return {'status': {'code': 1,
                                   'message': out + err}}
            message = doneCode['message']
            if p.returncode == 0:
                power = 'on'
            elif p.returncode == 2:
                power = 'off'
            else:
                power = 'unknown'
                message = out + err
            return {'status': {'code': 0, 'message': message},
                    'power': power}
        threading.Thread(target=waitForPid, args=(p, inp)).start()
        return {'status': doneCode}
