#
# Copyright 2009-2012 Red Hat, Inc.
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
import os
import glob
import logging
import re
import errno
import time
from collections import namedtuple

from vdsm import constants
import misc
from vdsm.config import config
import devicemapper
from threading import RLock

import iscsiadm
import supervdsm

IscsiPortal = namedtuple("IscsiPortal", "hostname, port")
IscsiTarget = namedtuple("IscsiTarget", "portal, tpgt, iqn")

ISCSI_DEFAULT_PORT = 3260
SCAN_PATTERN = "/sys/class/scsi_host/host*/scan"

IscsiSession = namedtuple("IscsiSession", "id, iface, target, credentials")

_iscsiadmTransactionLock = RLock()

log = logging.getLogger('Storage.ISCSI')


def getDevIscsiInfo(dev):
    """
    Reports the iSCSI parameters of the given device 'dev'
    Arguments:
        dev - for example 'sdf'
    Returns:
        IscsiSession

    """

    device = os.path.realpath(os.path.join("/sys/block", dev, "device"))
    if os.path.exists(device) and devIsiSCSI(dev):
        sessiondir = os.path.realpath(os.path.join(device, "../.."))
        sessionID = int(os.path.basename(sessiondir)[7:])
        return getSessionInfo(sessionID)

    #FIXME: raise exception instead of returning an empty object
    return IscsiSession(0, IscsiInterface(""),
                        IscsiTarget(IscsiPortal("", 0), 0, ""), None)


def getSessionInfo(sessionID):
    return supervdsm.getProxy().readSessionInfo(sessionID)


def readSessionInfo(sessionID):
    sessionName = "session%d" % sessionID
    connectionName = "connection%d:0" % sessionID
    iscsi_session = "/sys/class/iscsi_session/%s/" % sessionName
    iscsi_connection = "/sys/class/iscsi_connection/%s/" % connectionName
    if not os.path.isdir(iscsi_session) or not os.path.isdir(iscsi_connection):
        raise OSError(errno.ENOENT, "No such session")

    targetname = os.path.join(iscsi_session, "targetname")
    iface = os.path.join(iscsi_session, "ifacename")
    tpgt = os.path.join(iscsi_session, "tpgt")

    user = os.path.join(iscsi_session, "username")
    passwd = os.path.join(iscsi_session, "password")

    paddr = os.path.join(iscsi_connection, "persistent_address")
    pport = os.path.join(iscsi_connection, "persistent_port")

    res = []
    for fname in (targetname, tpgt, user, passwd, paddr, pport, iface):
        try:
            with open(fname, "r") as f:
                res.append(f.read().strip())
        except (OSError, IOError):
            res.append("")

    iqn, tpgt, username, password, ip, port, iface = res
    port = int(port)
    tpgt = int(tpgt)

    # Fix username and password if needed (iscsi reports empty user/password
    # as "<NULL>" (RHEL5) or "(null)" (RHEL6)
    if username in ["<NULL>", "(null)"]:
        username = None
    if password in ["<NULL>", "(null)"]:
        password = None

    iface = IscsiInterface(iface)
    portal = IscsiPortal(ip, port)
    target = IscsiTarget(portal, tpgt, iqn)
    cred = None
    #FIXME: Don't just assume CHAP
    if username or password:
        cred = ChapCredentials(username, password)

    return IscsiSession(sessionID, iface, target, cred)


def addIscsiNode(iface, target, credentials=None):
    targetName = target.iqn
    portalStr = "%s:%d" % (target.portal.hostname, target.portal.port)
    with _iscsiadmTransactionLock:
        iscsiadm.node_new(iface.name, portalStr, targetName)
        try:
            if credentials is not None:
                for key, value in credentials.getIscsiadmOptions():
                    key = "node.session." + key
                    iscsiadm.node_update(iface.name, portalStr, targetName,
                                         key, value, hideValue=True)

            iscsiadm.node_login(iface.name, portalStr, targetName)

            iscsiadm.node_update(iface.name, portalStr, targetName,
                                 "node.startup", "manual")
        except:
            removeIscsiNode(iface, target)
            raise


def removeIscsiNode(iface, target):
    targetName = target.iqn
    portalStr = "%s:%d" % (target.portal.hostname, target.portal.port)
    with _iscsiadmTransactionLock:
        try:
            iscsiadm.node_disconnect(iface.name, portalStr, targetName)
        except iscsiadm.IscsiSessionNotFound:
            pass

        iscsiadm.node_delete(iface.name, portalStr, targetName)


def addIscsiPortal(iface, portal, credentials=None):
    discoverType = "sendtargets"
    portalStr = "%s:%d" % (portal.hostname, portal.port)

    with _iscsiadmTransactionLock:
        iscsiadm.discoverydb_new(discoverType, iface.name, portalStr)

        # NOTE: We are not taking for granted that iscsiadm is not going to
        #       write the database when the discovery fails, therefore we try
        #       to set the node startup to manual anyway.
        try:
            iscsiadm.discoverydb_update(discoverType, iface.name, portalStr,
                                        "node.startup", "manual")
        except:
            # this is just to be polite we don't really care
            pass

        try:
            # Push credentials
            if credentials is not None:
                for key, value in credentials.getIscsiadmOptions():
                    key = "discovery.sendtargets." + key
                    iscsiadm.discoverydb_update(discoverType, iface.name,
                                                portalStr, key, value,
                                                hideValue=True)

        except:
            deleteIscsiPortal(iface, portal)
            raise


def deleteIscsiPortal(iface, portal):
    discoverType = "sendtargets"
    portalStr = "%s:%d" % (portal.hostname, portal.port)
    iscsiadm.discoverydb_delete(discoverType, iface.name, portalStr)


def discoverSendTargets(iface, portal, credentials=None):
    # Because proper discovery actually has to clear the DB having multiple
    # discoveries at once will cause unpredictable results
    discoverType = "sendtargets"
    portalStr = "%s:%d" % (portal.hostname, portal.port)

    with _iscsiadmTransactionLock:
        addIscsiPortal(iface, portal, credentials)
        try:
            targets = iscsiadm.discoverydb_discover(discoverType, iface.name,
                                                    portalStr)
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
        if self.password is not None:
            res.append(("auth.password", self.password))

        return res

    def __eq__(self, other):
        return hash(self) == hash(other)

    def __hash__(self):
        return hash(self.__class__) ^ hash(self.username) ^ hash(self.password)


# Technically there are a lot more interface properties but VDSM doesn't
# support them at the moment
class IscsiInterface(object):

    _fields = {
        "name": ('iface.iscsi_ifacename', 'rw'),
        'transport': ("iface.transport_name", 'r'),
        'hardwareAddress': ("iface.hwaddress", 'rw'),
        'ipAddress': ('iface.ipaddress', 'rw'),
        'initiatorName': ('iface.initiatorname', 'rw')
    }

    def __getattr__(self, name):
        if name in ("_conf", "_fields", "_loaded"):
            return object.__getattr__(self, name)

        if name not in self._fields:
            raise AttributeError(name)

        key, mode = self._fields[name]

        if not "r" in mode:
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
        if not "w" in mode:
            raise AttributeError(name)

        self._conf[key] = value

    def __init__(self, name, hardwareAddress=None, ipAddress=None,
                 initiatorName=None):

        # Only new tcp interfaces are supported for now
        self._conf = {'iface.transport_name': 'tcp'}

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
        for key, value in self._conf.iteritems():
            if key == 'iface.iscsi_ifacename':
                continue

            iscsiadm.iface_update(self.name, key, value)

    def delete(self):
        iscsiadm.iface_delete(self.name)

    def _load(self):
        conf = iscsiadm.iface_info(self.name)
        conf.update(self._conf)
        self._conf = conf

    def __repr__(self):
        return "<IscsiInterface name='%s' transport='%s'>" % (self.name,
                                                              self.transport)


def iterateIscsiInterfaces():
    names = iscsiadm.iface_list()
    for name in names:
        yield IscsiInterface(name)


@misc.samplingmethod
def rescan():
    try:
        iscsiadm.session_rescan()
    except iscsiadm.IscsiError:
        pass


@misc.samplingmethod
def forceIScsiScan():
    processes = []
    minTimeout = config.getint('irs', 'scsi_rescan_minimal_timeout')
    maxTimeout = config.getint('irs', 'scsi_rescan_maximal_timeout')
    for hba in glob.glob(SCAN_PATTERN):
        cmd = [constants.EXT_DD, 'of=' + hba]
        p = misc.execCmd(cmd, sudo=False, sync=False)
        try:
            p.stdin.write("- - -")
            p.stdin.flush()
            p.stdin.close()
        except OSError as e:
            if p.wait(0) is False:
                log.error("pid %s still running", p.pid)
            log.warning("Error in rescan of hba:%s with returncode:%s and "
                        "error message: %s", hba, p.returncode,
                        p.stderr.read(1000))
            if e.errno != errno.EPIPE:
                raise
            else:
                log.warning("Ignoring error in rescan of hba %s: ",
                            hba, exc_info=True)
                continue
        processes.append((hba, p))
    if (minTimeout > maxTimeout or minTimeout < 0):
        minTimeout = 2
        maxTimeout = 30
        log.warning("One of the following configuration arguments has an "
                    "illegal value: scsi_rescan_minimal_timeout or "
                    "scsi_rescan_maximal_timeout. Set to %s and %s seconds "
                    "respectively.", minTimeout, maxTimeout)
    log.debug("Performing SCSI scan, this will take up to %s seconds",
              maxTimeout)
    time.sleep(minTimeout)
    for i in xrange(maxTimeout - minTimeout):
        for p in processes[:]:
            (hba, proc) = p
            if proc.wait(0):
                if proc.returncode != 0:
                    log.warning('returncode for: %s is: %s', hba,
                                proc.returncode)
                processes.remove(p)
        if not processes:
            break
        else:
            time.sleep(1)
    else:
        log.warning("Still waiting for scsi scan of hbas: %s",
                    tuple(hba for p in processes))


def devIsiSCSI(dev):
    hostdir = os.path.realpath(os.path.join("/sys/block", dev,
                                            "device/../../.."))
    host = os.path.basename(hostdir)
    iscsi_host = os.path.join(hostdir, constants.STRG_ISCSI_HOST, host)
    scsi_host = os.path.join(hostdir, constants.STRG_SCSI_HOST, host)
    proc_name = os.path.join(scsi_host, "proc_name")
    return (os.path.exists(iscsi_host) and os.path.exists(proc_name))


def getiScsiTarget(dev):
    # FIXME: Use the new target object instead of a string
    device = os.path.realpath(os.path.join("/sys/block", dev, "device"))
    sessiondir = os.path.realpath(os.path.join(device, "../.."))
    session = os.path.basename(sessiondir)
    iscsi_session = os.path.join(sessiondir,
                                 constants.STRG_ISCSI_SESSION + session)
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
    #FIXME : Should throw exception on error
    sessionID = int(sessionID)
    try:
        iscsiadm.session_logout(sessionID)
    except iscsiadm.IscsiError as e:
        return e[0]

    return 0
