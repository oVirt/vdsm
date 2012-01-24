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
from define import errCode

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

    def getGlobalMethods(self):
        return ((self.cif.destroy, 'destroy'),
                (self.cif.create, 'create'),
                (self.cif.list, 'list'),
                (self.cif.pause, 'pause'),
                (self.cif.cont, 'cont'),
                (self.cif.snapshot, 'snapshot'),
                (self.cif.sysReset, 'reset'),
                (self.cif.shutdown, 'shutdown'),
                (self.cif.setVmTicket, 'setVmTicket'),
                (self.cif.changeCD, 'changeCD'),
                (self.cif.changeFloppy, 'changeFloppy'),
                (self.cif.sendkeys, 'sendkeys')    ,
                (self.cif.migrate, 'migrate'),
                (self.cif.migrateStatus, 'migrateStatus'),
                (self.cif.migrateCancel, 'migrateCancel'),
                (self.cif.getVdsCapabilities, 'getVdsCapabilities'),
                (self.cif.getVdsStats, 'getVdsStats'),
                (self.cif.getVmStats, 'getVmStats'),
                (self.cif.getAllVmStats, 'getAllVmStats'),
                (self.cif.migrationCreate, 'migrationCreate'),
                (self.cif.desktopLogin, 'desktopLogin'),
                (self.cif.desktopLogoff, 'desktopLogoff'),
                (self.cif.desktopLock, 'desktopLock'),
                (self.cif.sendHcCmdToDesktop, 'sendHcCmdToDesktop'),
                (self.cif.hibernate, 'hibernate'),
                (self.cif.monitorCommand, 'monitorCommand'),
                (self.cif.addNetwork, 'addNetwork'),
                (self.cif.delNetwork, 'delNetwork'),
                (self.cif.editNetwork, 'editNetwork'),
                (self.cif.setupNetworks, 'setupNetworks'),
                (self.cif.ping, 'ping'),
                (self.cif.setSafeNetworkConfig, 'setSafeNetworkConfig'),
                (self.cif.fenceNode, 'fenceNode'),
                (self.cif.prepareForShutdown, 'prepareForShutdown'),
                (self.cif.setLogLevel, 'setLogLevel'),
                (self.cif.hotplugDisk, 'hotplugDisk'),
                (self.cif.hotunplugDisk, 'hotunplugDisk'))

    def getIrsMethods(self):
        if not self.cif.irs:
            return None
        methodList = []
        for name in dir(self.cif.irs):
            method = getattr(self.cif.irs, name)
            if callable(method) and name[0] != '_':
                methodList.append((method, name))
        return methodList

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
