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
from threading import Lock
import socket
import pickle
import glob
from collections import namedtuple
import misc

import mount
import iscsi

class AliasAlreadyRegisteredError(RuntimeError): pass
class AliasNotRegisteredError(RuntimeError): pass

IscsiConnectionParameters = namedtuple("IscsiConnectionParameters", "target, iface, credentials")
PosixFsConnectionParameters = namedtuple("PosixFsConnectionParameters", "spec, vfsType, options")
LocaFsConnectionParameters = namedtuple("LocaFsConnectionParameters", "path")
NfsConnectionParameters = namedtuple("NfsConnectionParameters", "export, retrans, timeout, version")
ConnectionInfo = namedtuple("ConnectionInfo", "type, params")

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
        return os.path.join(self.localPathBase, self._path.replace("/", "_"))

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
        return os.path.exists(self._path)

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
    def _createConnection(cls, conInfo):
        conType = conInfo.type
        params = misc.namedtuple2dict(conInfo.params)
        try:
            ctor = cls._registeredConnectionTypes[conType]
        except KeyError:
            raise UnknownConnectionTypeError(conType)

        return ctor(**params)
