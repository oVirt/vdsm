# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import

import errno
import logging
from os.path import normpath
import os
import socket
from collections import namedtuple
import six
import sys

from vdsm.config import config
from vdsm import utils
from vdsm.common import concurrent
from vdsm.common import supervdsm
from vdsm.common import udevadm
from vdsm.common.marks import deprecated
from vdsm.gluster import cli as gluster_cli
from vdsm.gluster import exception as ge
from vdsm.storage import exception as se
from vdsm.storage import fileSD
from vdsm.storage import fileUtils
from vdsm.storage import iscsi
from vdsm.storage import mount
from vdsm.storage import sd
from vdsm.storage.mount import MountError


IscsiConnectionParameters = namedtuple("IscsiConnectionParameters",
                                       "id, target, iface, credentials")

PosixFsConnectionParameters = namedtuple("PosixFsConnectionParameters",
                                         "id, spec, vfsType, options")

GlusterFsConnectionParameters = namedtuple("GlusterFsConnectionParameters",
                                           "id, spec, vfsType, options")

LocaFsConnectionParameters = namedtuple("LocaFsConnectionParameters",
                                        "id, path")
NfsConnectionParameters = namedtuple("NfsConnectionParameters",
                                     "id, export, retrans, timeout, version, "
                                     "extraOptions")

FcpConnectionParameters = namedtuple("FcpConnectionParameters", "id")

ConnectionInfo = namedtuple("ConnectionInfo", "type, params")

# Connection Management API competability code
# Remove when deprecating dis\connectStorageServer

CON_TYPE_ID_2_CON_TYPE = {
    sd.LOCALFS_DOMAIN: 'localfs',
    sd.NFS_DOMAIN: 'nfs',
    sd.ISCSI_DOMAIN: 'iscsi',
    sd.FCP_DOMAIN: 'fcp',
    sd.POSIXFS_DOMAIN: 'posixfs',
    sd.GLUSTERFS_DOMAIN: 'glusterfs'}

log = logging.getLogger("storage.storageServer")


class Connection:

    @classmethod
    def connect_all(cls, connections):
        results = []

        for con in connections:
            try:
                con.connect()
            except Exception as err:
                log.exception("Could not connect to storage server")
                status, _ = cls.translate_error(err)
            else:
                status = 0

            results.append((con, status))

        return results

    @classmethod
    def disconnect_all(cls, connections):
        results = []

        for con in connections:
            try:
                con.disconnect()
                status = 0
            except Exception as err:
                log.exception("Could not disconnect from storage server")
                status, _ = cls.translate_error(err)

            results.append((con, status))

        return results

    @classmethod
    def translate_error(cls, e):
        if e is None:
            return 0, ""

        if hasattr(e, 'code'):
            return e.code, e.msg

        return se.GeneralException.code, str(e)


class ExampleConnection(Connection):
    """Do not inherit from this object it is just to show and document the
    connection object interface"""

    def __init__(self, arg1, arg2=None):
        """The connection should get all the information in the ctor.
        connection properties should not be modified after initialization"""
        pass

    @property
    def id(self):
        """The ID of the connection"""
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

    def __eq__(self, other):
        """Implement this! It will be used to detect multiple connections to
        the same target"""
        pass

    def __ne__(self, other):
        """Must be implemented otherwise != operator will return True for equal
        objects"""

    def __hash__(self):
        """All connection objects mush be hashable so they can be used as keys
        of dictionaries"""


class MountConnection(Connection):

    CGROUP = None
    DIR = ""

    localPathBase = "/tmp"

    @property
    def id(self):
        return self._id

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

    @classmethod
    def translate_error(cls, e):
        if isinstance(e, mount.MountError):
            return se.MountError.code, se.MountError.msg
        return super().translate_error(e)

    def __init__(self,
                 id,
                 spec,
                 vfsType=None,
                 options="",
                 mountClass=mount.Mount):
        self._id = id
        self._vfsType = vfsType
        # Note: must be normalized before we escape "/" in _getLocalPath.
        # See https://bugzilla.redhat.com/1300749
        self._remotePath = fileUtils.normalize_path(spec)
        self._options = options
        self._mount = mountClass(self._remotePath, self._getLocalPath())

    def _getLocalPath(self):
        path = fileUtils.transformPath(self._remotePath)
        return os.path.join(self.localPathBase, self.DIR, path)

    def connect(self):
        if self._mount.isMounted():
            return

        self.validate()
        log.info("Creating directory %r", self._getLocalPath())
        fileUtils.createdir(self._getLocalPath())

        try:
            self._mount.mount(self.options, self._vfsType, cgroup=self.CGROUP)
        except MountError:
            t, v, tb = sys.exc_info()
            try:
                os.rmdir(self._getLocalPath())
            except OSError as e:
                log.warning(
                    "Error removing mountpoint directory %r: %s",
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
                    log.exception("Error disconnecting")
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
                self._id == other._id and
                self._vfsType == other._vfsType and
                self._remotePath == other._remotePath and
                self._options == other._options)

    def __ne__(self, other):
        return not self == other

    def __hash__(self):
        return hash((self.__class__,
                     self._id,
                     self._vfsType,
                     self._remotePath,
                     self._options))

    def __repr__(self):
        return "<{0} id= {1!r} spec={2!r} vfstype={3!r} options={4!r}>".format(
            self.__class__.__name__,
            self._id,
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
                 id,
                 spec,
                 vfsType=None,
                 options="",
                 mountClass=mount.Mount):
        super(GlusterFSConnection, self).__init__(id,
                                                  spec,
                                                  vfsType=vfsType,
                                                  options=options,
                                                  mountClass=mountClass)
        self._volinfo = None
        self._volfileserver, volname = self._remotePath.split(":", 1)
        self._volname = volname.strip('/')
        self._have_gluster_cli = gluster_cli.exists()

    @property
    def options(self):
        backup_servers_option = ""
        if "backup-volfile-servers" in self._options:
            log.warning(
                "Using user specified backup-volfile-servers option")
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
            log.warning("Required glusterfs-cli package is missing "
                        "on this host. Note that automatic detection "
                        "of backup servers will be disabled! Please "
                        "install the missing package in order to "
                        "automatically mount gluster storage backup "
                        "servers")
            return

        if not self.volinfo:
            return

        if "disperse" in self.volinfo['volumeType'].lower():
            log.warning("Unsupported volume type, volume: %r, "
                        "volume type: %r. Please use the replicate type."
                        "To recover existing migrate it to "
                        "supported type.",
                        self._volname, self.volinfo['volumeType'])
            return

        replicaCount = self.volinfo['replicaCount']
        if replicaCount not in self.ALLOWED_REPLICA_COUNTS:
            log.warning("Unsupported replica count (%s) for volume %r, "
                        "please upgrade volume to replica 3",
                        replicaCount, self._volname)

    def _get_backup_servers_option(self):
        if not self.volinfo:
            return ""

        servers = utils.unique(brick.split(":")[0] for brick
                               in self.volinfo['bricks'])
        log.debug("Using bricks: %s", servers)
        if self._volfileserver in servers:
            servers.remove(self._volfileserver)
        else:
            log.warning("gluster server %r is not in bricks %s, possibly "
                        "mounting duplicate servers",
                        self._volfileserver, servers)

        if not servers:
            return ""

        return "backup-volfile-servers=" + ":".join(servers)

    def _get_gluster_volinfo(self):
        try:
            superVdsmProxy = supervdsm.getProxy()
            volinfo = superVdsmProxy.glusterVolumeInfo(self._volname,
                                                       self._volfileserver)
            return volinfo[self._volname]
        except ge.GlusterException as e:
            # The remote host may be down.
            # If we are running on a hyperconverged system the gluster client
            # can use one of the other gluster servers.
            log.info("Failed to get volume info from remote server %s: %s",
                     self._volfileserver, e)
            log.debug("Trying to get volume info from backup servers: %s",
                      self._options)
            try:
                volinfo = superVdsmProxy.glusterVolumeInfo(self._volname)
                return volinfo[self._volname]
            except ge.GlusterException as e:
                log.warning(
                    "Failed to get volume info from backup servers: %s", e)
        return {}


class NFSConnection(Connection):
    DEFAULT_OPTIONS = ["soft", "nosharecache"]

    @property
    def id(self):
        return self._mountCon._id

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

    def __init__(self, id, export, timeout=100, retrans=3, version=None,
                 extraOptions=""):
        """
        According to nfs(5), NFS will retry a request after 100 deciseconds (10
        seconds). After each retransmission, the timeout is increased by timeo
        value (up to maximum of 600 seconds). After retrans retires, the NFS
        client will fail with "server not responding" message.

        With the default configuration we expect failures in 60 seconds, which
        is about 3 times longer than multipath timeout (20 seconds) for block
        storage.

        00:00   retry 1 (10 seconds timeout)
        00:10   retry 2 (20 seconds timeout)
        00:30   retry 3 (30 seconds timeout)
        01:00  request fail

        WARNNING: timeout value must not be smaller than sanlock iotimeout (10
        seconds). Using smaller value may cause sanlock to fail to renew
        leases.
        """
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

        extraOptions = [opt for opt in extraOptions.split(",") if opt]

        if version in (None, "3"):
            # This is NFSv3 mount, or auto-negotiate, which may become one.
            # Disable NFSv3 remote locks since they break HA VMs failover, and
            # may prevent starting a VM on another host after a host is lost.
            # Locks on this mount will use local locks.
            #
            # If users need NFSv3 remote locks, and do not care about this
            # issue, they can override this by specifying the 'lock' option in
            # engine mount options.
            #
            # See https://bugzilla.redhat.com/1550127

            if "lock" in extraOptions:
                log.warning("Using remote locks for NFSv3 locks, HA VMs "
                            "should not be used with this mount")
            elif "nolock" not in extraOptions:
                log.debug("Using local locks for NFSv3 locks")
                extraOptions.append("nolock")

        optionsString = ",".join(options + extraOptions)
        self._mountCon = MountConnection(id, export, "nfs", optionsString)

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


class IscsiConnection(Connection):

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
    def id(self):
        return self._id

    @property
    def target(self):
        return self._target

    @property
    def iface(self):
        return self._iface

    @property
    def cred(self):
        return self._cred

    def __init__(self, id, target, iface=None, credentials=None):
        self._id = id
        self._target = target

        if iface is None:
            iface = iscsi.IscsiInterface('default')

        self._iface = iface
        self._cred = credentials

    @classmethod
    def connect_all(cls, prep_cons):
        results = []
        logins = []

        # Prepare connections and setup iSCSI nodes serially. These operations
        # happen on the host and are very fast, so there's no need to run them
        # in parallel. Also, running them concurrently could cause locking
        # issues when multiple threads try to access local iscsi database.

        for con in prep_cons:
            try:
                con.setup_node()
            except Exception as err:
                log.error(
                    "Could not configure connection to %s and iface %s: %s",
                    con.target, con.iface, err)
                status, _ = cls.translate_error(err)
                results.append((con, status))
            else:
                logins.append(con)

        if not logins:
            return results

        # Run login to nodes in parallel. This operations happen on remote
        # iscsi server and if the some targets are not available, the operation
        # can take quite some time (by default 120 seconds) and can cause
        # engine command times out. Running login to targets in parallel should
        # mitigate this issue.

        max_workers = cls.max_workers(logins)

        log.info("Log in to %s targets using %s workers",
                 len(logins), max_workers)

        def iscsi_login(con):
            try:
                iscsi.loginToIscsiNode(con.iface, con.target)
                return con, 0
            except Exception as e:
                log.exception("Could not login to target")
                status, _ = cls.translate_error(e)
                return con, status

        login_results = concurrent.tmap(
            iscsi_login,
            logins,
            max_workers=max_workers,
            name="iscsi-login")

        for res in login_results:
            results.append(res.value)

        # Wait for all new devices to be settled.
        cls.settle_devices()

        return results

    @classmethod
    def max_workers(cls, logins):
        max_workers = config.getint("iscsi", "parallel_logins")
        if max_workers < 1:
            log.warning("Number of parallel logins (%d) is less then 1, "
                        "using only one thread", max_workers)
            max_workers = 1
        return min(len(logins), max_workers)

    @classmethod
    def settle_devices(cls):
        timeout = config.getint("irs", "udev_settle_timeout")
        udevadm.settle(timeout)

    @classmethod
    def translate_error(cls, e):
        if isinstance(e, iscsi.iscsiadm.IscsiAuthenticationError):
            return se.iSCSILoginAuthError.code, se.iSCSILoginAuthError.msg
        if isinstance(e, iscsi.iscsiadm.IscsiInterfaceError):
            return se.iSCSIifaceError.code, se.iSCSIifaceError.msg
        if isinstance(e, iscsi.iscsiadm.IscsiError):
            return se.iSCSISetupError.code, se.iSCSISetupError.msg
        return super().translate_error(e)

    def connect(self):
        self._maybe_connect_iser()
        self._connect_iscsi()
        self.settle_devices()

    def _connect_iscsi(self):
        iscsi.addIscsiNode(self._iface, self._target, self._cred)
        iscsi.loginToIscsiNode(self._iface, self._target)

    def setup_node(self):
        self._maybe_connect_iser()
        iscsi.addIscsiNode(self.iface, self.target, self.cred)

    @deprecated
    def _maybe_connect_iser(self):
        """
        Tries to connect the storage server over iSER.
        This applies if 'iser' is in the configuration option
        'iscsi_default_ifaces'.
        """
        # FIXME: remove this method when iface selection is in higher interface
        try:
            self._iface.initiatorName
        except KeyError:
            ifaces = config.get('irs', 'iscsi_default_ifaces').split(',')
            if 'iser' in ifaces:
                orig_iface = self._iface
                self._iface = iscsi.IscsiInterface('iser')
                try:
                    self._connect_iscsi()
                    self._disconnect_iscsi()
                except:
                    log.warning(
                        "Cannot connect to storage over iSER, using original "
                        "iface %r", orig_iface)
                    self._iface = orig_iface

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
                return session

        log.debug("Session mismatches: %s", errors)
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
        self._disconnect_iscsi()

    def _disconnect_iscsi(self):
        try:
            sid = self.getSessionInfo().id
        except OSError as e:
            if e.errno == errno.ENOENT:
                log.debug("not connected")
                return
            raise

        iscsi.disconnectiScsiSession(sid)

    def __eq__(self, other):
        if not isinstance(other, IscsiConnection):
            return False

        if self._id != other._id:
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
        for attr in (self._id, self._target, self._cred, self._iface.name):
            hsh ^= hash(attr)

        return hsh


class FcpConnection(Connection):

    @property
    def id(self):
        return self._id

    def __init__(self, id):
        self._id = id

    def connect(self):
        pass

    def disconnect(self):
        pass

    def isConnected(self):
        return True

    def __eq__(self, other):
        return (self.__class__ == other.__class and
                self._id == other._id)

    def __ne__(self, other):
        return not self == other

    def __hash__(self):
        return hash((self.__class__, self._id))


class LocalDirectoryConnection(Connection):

    @property
    def id(self):
        return self._id

    @property
    def path(self):
        return self._path

    def __init__(self, id, path):
        self._id = id
        self._path = path

    @classmethod
    def setLocalPathBase(cls, path):
        cls.localPathBase = path

    def _getLocalPath(self):
        return os.path.join(self.localPathBase,
                            fileUtils.transformPath(self._path))

    def checkTarget(self):
        if not os.path.isdir(self._path):
            raise se.InvalidParameterException(
                'path', self._path, "not a directory")
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

        return self._id == other._id and self._path == other._path

    def __hash__(self):
        return hash((self.__class__,
                     self._id,
                     self._path))


class UnknownConnectionTypeError(RuntimeError):
    pass


class ConnectionFactory(object):
    registeredConnectionTypes = {
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
        params = conInfo.params._asdict()
        for param in list(params):
            if params[param] is None:
                del params[param]
        try:
            ctor = cls.registeredConnectionTypes[conType]
        except KeyError:
            raise UnknownConnectionTypeError(conType)

        return ctor(**params)


def connect(dom_type, con_defs):
    con_class, connections = _prepare_connections(dom_type, con_defs)
    return con_class.connect_all(connections)


def disconnect(dom_type, con_defs):
    con_class, connections = _prepare_connections(dom_type, con_defs)
    return con_class.disconnect_all(connections)


def _prepare_connections(dom_type, con_defs):
    prep_cons = []
    con_type = None
    for con_def in con_defs:
        con_info = _connectionDict2ConnectionInfo(dom_type, con_def)
        prep_con = ConnectionFactory.createConnection(con_info)
        prep_cons.append(prep_con)
        con_type = con_info.type
    con_class = ConnectionFactory.registeredConnectionTypes[con_type]
    return con_class, prep_cons


def _connectionDict2ConnectionInfo(conTypeId, conDict):
    def getIntParam(optDict, key, default):
        res = optDict.get(key, default)
        if res is None:
            return res

        try:
            return int(res)
        except ValueError:
            raise se.InvalidParameterException(key, res)

    # FIXME: Remove when nfs_mount_options is no longer supported.  This is
    # in the compatibility layer so that the NFSConnection class stays clean.
    # Engine options have precendence, so use deprecated nfs_mount_options
    # only if engine passed nothing (indicated by default params of 'None').
    def tryDeprecatedNfsParams(conDict):
        if (conDict.get('protocol_version', None),
                conDict.get('retrans', None),
                conDict.get('timeout', None)) == (None, None, None):
            conf_options = config.get(
                'irs', 'nfs_mount_options').replace(' ', '')
            if (frozenset(conf_options.split(',')) !=
                    frozenset(NFSConnection.DEFAULT_OPTIONS)):
                logging.warning("Using deprecated nfs_mount_options from"
                                " vdsm.conf to mount %s: %s",
                                conDict.get('connection', '(unknown)'),
                                conf_options)
                return PosixFsConnectionParameters(
                    conDict["id"],
                    conDict.get('connection', None),
                    'nfs',
                    conf_options)
        return None

    typeName = CON_TYPE_ID_2_CON_TYPE[conTypeId]
    if typeName == 'localfs':
        params = LocaFsConnectionParameters(
            conDict["id"],
            conDict.get('connection', None))
    elif typeName == 'nfs':
        params = tryDeprecatedNfsParams(conDict)
        if params is not None:
            # Hack to support vdsm.conf nfs_mount_options
            typeName = 'posixfs'
        else:
            version = conDict.get('protocol_version', "3")
            version = str(version)
            if version == "auto":
                version = None

            params = NfsConnectionParameters(
                conDict["id"],
                conDict.get('connection', None),
                getIntParam(conDict, 'retrans', None),
                getIntParam(conDict, 'timeout', None),
                version,
                conDict.get('mnt_options', None))
    elif typeName == 'posixfs':
        params = PosixFsConnectionParameters(
            conDict["id"],
            conDict.get('connection', None),
            conDict.get('vfs_type', None),
            conDict.get('mnt_options', None))
    elif typeName == 'glusterfs':
        params = GlusterFsConnectionParameters(
            conDict["id"],
            conDict.get('connection', None),
            conDict.get('vfs_type', None),
            conDict.get('mnt_options', None))
    elif typeName == 'iscsi':
        portal = iscsi.IscsiPortal(
            conDict.get('connection', None),
            int(conDict.get('port', None)))
        tpgt = int(conDict.get('tpgt', iscsi.DEFAULT_TPGT))

        target = iscsi.IscsiTarget(portal, tpgt, conDict.get('iqn', None))

        iface = iscsi.resolveIscsiIface(conDict.get('ifaceName', None),
                                        conDict.get('initiatorName', None),
                                        conDict.get('netIfaceName', None))

        # NOTE: ChapCredentials must match the way we initialize username and
        # password when reading session info in iscsi.readSessionInfo(). Empty
        # or missing username or password are stored as None.

        username = conDict.get('user')
        if not username:
            username = None
        password = conDict.get('password')
        if not getattr(password, "value", None):
            password = None
        cred = None
        if username or password:
            cred = iscsi.ChapCredentials(username, password)

        params = IscsiConnectionParameters(
            conDict["id"],
            target,
            iface,
            cred)
    elif typeName == 'fcp':
        params = FcpConnectionParameters(conDict["id"])
    else:
        raise se.StorageServerActionError()

    return ConnectionInfo(typeName, params)
