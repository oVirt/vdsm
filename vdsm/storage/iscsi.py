#
# Copyright 2009-2011 Red Hat, Inc.
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

"""
iSCSI service module. Provides helper functions to interact with iscsiadm
facility
"""
import os.path
import glob
import logging
import socket
import re
import sys

import constants
import misc
import storage_exception as se
import devicemapper

SENDTARGETS_DISCOVERY = [constants.EXT_ISCSIADM, "-m", "discoverydb", "-t", "sendtargets"]
ISCSIADM_NODE = [constants.EXT_ISCSIADM, "-m", "node"]
ISCSIADM_IFACE = [constants.EXT_ISCSIADM, "-m", "iface"]
ISCSI_DEFAULT_PORT = "3260"
MANUAL_STARTUP = ["-o", "update", "-n", "node.startup", "-v", "manual"]
NEW_REC = ["-o", "new"]
AUTH_CHAP = ["-o", "update", "-n", "discovery.sendtargets.auth.authmethod", "-v", "CHAP"]
AUTH_USER = ["-o", "update", "-n", "discovery.sendtargets.auth.username", "-v"]
AUTH_PASS = ["-o", "update", "-n", "discovery.sendtargets.auth.password", "-v"]
LOGIN_AUTH_CHAP = ["-o", "update", "-n", "node.session.auth.authmethod", "-v", "CHAP"]
LOGIN_AUTH_USER = ["-o", "update", "-n", "node.session.auth.username", "-v"]
LOGIN_AUTH_PASS = ["-o", "update", "-n", "node.session.auth.password", "-v"]
AUTH_EXEC_DISCOVER = ["--discover"]
SCAN_PATTERN = "/sys/class/scsi_host/host*/scan"

# iscsiadm exit statuses
ISCSI_ERR_SESS_EXISTS = 15
ISCSI_ERR_LOGIN_AUTH_FAILED = 24

log = logging.getLogger('Storage.iScsi')


def validateiSCSIParams(ip, port, username=None, password=None):
    if not ip:
        raise se.InvalidParameterException("IP", ip)
    else:
        try:
            ip = socket.gethostbyname(ip)
        except socket.gaierror:
            raise se.InvalidIpAddress(ip)
    if not port:
        raise se.InvalidParameterException("Port", port)

    return (ip, port, username, password)


def getiSCSIifaces():
    """
    Collect the dictionary of all the existing iSCSI ifaces
    (including the default and hw/fw)
    """
    rc, out, err = misc.execCmd(ISCSIADM_IFACE)
    if rc != 0:
        raise se.iSCSIifaceError()
    ifaces = dict()
    for i in out:
        iface, params = i.split()
        params = params.split(',')
        ifaces[iface] = params

    return ifaces


def addiSCSIiface(initiator):
    """
    Create the iSCSI iface with the given initiator name.
    For the sake of simplicity the iface is created with the same name
    as an initiator. It makes the bookkeeping trivial.
    """
    cmd = ISCSIADM_IFACE + NEW_REC + ["-I", initiator]
    rc, out, err = misc.execCmd(cmd)
    if rc != 0:
        raise se.iSCSIifaceError()

    cmd = ISCSIADM_IFACE + ["-o", "update", "-I", initiator, "-n",
        "iface.initiatorname", "-v", initiator]
    rc, out, err = misc.execCmd(cmd)
    if rc != 0:
        raise se.iSCSIifaceError()


def remiSCSIiface(initiator):
    """
    Remove the iface with the given initiator name.
    """
    cmd = ISCSIADM_IFACE + ["-o", "delete", "-I", initiator]
    rc, out, err = misc.execCmd(cmd)
    if rc != 0:
        raise se.iSCSIifaceError()


def addiSCSIPortal(ip, port, initiator, username=None, password=None):
    """
    Attempts SendTarget discovery at the portal ip:port.
    """

    if port == "":
        port = ISCSI_DEFAULT_PORT

    ip, port, username, password = validateiSCSIParams(ip, port, username,
        password)
    portal = "%s:%s" % (ip, port)

    args = ["-p", portal]

    if initiator:
        if initiator not in getiSCSIifaces():
            addiSCSIiface(initiator)
        args += ["-I", initiator]

    cmd = SENDTARGETS_DISCOVERY + args

    if username or password:
        _configureAuthInformation(cmd, username, password)

    cmd.extend(AUTH_EXEC_DISCOVER)

    # Discovering the targets and setting them to "manual".
    # NOTE: We are not taking for granted that iscsiadm is not going to write
    #       the database when the discovery fails, therefore we try to set the
    #       node startup to manual anyway.
    (dRet, dOut, dErr) = misc.execCmd(cmd)
    (mRet, mOut, mErr) = misc.execCmd(ISCSIADM_NODE + args + MANUAL_STARTUP)

    # Even if the discovery failed it's important to log that we tried to set
    # the node startup to manual and we failed.
    if mRet != 0:
        log.error("Could not set the iscsi node.startup to manual")

    # Raise an exception if discovering the targets failed
    if dRet != 0:
        raise se.iSCSIDiscoveryError(portal, dErr)

    return dRet, dOut

def remiSCSIPortal(ip, port):
    """
    Removes iSCSI portal from discovery list
    """

    if port == "":
        port = ISCSI_DEFAULT_PORT

    ip, port, username, password = validateiSCSIParams(ip, port)
    portal = "%s:%s" % (ip, port)

    cmd = [constants.EXT_ISCSIADM, "-m", "discovery", "-o", "delete", "-p", portal]
    rc = misc.execCmd(cmd)[0]
    if rc != 0:
        raise se.RemoveiSCSIPortalError(portal)


def discoverSendTargets(ip, port, username=None, password=None):
    """
    Perform iSCSI SendTargets discovery for a given iSCSI portal
    """
    ip, port, username, password = validateiSCSIParams(ip, port, username,
        password)
    rc, out = addiSCSIPortal(ip, port, None, username, password)
    targets = [target for target in out]

    # Ideally we would remove the discovery record right away,
    # however there is some subtle issue with tpgt if I add
    # the node manually via iscsiadm -m node - it is being
    # recorded as -1 inside the node record. the record itself,
    # nonetheless, doesn't bear any tpgt in its name.
    # That causes conflicts later.

    #remiSCSIPortal(ip, port)
    return targets

def _configureAuthInformation(cmd, usr, passwd):
    cmdList = [(cmd + NEW_REC, None), # Create a new record
               (cmd + AUTH_CHAP, None), # Set auth method to CHAP
               (cmd + AUTH_PASS + [passwd], cmd + AUTH_PASS + ["******"])] # Set password
    if usr:
        cmdList.append((cmd + AUTH_USER + [usr], None)) # Set username

    for cmd in cmdList:
        if cmd == None:
            continue
        (rc, out, err) = misc.execCmd(cmd[0],printable=cmd[1])
        if rc != 0:
            raise se.SetiSCSIAuthError(cmd[0])

def addiSCSINode(ip, port, iqn, tpgt, initiator, username=None, password=None):
    """
    Add a specific node/iSCSI target
    """
    ip, port, username, password = validateiSCSIParams(ip, port, username,
        password)
    if port == "":
        port = ISCSI_DEFAULT_PORT

    portal = "%s:%s" % (ip, port)

    try:
        addiSCSIPortal(ip, port, initiator, username, password)[0]

        cmdt = ISCSIADM_NODE + ["-T", iqn]

        if initiator:
            cmdt += ["-I", initiator]

        # If username or password exists assume CHAP authentication is required
        if username or password:
            # Set authentication type
            cmd = cmdt + LOGIN_AUTH_CHAP
            rc = misc.execCmd(cmd)[0]
            if rc != 0:
                raise se.SetiSCSIAuthError(portal)

            if username:
                # Set username
                cmd = cmdt + LOGIN_AUTH_USER + [username]
                rc = misc.execCmd(cmd)[0]
                if rc != 0:
                    raise se.SetiSCSIUsernameError(portal)

            # Set password
            cmd = cmdt + LOGIN_AUTH_PASS
            rc = misc.execCmd(cmd + [password], printable=cmd + ["******"])[0]
            if rc != 0:
                raise se.SetiSCSIPasswdError(portal)

        # Finally instruct the iscsi initiator to login to the target
        cmd = cmdt + ["-l", "-p", portal]
        rc = misc.execCmd(cmd)[0]
        if rc == ISCSI_ERR_LOGIN_AUTH_FAILED:
            raise se.iSCSILoginAuthError(portal)
        elif rc not in (0, ISCSI_ERR_SESS_EXISTS):
            raise se.iSCSILoginError(portal)

    except se.StorageException:
        exc_class, exc, tb = sys.exc_info()
        # Do not try to disconnect - we may remove live node!
        try:
            remiSCSINode(ip, port, iqn, tpgt, username, password, logout=False)
        except Exception:
            log.error("Could not remove iscsi node", exc_info=True)

        raise exc_class, exc, tb


def remiSCSINode(ip, port, iqn, tpgt, username=None, password=None, logout=True):
    """
    Remove a specific node/iSCSI target
    """
    ip, port, username, password = validateiSCSIParams(ip, port, username,
        password)
    if port == "":
        port = ISCSI_DEFAULT_PORT

    portal = "%s:%s" % (ip, port)

    if logout:
        cmd = ISCSIADM_NODE + ["-T", iqn, "-p", portal, "-u"]
        rc = misc.execCmd(cmd)[0]
        if rc:
            raise se.iSCSILogoutError(portal)

    # FIXME: should we check if logout succeeds?
    cmd = ISCSIADM_NODE + ["-o", "delete", "-T", iqn, "-p", portal]
    rc = misc.execCmd(cmd)[0]
    if rc:
        raise se.RemoveiSCSINodeError(portal)


def discoveriSNS():
    pass

def addiSCSIiSNS():
    pass

def forceIScsiScan():
    for host in glob.glob(SCAN_PATTERN):
        try:
            with open(host, "w") as f:
                f.write("- - -")
        except Exception:
            # Ignore exception, there is nothing intelligent we can do about it
            log.warning("Failed to rescan host %s", host, exc_info=True)

def devIsiSCSI(dev):
    hostdir = os.path.realpath(os.path.join("/sys/block", dev, "device/../../.."))
    host = os.path.basename(hostdir)
    iscsi_host = os.path.join(hostdir, constants.STRG_ISCSI_HOST, host)
    scsi_host = os.path.join(hostdir, constants.STRG_SCSI_HOST, host)
    proc_name = os.path.join(scsi_host, "proc_name")
    return (os.path.exists(iscsi_host) and os.path.exists(proc_name))

def getiScsiTarget(dev):
    device = os.path.realpath(os.path.join("/sys/block", dev, "device"))
    sessiondir = os.path.realpath(os.path.join(device, "../.."))
    session = os.path.basename(sessiondir)
    iscsi_session = os.path.join(sessiondir, constants.STRG_ISCSI_SESSION + session)
    with open(os.path.join(iscsi_session, "targetname")) as f:
        return f.readline().strip()

def getiScsiSession(dev):
    device = os.path.realpath(os.path.join("/sys/block", dev, "device"))
    sessiondir = os.path.realpath(os.path.join(device, "../.."))
    session = os.path.basename(sessiondir)
    return session

def getdeviSCSIinfo(dev):
    """
    Reports the iSCSI parameters of the given device 'dev'
    Arguments:
        dev - for example 'sdf'
    Returns:
        (ip, port, iqn, num, username, password)

    """

    ip = port = iqn = num = username = password = initiator = ""

    device = os.path.realpath(os.path.join("/sys/block", dev, "device"))
    if os.path.exists(device) and devIsiSCSI(dev):
        sessiondir = os.path.realpath(os.path.join(device, "../.."))
        session = os.path.basename(sessiondir)
        iscsi_session = os.path.join(sessiondir, constants.STRG_ISCSI_SESSION + session)

        targetname = os.path.join(iscsi_session, "targetname")
        initiator = os.path.join(iscsi_session, "initiatorname")
        tpgt = os.path.join(iscsi_session, "tpgt")
        user = os.path.join(iscsi_session, "username")
        passwd = os.path.join(iscsi_session, "password")

        conn_pattern = os.path.join(sessiondir, "connection*")
        connectiondir = glob.glob(conn_pattern)[0]
        connection = os.path.basename(connectiondir)
        iscsi_connection = os.path.join(connectiondir,
            constants.STRG_ISCSI_CONNECION + connection)
        paddr = os.path.join(iscsi_connection, "persistent_address")
        pport = os.path.join(iscsi_connection, "persistent_port")

        res = []
        for fname in (targetname, tpgt, user, passwd, paddr, pport, initiator):
            try:
                with open(fname, "r") as f:
                    res.append(f.read().strip())
            except (OSError, IOError):
                res.append("")

        iqn, num, username, password, ip, port, initiator = res

    # Fix username and password if needed (iscsi reports empty user/password
    # as "<NULL>" (RHEL5) or "(null)" (RHEL6)
    if username in ["<NULL>", "(null)"]:
        username = ""
    if password in ["<NULL>", "(null)"]:
        password = ""

    info = dict(connection=ip, port=port, iqn=iqn, portal=num,
        user=username, password=password, initiator=initiator)

    return info

@misc.samplingmethod
def rescan():
    cmd = [constants.EXT_ISCSIADM, "-m", "session", "-R"]
    misc.execCmd(cmd)

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

        for slave in devicemapper.getSlaves():
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
    sessionID = int(sessionID)
    rc, out, err = misc.execCmd([constants.EXT_ISCSIADM, "-m", "session", "-r", str(sessionID), "-u"])
    return rc

