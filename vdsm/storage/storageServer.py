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
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA  02110-1301  USA
#
# Refer to the README and COPYING files for full details of the license
#
from itertools import chain
import errno
import logging
from os.path import normpath, basename, splitext
import os
from threading import RLock, Lock, Event, Thread
import socket
import pickle
import glob
from collections import namedtuple
import misc
from functools import partial

import mount
import iscsi
from sync import asyncmethod, AsyncCallStub

class AliasAlreadyRegisteredError(RuntimeError): pass
class AliasNotRegisteredError(RuntimeError): pass
class UnsupportedAuthenticationMethod(RuntimeError): pass

IscsiConnectionParameters = namedtuple("IscsiConnectionParameters", "target, iface, credentials")
PosixFsConnectionParameters = namedtuple("PosixFsConnectionParameters", "spec, vfsType, options")
LocaFsConnectionParameters = namedtuple("LocaFsConnectionParameters", "path")
NfsConnectionParameters = namedtuple("NfsConnectionParameters", "export, retrans, timeout, version")
ConnectionInfo = namedtuple("ConnectionInfo", "type, params")

def _credentialAssembly(credInfo):
    authMethod = credInfo.get('type', 'chap').lower()
    if authMethod != 'chap':
        raise UnsupportedAuthenticationMethod(authMethod)

    params = credInfo.get('params', {})
    username = params.get('username', None)
    password = params.get('password', None)
    return iscsi.ChapCredentials(username, password)


def _iscsiParameterAssembly(d):
    port = d['portal'].get('port', iscsi.ISCSI_DEFAULT_PORT)
    host = d['portal']['host']
    portal = iscsi.IscsiPortal(host, port)
    iqn = d['iqn']
    tpgt = d.get('tpgt', 1)
    target = iscsi.IscsiTarget(portal, tpgt, iqn)
    iface = iscsi.IscsiInterface(d.get('iface', 'default'))
    credInfo = d.get('credentials', None)
    cred = None
    if credInfo:
        cred = _credentialAssembly(credInfo)

    return IscsiConnectionParameters(target, iface, cred)

def _namedtupleAssembly(nt, d):
    d = d.copy()
    for field in nt._fields:
        if field not in d:
            d[field] = None

    return nt(**d)

_posixFsParameterAssembly = partial(_namedtupleAssembly,
        PosixFsConnectionParameters)
_nfsParamerterAssembly = partial(_namedtupleAssembly, NfsConnectionParameters)
_localFsParameterAssembly = partial(_namedtupleAssembly, LocaFsConnectionParameters)


_TYPE_NT_MAPPING = {
        'iscsi': _iscsiParameterAssembly,
        'sharedfs': _posixFsParameterAssembly,
        'posixfs': _posixFsParameterAssembly,
        'nfs': _nfsParamerterAssembly,
        'localfs': _localFsParameterAssembly}

def dict2conInfo(d):
    conType = d['type']
    params = _TYPE_NT_MAPPING[conType](d.get('params', {}))
    return ConnectionInfo(conType, params)

class ExampleConnection(object):
    """Do not inherit from this object it is just to show and document the
    connection object interface"""

    def __init__(self, arg1, arg2=None):
        """The connection should get all the information in the ctor.
        connection properties should not be modified after initialization"""
        pass

    def connect(self):
        """Connect if not connected. If connected just return
        successfully"""
        pass

    def disconnect(self):
        """Disconnect if connected. If already disconnected fail silently"""
        pass

    def isConnected(self):
        """Is the connection active. This function should return quickly. Think
        long and hard if you really want to do something that could block here
        and if you do implement the caching inside the method so it doesn't
        appear to block.

        Don't use this to monitor connection health"""
        pass

    def __eq__(self):
        """Implement this! It will be used to detect multiple connections to the
        same target"""
        pass

    def __hash__(self):
        """All connection objects mush be hashable so they can be used as keys of dictionaries"""

class MountConnection(object):
    localPathBase = "/tmp"

    @property
    def remotePath(self):
        return self._remotePath

    @property
    def vfsType(self):
        return self._vfsType

    @property
    def options(self):
        return self._options

    @classmethod
    def setLocalPathBase(cls, path):
        cls.localPathBase = path

    def __init__(self, spec, vfsType=None, options=""):
        self._vfsType = vfsType
        self._remotePath = spec
        self._options = options
        self._mount = mount.Mount(spec, self._getLocalPath())

    def _getLocalPath(self):
        return os.path.join(self.localPathBase, self._remotePath.replace("_", "__").replace("/", "_"))

    def connect(self):
        if self._mount.isMounted():
            return

        try:
            os.makedirs(self._getLocalPath())
        except OSError as e:
            if e.errno != errno.EEXIST:
                raise

        self._mount.mount(self.options, self._vfsType)

    def isConnected(self):
        return self._mount.isMounted()

    def disconnect(self):
        self._mount.umount(True, True)
        try:
            os.rmdir(self._getLocalPath())
        except OSError as e:
            if e.errno != errno.ENOENT:
                raise

    def __eq__(self, other):
        if not isinstance(other, MountConnection):
            return False

        return self._mount.__eq__(other._mount)

    def getMountObj(self):
        return self._mount

    def __hash__(self):
        return hash(type(self)) ^ hash(self._mount)

class NFSConnection(object):
    DEFAULT_OPTIONS = ["soft", "nosharecache"]

    @property
    def remotePath(self):
        return self._remotePath

    @property
    def timeout(self):
        return self._timeout

    @property
    def retrans(self):
        return self._retrans

    @property
    def version(self):
        if self._version is not None:
            return self._version

        # Version was not specified but if we are connected we can figure out
        # the negotiated version
        mnt = self._mountCon.getMountObj()
        try:
            rec = mnt.getRecord()
            if rec.fs_vfstype == "nfs4":
                return 4
            else:
                return 3
        except OSError:
            # We are not connected
            pass

        # Return -1 to signify the version has not been negotiated yet
        return -1


    def __init__(self, export, timeout=600, retrans=6, version=None):
        self._remotePath = normpath(export)
        self._version = version
        options = self.DEFAULT_OPTIONS[:]
        self._timeout = timeout
        self._version = version
        self._retrans = retrans
        options.append("timeo=%d" % timeout)
        options.append("retrans=%d" % retrans)
        if version:
            options.append("vers=%d" % version)

        self._mountCon = MountConnection(export, "nfs", ",".join(options))

    def connect(self):
        return self._mountCon.connect()

    def isConnected(self):
        return self._mountCon.isConnected()

    def disconnect(self):
        return self._mountCon.disconnect()

    def __eq__(self, other):
        if not isinstance(other, NFSConnection):
            return False

        return self._mountCon.__eq__(other._mountCon)

    def __hash__(self):
        return hash(type(self)) ^ hash(self._mountCon)

class IscsiConnection(object):
    @property
    def target(self):
        return self._target

    @property
    def iface(self):
        return self._iface

    def __init__(self, target, iface=None, credentials=None):
        self._target = target

        if iface is None:
            iface = iscsi.IscsiInterface('default')

        self._iface = iface
        self._cred = credentials

        self._lastSessionId = -1

    def connect(self):
        iscsi.addIscsiNode(self._iface, self._target, self._cred)

    def isSession(self, session):
        target = session.target
        portal = target.portal
        iface = session.iface
        cred = session.credentials

        if self._target.portal.hostname != portal.hostname:
            host = self._target.portal.hostname
            try:
                ip = socket.gethostbyname(host)
                if ip != portal.hostname:
                    return False

            except socket.gaierror:
                return False

        if self._target.portal.port != portal.port:
            return False

        if self._target.tpgt != target.tpgt:
            return False

        if self._target.iqn != target.iqn:
            return False

        if self._iface.name != iface.name:
            return False

        if self._cred != cred:
            return False

        return True

    def getSessionInfo(self):
        sessions = iscsi.iterateIscsiSessions()
        try:
            info = iscsi.getSessionInfo(self._lastSessionId)
            sessions = chain(info, sessions)
        except Exception:
            pass

        for session in iscsi.iterateIscsiSessions():
            if self.isSession(session):
                self._lastSessionId = session.id
                return session

        raise OSError(errno.ENOENT, "Session not found")

    def isConnected(self):
        try:
            self.getSessionInfo()
            return True
        except OSError as e:
            if e.errno == errno.ENOENT:
                return False
            raise

    def disconnect(self):
        try:
            sid = self.getSessionInfo().id
        except OSError, e:
            if e.errno == errno.ENOENT:
                return
            raise

        iscsi.disconnectiScsiSession(sid)

    def __eq__(self, other):
        if not isinstance(other, IscsiConnection):
            return False

        try:
            myInfo = self.getSessionInfo()
            hisInfo = other.getSessionInfo()
            return myInfo.id == hisInfo.id
        except OSError:
            pass

        return hash(self) == hash(other)

    def __hash__(self):
        hsh = hash(type(self))
        for attr in (self._target, self._cred, self._iface.name):
            hsh ^= hash(attr)

        return hsh


class LocalDirectoryConnection(object):
    @property
    def path(self):
        return self._path

    def __init__(self, path):
        self._path = path

    @classmethod
    def setLocalPathBase(cls, path):
        cls.localPathBase = path

    def _getLocalPath(self):
        return os.path.join(self.localPathBase, self._path.replace("_", "__").replace("/", "_"))

    def checkTarget(self):
        return os.path.exists(self._path) and os.path.isdir(self._path)

    def checkLink(self):
        lnPath = self._getLocalPath()
        if os.path.lexists(lnPath):
            if normpath(os.readlink(lnPath)) == self._path:
                return True

            os.unlink(lnPath)

        return False

    def connect(self):
        if not self.checkTarget():
            #TODO: Use proper exception
            raise Exception("Could not like to directory. Path does not exist "
                    "or isn't a directory")

        if self.checkLink():
            return

        lnPath = self._getLocalPath()
        os.symlink(self._path, lnPath)
        os.chmod(lnPath, 0775)

    def isConnected(self):
        return os.path.exists(self._getLocalPath())

    def disconnect(self):
        lnPath = self._getLocalPath()
        try:
            os.unlink(lnPath)
        except OSError as e:
            if e.errno != errno.ENOENT:
                raise

    def __eq__(self, other):
        if not isinstance(other, LocalDirectoryConnection):
            return False

        return self._path == other._path

    def __hash__(self):
        return hash(type(self)) ^ hash(self._path)

class IlligalAliasError(RuntimeError): pass
class ConnectionAliasRegistrar(object):
    log = logging.getLogger("StorageServer.ConnectionAliasRegistrar")
    def __init__(self, persistDir):
        self._aliases = {}
        self._syncroot = Lock()
        self._persistDir = persistDir
        for alias, conInfo in self._iterPersistedConnectionInfo():
            self._aliases[alias] = conInfo

    def register(self, alias, connectionInfo):
        with self._syncroot:
            if alias in self._aliases:
                raise AliasAlreadyRegisteredError(alias)

            self._persistAlias(alias, connectionInfo)
            self._aliases[alias] = connectionInfo

    def unregister(self, alias):
        with self._syncroot:
            try:
                del self._aliases[alias]
            except KeyError:
                raise AliasNotRegisteredError(alias)

            self._unpersistAlias(alias)

    def getConnectionInfo(self, alias):
        with self._syncroot:
            try:
                info = self._aliases[alias]
            except KeyError:
                raise AliasNotRegisteredError(alias)

            return info

    def getAliases(self):
        # No need for deep copy as strings and tuples ar immutable
        return self._aliases.copy()

    def _getConnectionFile(self, alias):
        if "/" in alias:
            raise IlligalAliasError(alias)

        try:
            os.makedirs(self._persistDir)
        except OSError, e:
            if e.errno != errno.EEXIST:
                raise

        return os.path.join(self._persistDir, alias + ".con")

    def _iterPersistedConnectionInfo(self):
        for path in glob.iglob(os.path.join(self._persistDir, "*.con")):
            alias = splitext(basename(path))[0]
            with open(path, "r") as f:
                conInfo = pickle.load(f)

            # Yield out of scope so the file is closed before giving the flow
            # back to calling method
            yield alias, conInfo

    def _persistAlias(self, alias, conInfo):
        path = self._getConnectionFile(alias)
        tmpPath = path + ".tmp"
        with open(tmpPath, "w") as f:
            pickle.dump(conInfo, f)

        os.rename(tmpPath, path)

    def _unpersistAlias(self, alias):
        os.unlink(self._getConnectionFile(alias))

class UnknownConnectionTypeError(RuntimeError): pass

class ConnectionFactory(object):
    _registeredConnectionTypes = {
        "nfs" : NFSConnection,
        "posixfs" : MountConnection,
        "iscsi" : IscsiConnection,
        "localfs" : LocalDirectoryConnection,
        }

    @classmethod
    def createConnection(cls, conInfo):
        conType = conInfo.type
        params = misc.namedtuple2dict(conInfo.params)
        for param in params.keys():
            if params[param] is None:
                del params[param]
        try:
            ctor = cls._registeredConnectionTypes[conType]
        except KeyError:
            raise UnknownConnectionTypeError(conType)

        return ctor(**params)


class ConnectionMonitor(object):
    _log = logging.getLogger("ConnectionMonitor")

    TAG = "managed"

    def __init__(self, aliasRegistrar, checkInterval=10):
        self._aliasRegistrar = aliasRegistrar
        self.checkInterval = checkInterval
        self._conDict = {}
        self._conDictLock = RLock()

        self._lastErrors = {}
        self._activeOperations = {}
        self._activeOpsLock = Lock()
        self._stopEvent = Event()
        self._stopEvent.set()
        for alias, conInfo in self._aliasRegistrar.getAliases().iteritems():
            self._conDict[alias] = ConnectionFactory.createConnection(conInfo)

    def startMonitoring(self):
        t = Thread(target=self._monitorConnections)
        t.setDaemon(True)
        self._stopEvent.clear()
        t.start()

    def stopMonitoring(self):
        self._stopEvent.set()

    def _recoverLostConnection(self, conId, con):
        with self._activeOpsLock:
            if con in self._activeOperations:
                e = self._activeOperations[con]
                if not e.wait(0):
                    return

                del self._activeOperations[con]

                if con.isConnected():
                    return

            self._log.debug("Recovering lost connection '%s'", conId)
            self._activeOperations[con] = self._asyncConnect(con)


    def _checkConnections(self):
        for conId, con in self._conDict.iteritems():
            # Spread checks over time so we don't get cpu spikes
            # I'm not sure it's the best way to go but I would like to try it
            # out. It feels like it might be a good pattern to use.
            self._stopEvent.wait(self.checkInterval / float(len(self._conDict)))

            if self._stopEvent.isSet():
                break

            if con.isConnected():
                continue

            self._log.debug("Connection '%s' is not connected", conId)

            self._recoverLostConnection(conId, con)

    def _monitorConnections(self):
        while True:
            try:
                self._checkConnections()

                if len(self._conDict) == 0:
                    self._stopEvent.wait(self.checkInterval)

                if self._stopEvent.isSet():
                    break
            except:
                self._log.error("Monitoring failed", exc_info=True)

        self._log.debug("Monitoring stopped")

    def getConnectionsStatus(self):
        res = {}
        for key in self._monitorConnections.keys():
            # A key could be removed while iterating
            value = self._monitorConnections.get(key, None)
            if value == None:
                continue

            res[key] = (value.isConnected(), "")

        return res

    def _addConnection(self, alias):
        with self._conDictLock:
            if alias in self._conDict:
                return self._conDict[alias]

            conInfo = self._aliasRegistrar.getConnectionInfo(alias)

            con = ConnectionFactory.createConnection(conInfo)

            self._conDict[alias] = con

            return con

    def _delConnection(self, alias):
        with self._conDictLock:
            del self._conDict[alias]

    def getMonitoredConnectionsDict(self):
        return self._conDict.copy()

    def manage(self, alias):
        conObj = self._addConnection(alias)
        self._log.info("Started managing connection alias %s", alias)

        with self._activeOpsLock:
            res = self._activeOperations.get(conObj, None)
            if res is not None:
                return res

            res = self._activeOperations[conObj] = self._asyncConnect(conObj)

        return res

    def unmanage(self, alias):
        with self._conDictLock:
            con = self._conDict[alias]
            self._delConnection(alias)
            self._log.info("Stopped managing connection alias %s", alias)
            if not con in self._conDict.values():
                return self._asyncDisconnect(con)

        return AsyncCallStub(None)

    def getLastError(self, conId):
        return self._lastErrors.get(self._conDict[conId], None)

    @asyncmethod
    def _asyncConnect(self, con):
        try:
            con.connect()
            self._lastErrors.pop(con, None)
        except Exception as e:
            self._lastErrors[con] = e
            self._log.error("Could not connect to %s", con, exc_info=True)
            raise

    @asyncmethod
    def _asyncDisconnect(self, con):
        try:
            con.disconnect()
        except:
            self._log.error("Could not disconnect from %s", con, exc_info=True)
            raise
        finally:
            self._lastErrors.pop(con, None)
