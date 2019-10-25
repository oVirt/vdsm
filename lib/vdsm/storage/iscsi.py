#
# Copyright 2009-2017 Red Hat, Inc.
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

"""
iSCSI service module. Provides helper functions to interact with iscsiadm
facility
"""

from __future__ import absolute_import

import errno
import glob
import logging
import os
import re

from collections import namedtuple
from threading import RLock

import six

from vdsm import utils
from vdsm.config import config
from vdsm.common import supervdsm
from vdsm.common.network.address import hosttail_join
from vdsm.network.netinfo.routes import getRouteDeviceTo
from vdsm.storage import devicemapper
from vdsm.storage import iscsiadm
from vdsm.storage import misc
from vdsm.storage import sysfs


class IscsiPortal(namedtuple("IscsiPortal", "hostname, port")):
    """
    Represents transport (TCP) address like defined in rfc 3721 or
    Network Portal of rfc 3720.
    """
    def __str__(self):
        return hosttail_join(self.hostname, str(self.port))

    def is_ipv6(self):
        return ":" in self.hostname


class IscsiTarget(namedtuple("IscsiTarget", "portal, tpgt, iqn")):
    """
    Represents the iSCSI Address like defined in rfc 3721 or
    the target record of rfc 3720 Appendix D.
    """
    def __str__(self):
        return "%s %s" % (self.address, self.iqn)

    @property
    def address(self):
        """
        Gets the TargetAddress like defined in rfc 3720 section 12.8.
        """
        return "%s,%s" % (self.portal, self.tpgt)


DEFAULT_TPGT = 1

IscsiSession = namedtuple("IscsiSession", "id, iface, target, credentials")

_iscsiadmTransactionLock = RLock()

log = logging.getLogger('storage.ISCSI')


def getDevIscsiSessionId(dev):
    device = os.path.realpath(os.path.join("/sys", "block", dev, "device"))
    if not os.path.exists(device):
        return None
    # Path format example:
    #   device  = "/sys/devices/platform/host1/session1/target1:0:0/1:0:0:1"
    #   session = "/sys/devices/platform/host1/session1"
    session = os.path.realpath(os.path.join(device, "../.."))
    # Returning the session id from the session string (e.g. "session1")
    return int(os.path.basename(session)[7:])


def getDevIscsiInfo(dev):
    """
    Reports the iSCSI parameters of the given device 'dev'
    Arguments:
        dev - for example 'sdf'
    Returns:
        IscsiSession

    """

    if devIsiSCSI(dev):
        return getSessionInfo(getDevIscsiSessionId(dev))

    # FIXME: raise exception instead of returning an empty object
    return IscsiSession(0, IscsiInterface(""),
                        IscsiTarget(IscsiPortal("", 0), 0, ""), None)


def getSessionInfo(sessionID):
    return supervdsm.getProxy().readSessionInfo(sessionID)


def getIscsiSessionPath(sessionId):
    return os.path.join("/sys", "class", "iscsi_session",
                        "session%d" % sessionId)


def getIscsiConnectionPath(sessionId):
    return os.path.join("/sys", "class", "iscsi_connection",
                        "connection%d:0" % sessionId)


def getIscsiHostPath(sessionID):
    """
    Report the iSCSI host path of the given iSCSI session to be further
    used to retrieve iface.net_ifacename (netdev) in use for the session.
    Arguments:
        sessionID - the iSCSI session ID.
    Returns:
        - iSCSI host path - e.g. '/sys/class/iscsi_host/host17/'
    """
    path = os.path.realpath(getIscsiSessionPath(sessionID))
    if not os.path.exists(path):
        raise OSError(errno.ENOENT, "No such session %r" % sessionID)

    # Session path depends on the iSCSI implementation:
    # Hardware iSCSI:
    #   /sys/devices/pci*/*/*/host0/session7/iscsi_session/session7
    # Software iSCSI:
    #   /sys/devices/platform/host5/session8/iscsi_session/session8
    host = path.rsplit(os.sep, 4)[-4]
    if not host.startswith('host'):
        raise RuntimeError("Unexpected session path %r" % path)

    return '/sys/class/iscsi_host/' + host


def readSessionInfo(sessionID):
    iscsi_session = getIscsiSessionPath(sessionID)
    iscsi_connection = getIscsiConnectionPath(sessionID)

    if not os.path.isdir(iscsi_session) or not os.path.isdir(iscsi_connection):
        raise OSError(errno.ENOENT, "No such session")

    iqn = sysfs.read(os.path.join(iscsi_session, "targetname"), default="")
    iface = sysfs.read(os.path.join(iscsi_session, "ifacename"), default="")
    tpgt = sysfs.read_int(os.path.join(iscsi_session, "tpgt"))
    username = sysfs.read(os.path.join(iscsi_session, "username"), default="")
    password = sysfs.read_password(os.path.join(iscsi_session, "password"),
                                   default="")
    ip = sysfs.read(os.path.join(iscsi_connection, "persistent_address"),
                    default="")
    port = sysfs.read_int(os.path.join(iscsi_connection, "persistent_port"))

    # iscsi_host is available only when the session exists.
    iscsi_host = getIscsiHostPath(sessionID)
    netdev = sysfs.read(os.path.join(iscsi_host, "netdev"), default="")
    if netdev in ["<NULL>", "(null)"]:
        netdev = None

    iface = IscsiInterface(iface, netIfaceName=netdev)
    portal = IscsiPortal(ip, port)
    target = IscsiTarget(portal, tpgt, iqn)

    # NOTE: ChapCredentials must match the way we initialize username and
    # password when receiving request from engine in
    # hsm._connectionDict2ConnectionInfo().
    # iscsi reports empty user/password as "<NULL>" (RHEL5) or "(null)"
    # (RHEL6);  empty values are stored as None.

    if username in ["<NULL>", "(null)", ""]:
        username = None
    if password.value in ["<NULL>", "(null)", ""]:
        password = None
    cred = None
    if username or password:
        cred = ChapCredentials(username, password)

    return IscsiSession(sessionID, iface, target, cred)


def addIscsiNode(iface, target, credentials=None):
    # There are 2 formats for an iSCSI node record. An old style format where
    # the path is /var/lib/iscsi/nodes/{target}/{portal} and a new style format
    # where the portal path is a directory containing a record file for each
    # bounded iface. Explicitly specifying tpgt on iSCSI login imposes creation
    # of the node record in the new style format which enables to access a
    # portal through multiple ifaces for multipathing.
    with _iscsiadmTransactionLock:
        iscsiadm.node_new(iface.name, target.address, target.iqn)
        try:
            if credentials is not None:
                for key, value in credentials.getIscsiadmOptions():
                    key = "node.session." + key
                    iscsiadm.node_update(iface.name, target.address,
                                         target.iqn, key, value)

            setRpFilterIfNeeded(iface.netIfaceName, target.portal.hostname,
                                True)

            iscsiadm.node_login(iface.name, target.address, target.iqn)

            iscsiadm.node_update(iface.name, target.address, target.iqn,
                                 "node.startup", "manual")
        except:
            removeIscsiNode(iface, target)
            raise


def removeIscsiNode(iface, target):
    # Basically this command deleting a node record (see addIscsiNode).
    # Once we create a record in the new style format by specifying a tpgt,
    # we delete it in the same way.
    with _iscsiadmTransactionLock:
        try:
            iscsiadm.node_disconnect(iface.name, target.address, target.iqn)
        except iscsiadm.IscsiSessionNotFound:
            pass

        iscsiadm.node_delete(iface.name, target.address, target.iqn)
        setRpFilterIfNeeded(iface.netIfaceName, target.portal.hostname, False)


def addIscsiPortal(iface, portal, credentials=None):
    discoverType = "sendtargets"

    with _iscsiadmTransactionLock:
        iscsiadm.discoverydb_new(discoverType, iface.name, str(portal))

        try:
            # Push credentials
            if credentials is not None:
                for key, value in credentials.getIscsiadmOptions():
                    key = "discovery.sendtargets." + key
                    iscsiadm.discoverydb_update(discoverType, iface.name,
                                                str(portal), key, value)

        except:
            deleteIscsiPortal(iface, portal)
            raise


def deleteIscsiPortal(iface, portal):
    discoverType = "sendtargets"
    iscsiadm.discoverydb_delete(discoverType, iface.name, str(portal))


def discoverSendTargets(iface, portal, credentials=None):
    # Because proper discovery actually has to clear the DB having multiple
    # discoveries at once will cause unpredictable results
    discoverType = "sendtargets"

    with _iscsiadmTransactionLock:
        addIscsiPortal(iface, portal, credentials)
        try:
            targets = iscsiadm.discoverydb_discover(discoverType, iface.name,
                                                    str(portal))
        finally:
            deleteIscsiPortal(iface, portal)

        res = []
        for ip, port, tpgt, iqn in targets:
            # Do not reuse the portal from argument as the portal that
            # returns here has it's IP resolved!
            res.append(IscsiTarget(IscsiPortal(ip, port), tpgt, iqn))

        return res


def iterateIscsiSessions():
    for sessionDir in glob.iglob("/sys/class/iscsi_session/session*"):
        sessionID = int(os.path.basename(sessionDir)[len("session"):])
        try:
            yield getSessionInfo(sessionID)
        except OSError as e:
            if e.errno != errno.ENOENT:
                raise

            continue


class ChapCredentials(object):
    def __init__(self, username=None, password=None):
        self.username = username
        self.password = password

    def getIscsiadmOptions(self):
        res = [("auth.authmethod", "CHAP")]
        if self.username is not None:
            res.append(("auth.username", self.username))
        # Note: password will be unprotected by the underlying command just
        # before running the command.
        if self.password is not None:
            res.append(("auth.password", self.password))

        return res

    def __eq__(self, other):
        return (self.__class__ == other.__class__ and
                self.username == other.username and
                self.password == other.password)

    def __ne__(self, other):
        return not self == other

    def __hash__(self):
        return hash((self.__class__, self.username, hash(self.password)))


# Technically there are a lot more interface properties but VDSM doesn't
# support them at the moment
class IscsiInterface(object):

    _fields = {
        "name": ('iface.iscsi_ifacename', 'rw'),
        'transport': ("iface.transport_name", 'r'),
        'hardwareAddress': ("iface.hwaddress", 'rw'),
        'ipAddress': ('iface.ipaddress', 'rw'),
        'initiatorName': ('iface.initiatorname', 'rw'),
        'netIfaceName': ('iface.net_ifacename', 'rw')
    }

    def __getattr__(self, name):
        if name not in self._fields:
            raise AttributeError(name)

        key, mode = self._fields[name]

        if "r" not in mode:
            raise AttributeError(name)

        value = self._conf[key]
        if value is None and not self._loaded:
            # Lazy load
            self._loaded = True
            self._load()
            return getattr(self, name)

        if value == "<empty>":
            return None

        return value

    def __setattr__(self, name, value):
        if name in ("_conf", "_fields", "_loaded"):
            return object.__setattr__(self, name, value)

        if name not in self._fields:
            raise AttributeError(name)

        key, mode = self._fields[name]
        if "w" not in mode:
            raise AttributeError(name)

        self._conf[key] = value

    def __init__(self, name, hardwareAddress=None, ipAddress=None,
                 initiatorName=None, netIfaceName=None):

        # Only new tcp interfaces are supported for now
        self._conf = {'iface.transport_name': 'tcp'}
        self._conf['iface.net_ifacename'] = netIfaceName

        self.name = name
        self._loaded = False

        if hardwareAddress:
            self.hardwareAddress = hardwareAddress

        if ipAddress:
            self.ipAddress = ipAddress

        if initiatorName:
            self.initiatorName = initiatorName

    @staticmethod
    def fromConf(conf):
        tmp = IscsiInterface("tmp")
        tmp._conf = conf
        return tmp

    def create(self):
        # If VDSM crashes while creating an interface a garbage interface will
        # still exist. If you have an idea how to go about making this atomic
        # please fix this.

        iscsiadm.iface_new(self.name)
        try:
            self.update()
        except:
            self.delete()
            raise

    def update(self):
        # If this fails mid operation we get a partially updated interface.
        # Suggestions are welcome.
        for key, value in six.iteritems(self._conf):
            if value is None or key == 'iface.iscsi_ifacename':
                continue

            iscsiadm.iface_update(self.name, key, value)

    def delete(self):
        iscsiadm.iface_delete(self.name)

    def _load(self):
        conf = iscsiadm.iface_info(self.name)
        conf.update(self._conf)
        self._conf = conf

    def __repr__(self):
        return "<IscsiInterface name='%s' transport='%s' netIfaceName='%s'>" \
            % (self.name, self.transport, self.netIfaceName)


def iterateIscsiInterfaces():
    for iface in iscsiadm.iface_list():
        yield IscsiInterface(iface.ifacename, netIfaceName=iface.net_ifacename)


@misc.samplingmethod
def rescan():
    timeout = config.getint('irs', 'scsi_rescan_maximal_timeout')
    log.info("Scanning iSCSI devices")
    try:
        with utils.stopwatch(
                "Scanning iSCSI devices", level=logging.INFO, log=log):
            iscsiadm.session_rescan(timeout=timeout)
    except iscsiadm.IscsiSessionError as e:
        log.error("Scan failed: %s", e)


def devIsiSCSI(dev):
    hostdir = os.path.realpath(os.path.join("/sys/block", dev,
                                            "device/../../.."))
    host = os.path.basename(hostdir)
    iscsi_host = os.path.join(hostdir, "iscsi_host/", host)
    scsi_host = os.path.join(hostdir, "scsi_host/", host)
    proc_name = os.path.join(scsi_host, "proc_name")

    if not os.path.exists(iscsi_host) or not os.path.exists(proc_name):
        return False

    # This second part of the validation is to make sure that if the
    # iscsi connection is handled by an HBA (e.g. qlogic in bz967605)
    # the device is reported as fiber channel to avoid unmanageable
    # commands (dis/connectStorageServer).
    session_id = getDevIscsiSessionId(dev)

    if session_id is None:
        return False

    iscsi_connection = getIscsiConnectionPath(session_id)
    pers_addr = os.path.join(iscsi_connection, "persistent_address")
    pers_port = os.path.join(iscsi_connection, "persistent_port")

    return os.path.exists(pers_addr) and os.path.exists(pers_port)


def getiScsiTarget(dev):
    # FIXME: Use the new target object instead of a string
    device = os.path.realpath(os.path.join("/sys/block", dev, "device"))
    sessiondir = os.path.realpath(os.path.join(device, "../.."))
    session = os.path.basename(sessiondir)
    iscsi_session = os.path.join(sessiondir,
                                 "iscsi_session/" + session)
    with open(os.path.join(iscsi_session, "targetname")) as f:
        return f.readline().strip()


def getiScsiSession(dev):
    # FIXME: Use the new session object instead of a string
    device = os.path.realpath(os.path.join("/sys/block", dev, "device"))
    sessiondir = os.path.realpath(os.path.join(device, "../.."))
    session = os.path.basename(sessiondir)
    return int(session[len('session'):])


def getDefaultInitiatorName():
    with open("/etc/iscsi/initiatorname.iscsi", "r") as f:
        return f.read().strip().split("=", 1)[1]


def findUnderlyingStorage(devPath):
    # make sure device exists and is accessible
    os.stat(devPath)
    sessions = []
    try:
        devs = devicemapper.getSlaves(os.path.basename(devPath))
    except:
        devs = [os.path.basename(devPath)]

    for realDev in devs:
        if not devicemapper.isVirtualDevice(realDev):
            sessions.append(getiScsiSession(realDev))
            continue

        for slave in devicemapper.getSlaves(realDev):
            sessions.extend(findUnderlyingStorage(os.path.join("/dev", slave)))

    return sessions


RE_SCSI_SESSION = re.compile(r"^[Ss]ession(\d+)$")


def disconnectFromUndelyingStorage(devPath):
    storageList = findUnderlyingStorage(devPath)
    res = []
    for target in storageList:
        m = RE_SCSI_SESSION.match(target)
        if not m:
            res.append(None)
            continue

        sessionID = m.groups()[0]
        res.append(disconnectiScsiSession(sessionID))

    return res


def disconnectiScsiSession(sessionID):
    # FIXME : Should throw exception on error
    sessionID = int(sessionID)
    sessionInfo = getSessionInfo(sessionID)
    try:
        iscsiadm.session_logout(sessionID)
    except iscsiadm.IscsiError as e:
        return e.args[0]

    netIfaceName = sessionInfo.iface.netIfaceName
    hostname = sessionInfo.target.portal.hostname
    setRpFilterIfNeeded(netIfaceName, hostname, False)

    return 0


def _sessionsUsingNetiface(netIfaceName):
    """ Return sessions using netIfaceName """
    for session in iterateIscsiSessions():
        if session.iface.netIfaceName == netIfaceName:
            yield session


def setRpFilterIfNeeded(netIfaceName, hostname, loose_mode):
    """
    Set rp_filter to loose or strict mode if there's no session using the
    netIfaceName device and it's not the device used by the OS to reach the
    'hostname'.
    loose mode is needed to allow multiple iSCSI connections in a multiple NIC
    per subnet configuration. strict mode is needed to avoid the security
    breach where an untrusted VM can DoS the host by sending it packets with
    spoofed random sources.

    Arguments:
        netIfaceName: the device used by the iSCSI session
        target: iSCSI target object cointaining the portal hostname
        loose_mode: boolean
    """
    if netIfaceName is None:
        log.debug("iface.net_ifacename not provided, skipping rp filter setup")
        return

    sessions = _sessionsUsingNetiface(netIfaceName)

    if not any(sessions) and netIfaceName != getRouteDeviceTo(hostname):
        if loose_mode:
            log.info("Setting loose mode rp_filter for device %r." %
                     netIfaceName)
            supervdsm.getProxy().set_rp_filter_loose(netIfaceName)
        else:
            log.info("Setting strict mode rp_filter for device %r." %
                     netIfaceName)
            supervdsm.getProxy().set_rp_filter_strict(netIfaceName)
