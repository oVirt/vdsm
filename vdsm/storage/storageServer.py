#
# Copyright 2012-2015 Red Hat, Inc.
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
import errno
import logging
from os.path import normpath, basename, splitext
import os
from threading import RLock, Lock, Event, Thread
import socket
import glob
from collections import namedtuple
import misc
from functools import partial
import six
import sys

from vdsm.compat import pickle
from vdsm.config import config
from vdsm import udevadm
from vdsm import utils

import supervdsm
import mount
import fileUtils
import fileSD
import iscsi
from sync import asyncmethod, AsyncCallStub
from mount import MountError
import gluster.cli
import storage_exception as se


class AliasAlreadyRegisteredError(RuntimeError):
    pass


class AliasNotRegisteredError(RuntimeError):
    pass


class UnsupportedAuthenticationMethod(RuntimeError):
    pass

IscsiConnectionParameters = namedtuple("IscsiConnectionParameters",
                                       "target, iface, credentials")

PosixFsConnectionParameters = namedtuple("PosixFsConnectionParameters",
                                         "spec, vfsType, options")

GlusterFsConnectionParameters = namedtuple("GlusterFsConnectionParameters",
                                           "spec, vfsType, options")

LocaFsConnectionParameters = namedtuple("LocaFsConnectionParameters", "path")
NfsConnectionParameters = namedtuple("NfsConnectionParameters",
                                     "export, retrans, timeout, version, "
                                     "extraOptions")

FcpConnectionParameters = namedtuple("FcpConnectionParameters", "")

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
_glusterFsParameterAssembly = partial(_namedtupleAssembly,
                                      GlusterFsConnectionParameters)
_nfsParamerterAssembly = partial(_namedtupleAssembly, NfsConnectionParameters)
_localFsParameterAssembly = partial(_namedtupleAssembly,
                                    LocaFsConnectionParameters)


_TYPE_NT_MAPPING = {
    'iscsi': _iscsiParameterAssembly,
    'sharedfs': _posixFsParameterAssembly,
    'posixfs': _posixFsParameterAssembly,
    'glusterfs': _glusterFsParameterAssembly,
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
        """Implement this! It will be used to detect multiple connections to
        the same target"""
        pass

    def __ne__(self):
        """Must be implemented otherwise != operator will return True for equal
        objects"""

    def __hash__(self):
        """All connection objects mush be hashable so they can be used as keys
        of dictionaries"""


class MountConnection(object):

    CGROUP = None
    DIR = ""

    log = logging.getLogger("Storage.StorageServer.MountConnection")
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

    @classmethod
    def getLocalPathBase(cls):
        return cls.localPathBase

    def __init__(self, spec, vfsType=None, options="", mountClass=mount.Mount):
        self._vfsType = vfsType
        # Note: must be normalized before we escape "/" in _getLocalPath.
        # See https://bugzilla.redhat.com/1300749
        self._remotePath = normpath(spec)
        self._options = options
        self._mount = mountClass(spec, self._getLocalPath())

    def _getLocalPath(self):
        path = self._remotePath.replace("_", "__").replace("/", "_")
        return os.path.join(self.localPathBase, self.DIR, path)

    def connect(self):
        if self._mount.isMounted():
            return

        self.validate()

        fileUtils.createdir(self._getLocalPath())

        try:
            self._mount.mount(self.options, self._vfsType, cgroup=self.CGROUP)
        except MountError:
            t, v, tb = sys.exc_info()
            try:
                os.rmdir(self._getLocalPath())
            except OSError as e:
                self.log.warn("Error removing mountpoint directory %r: %s",
                              self._getLocalPath(), e)
            six.reraise(t, v, tb)
        else:
            try:
                fileSD.validateDirAccess(
                    self.getMountObj().getRecord().fs_file)
            except se.StorageServerAccessPermissionError:
                t, v, tb = sys.exc_info()
                try:
                    self.disconnect()
                except OSError:
                    self.log.exception("Error disconnecting")
                six.reraise(t, v, tb)

    def validate(self):
        """
        This method may be overriden by derived classes to perform validation.
        """

    def isConnected(self):
        return self._mount.isMounted()

    def disconnect(self):
        self._mount.umount(True, True)
        try:
            os.rmdir(self._getLocalPath())
        except OSError as e:
            if e.errno != errno.ENOENT:
                raise

    def getMountObj(self):
        return self._mount

    def __eq__(self, other):
        return (self.__class__ == other.__class__ and
                self._vfsType == other._vfsType and
                self._remotePath == other._remotePath and
                self._options == other._options)

    def __ne__(self, other):
        return not self == other

    def __hash__(self):
        return hash((self.__class__,
                     self._vfsType,
                     self._remotePath,
                     self._options))

    def __str__(self):
        return "<{0} spec={1!r} vfstype={2!r} options={3!r}>".format(
            self.__class__.__name__,
            self._remotePath,
            self._vfsType,
            self._options)


class GlusterFSConnection(MountConnection):

    # Run the mount command as a systemd service, so glusterfs helper run in
    # its own cgroup, and will not die when vdsm is terminated.
    #
    # - vdsm.slice
    #   - vdsm-glusterfs.slice
    #     - run-22137.scope
    #       - 22180 /usr/bin/glusterfs ...
    #     - run-21649.scope
    #       - 21692 /usr/bin/glusterfs ...
    #
    CGROUP = "vdsm-glusterfs"
    DIR = "glusterSD"
    ALLOWED_REPLICA_COUNTS = tuple(
        config.get("gluster", "allowed_replica_counts").split(","))

    def __init__(self,
                 spec,
                 vfsType=None,
                 options="",
                 mountClass=mount.Mount):
        super(GlusterFSConnection, self).__init__(spec,
                                                  vfsType=vfsType,
                                                  options=options,
                                                  mountClass=mountClass)
        self._volinfo = None
        self._volfileserver, volname = self._remotePath.split(":", 1)
        self._volname = volname.strip('/')
        self._have_gluster_cli = gluster.cli.exists()

    @property
    def options(self):
        backup_servers_option = ""
        if "backup-volfile-servers" in self._options:
            self.log.warn("Using user specified backup-volfile-servers option")
        elif self._have_gluster_cli:
            backup_servers_option = self._get_backup_servers_option()
        return ",".join(
            p for p in (self._options, backup_servers_option) if p)

    @property
    def volinfo(self):
        if self._volinfo is None:
            self._volinfo = self._get_gluster_volinfo()
        return self._volinfo

    def validate(self):
        if not self._have_gluster_cli:
            self.log.warning("Required glusterfs-cli package is missing "
                             "on this host. Note that automatic detection "
                             "of backup servers will be disabled! Please "
                             "install the missing package in order to "
                             "automatically mount gluster storage backup "
                             "servers")
            return

        replicaCount = self.volinfo['replicaCount']
        if replicaCount not in self.ALLOWED_REPLICA_COUNTS:
            self.log.warning("Unsupported replica count (%s) for volume %r, "
                             "please upgrade volume to replica 3",
                             replicaCount, self._volname)

    def _get_backup_servers_option(self):
        servers = utils.unique(brick.split(":")[0] for brick
                               in self.volinfo['bricks'])
        self.log.debug("Using bricks: %s", servers)
        if self._volfileserver in servers:
            servers.remove(self._volfileserver)
        else:
            self.log.warning("gluster server %r is not in bricks %s, possibly "
                             "mounting duplicate servers",
                             self._volfileserver, servers)

        if not servers:
            return ""

        return "backup-volfile-servers=" + ":".join(servers)

    def _get_gluster_volinfo(self):
        volinfo = supervdsm.getProxy().glusterVolumeInfo(self._volname,
                                                         self._volfileserver)
        return volinfo[self._volname]


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
    def options(self):
        return self._options

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

    def __init__(self, export, timeout=600, retrans=6, version=None,
                 extraOptions=""):
        self._remotePath = normpath(export)
        options = self.DEFAULT_OPTIONS[:]
        self._timeout = timeout
        self._version = version
        self._retrans = retrans
        self._options = extraOptions
        options.append("timeo=%d" % timeout)
        options.append("retrans=%d" % retrans)

        if version:
            try:
                vers = [int(p) for p in version.split(".")]
            except ValueError:
                raise ValueError("Invalid NFS version '%s'" % version)

            if len(vers) > 2:
                raise ValueError("Invalid NFS version '%s'" % version)

            options.append("nfsvers=%d" % vers[0])
            if len(vers) > 1:
                options.append("minorversion=%d" % vers[1])
        optionsString = ",".join(filter(None, options + [extraOptions]))
        self._mountCon = MountConnection(export, "nfs", optionsString)

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
    log = logging.getLogger("Storage.Server.ISCSI")

    class Mismatch(Exception):

        def __init__(self, fmt, *args):
            self.fmt = fmt
            self.args = args

        def __str__(self):
            return self.fmt % self.args

        def __repr__(self):
            # Required for logging list of errors
            return repr(self.__str__())

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
        timeout = config.getint("irs", "scsi_settle_timeout")
        udevadm.settle(timeout)

    def _match(self, session):
        target = session.target
        portal = target.portal
        iface = session.iface
        cred = session.credentials

        if self._target.portal.hostname != portal.hostname:
            host = self._target.portal.hostname
            try:
                ip = socket.gethostbyname(host)
                if ip != portal.hostname:
                    raise self.Mismatch("target.portal.hostname mismatch: "
                                        "%r != %r", ip, portal.hostname)

            except socket.gaierror:
                raise self.Mismatch("target.portal.hostname mismatch: "
                                    "%r != %r", host, portal.hostname)

        if self._target.portal.port != portal.port:
            raise self.Mismatch("target.portal.port mismatch: %r != %r",
                                self._target.portal.port, portal.portal)

        if self._target.tpgt is not None and self._target.tpgt != target.tpgt:
            raise self.Mismatch("target.tpgt mismatch: %r != %r",
                                self._target.tpgt, target.tpgt)

        if self._target.iqn != target.iqn:
            raise self.Mismatch("target.iqn mismatch: %r != %r",
                                self._target.iqn, target.iqn)

        if self._iface.name != iface.name:
            raise self.Mismatch("iface.name mismatch: %r != %r",
                                self._iface.name, iface.name)

        if self._cred != cred:
            raise self.Mismatch("cred mismatch")

    def getSessionInfo(self):
        errors = []
        for session in iscsi.iterateIscsiSessions():
            try:
                self._match(session)
            except self.Mismatch as e:
                errors.append(e)
            else:
                self._lastSessionId = session.id
                return session

        self.log.debug("Session mismatches: %s", errors)
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
        self.log.info("disconnecting")
        try:
            sid = self.getSessionInfo().id
        except OSError as e:
            if e.errno == errno.ENOENT:
                self.log.debug("not connected")
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


class FcpConnection(object):

    def connect(self):
        pass

    def disconnect(self):
        pass

    def isConnected(self):
        return True

    def __eq__(self, other):
        return self.__class__ == other.__class

    def __ne__(self, other):
        return not self == other

    def __hash__(self):
        return hash(self.__class__)


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
        return os.path.join(self.localPathBase,
                            self._path.replace("_", "__").replace("/", "_"))

    def checkTarget(self):
        if not os.path.isdir(self._path):
            raise se.StorageServerLocalNotDirError(self._path)
        fileSD.validateDirAccess(self._path)
        return True

    def checkLink(self):
        lnPath = self._getLocalPath()
        if os.path.lexists(lnPath):
            if normpath(os.readlink(lnPath)) == self._path:
                return True

            os.unlink(lnPath)

        return False

    def connect(self):
        self.checkTarget()

        if self.checkLink():
            return

        lnPath = self._getLocalPath()
        os.symlink(self._path, lnPath)

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


class IllegalAliasError(RuntimeError):
    pass


class ConnectionAliasRegistrar(object):
    log = logging.getLogger("Storage.StorageServer.ConnectionAliasRegistrar")

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
            raise IllegalAliasError(alias)

        try:
            os.makedirs(self._persistDir)
        except OSError as e:
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


class UnknownConnectionTypeError(RuntimeError):
    pass


class ConnectionFactory(object):
    _registeredConnectionTypes = {
        "nfs": NFSConnection,
        "posixfs": MountConnection,
        "glusterfs": GlusterFSConnection,
        "iscsi": IscsiConnection,
        "localfs": LocalDirectoryConnection,
        "fcp": FcpConnection,
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
    _log = logging.getLogger("Storage.ConnectionMonitor")

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
            interval = self.checkInterval / float(len(self._conDict))
            self._stopEvent.wait(interval)

            if self._stopEvent.isSet():
                break

            with self._conDictLock:
                if conId not in self._conDict:
                    # the connection is deleted when we were in wait for the
                    # stop event, so skip it
                    continue

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
            if value is None:
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
            if con not in self._conDict.values():
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
