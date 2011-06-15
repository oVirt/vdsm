#
# Copyright 2009 Red Hat, Inc. and/or its affiliates.
#
# Licensed to you under the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.  See the files README and
# LICENSE_GPL_v2 which accompany this distribution.
#


"""
iSCSI service module. Provides helper functions to interact with iscsiadm
facility
"""
import os.path
import glob
import tempfile
import logging
import socket
import re

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
ISCSID_CONF = "/etc/iscsi/iscsid.conf"
ISCSID_CONF_TAG = "# RHEV REVISION 0"
SCAN_PATTERN = "/sys/class/scsi_host/host*/scan"
ISCSID_CONF_TEMPLATE = ISCSID_CONF_TAG + """
#
# Open-iSCSI default configuration.
# Could be located at /etc/iscsi/iscsid.conf or ~/.iscsid.conf
#
# Note: To set any of these values for a specific node/session run
# the iscsiadm --mode node --op command for the value. See the README
# and man page for iscsiadm for details on the --op command.
#

################
# iSNS settings
################
# Address of iSNS server
#isns.address = 192.168.0.1
#isns.port = 3205

#############################
# NIC/HBA and driver settings
#############################
# open-iscsi can create a session and bind it to a NIC/HBA.
# To set this up see the example iface config file.

#*****************
# Startup settings
#*****************

# To request that the iscsi initd scripts startup a session set to "automatic".
# node.startup = automatic
#
# To manually startup the session set to "manual". The default is automatic.
node.startup = manual

# *************
# CHAP Settings
# *************

# To enable CHAP authentication set node.session.auth.authmethod
# to CHAP. The default is None.
#node.session.auth.authmethod = CHAP

# To set a CHAP username and password for initiator
# authentication by the target(s), uncomment the following lines:
#node.session.auth.username = username
#node.session.auth.password = password

# To set a CHAP username and password for target(s)
# authentication by the initiator, uncomment the following lines:
#node.session.auth.username_in = username_in
#node.session.auth.password_in = password_in

# To enable CHAP authentication for a discovery session to the target
# set discovery.sendtargets.auth.authmethod to CHAP. The default is None.
#discovery.sendtargets.auth.authmethod = CHAP

# To set a discovery session CHAP username and password for the initiator
# authentication by the target(s), uncomment the following lines:
#discovery.sendtargets.auth.username = username
#discovery.sendtargets.auth.password = password

# To set a discovery session CHAP username and password for target(s)
# authentication by the initiator, uncomment the following lines:
#discovery.sendtargets.auth.username_in = username_in
#discovery.sendtargets.auth.password_in = password_in

# ********
# Timeouts
# ********
#
# See the iSCSI REAME's Advanced Configuration section for tips
# on setting timeouts when using multipath or doing root over iSCSI.
#
# To specify the length of time to wait for session re-establishment
# before failing SCSI commands back to the application when running
# the Linux SCSI Layer error handler, edit the line.
# The value is in seconds and the default is 120 seconds.
node.session.timeo.replacement_timeout = 120

# To specify the time to wait for login to complete, edit the line.
# The value is in seconds and the default is 15 seconds.
node.conn[0].timeo.login_timeout = 15

# To specify the time to wait for logout to complete, edit the line.
# The value is in seconds and the default is 15 seconds.
node.conn[0].timeo.logout_timeout = 15

# Time interval to wait for on connection before sending a ping.
node.conn[0].timeo.noop_out_interval = 5

# To specify the time to wait for a Nop-out response before failing
# the connection, edit this line. Failing the connection will
# cause IO to be failed back to the SCSI layer. If using dm-multipath
# this will cause the IO to be failed to the multipath layer.
node.conn[0].timeo.noop_out_timeout = 5

#******
# Retry
#******

# To speficy the number of times iscsiadm should retry a login
# to the target when we first login, modify the following line.
# The default is 4. Valid values are any integer value. This only
# affects the initial login. Setting it to a high value can slow
# down the iscsi service startup. Setting it to a low value can
# cause a session to not get logged into, if there are distuptions
# during startup or if the network is not ready at that time.
node.session.initial_login_retry_max = 4

################################
# session and device queue depth
################################

# To control how many commands the session will queue set
# node.session.cmds_max to an integer between 2 and 2048 that is also
# a power of 2. The default is 128.
node.session.cmds_max = 128

# To control the device's queue depth set node.session.queue_depth
# to a value between 1 and 128. The default is 32.
node.session.queue_depth = 32

#***************
# iSCSI settings
#***************

# To enable R2T flow control (i.e., the initiator must wait for an R2T
# command before sending any data), uncomment the following line:
#
#node.session.iscsi.InitialR2T = Yes
#
# To disable R2T flow control (i.e., the initiator has an implied
# initial R2T of "FirstBurstLength" at offset 0), uncomment the following line:
#
# The defaults is No.
node.session.iscsi.InitialR2T = No

#
# To disable immediate data (i.e., the initiator does not send
# unsolicited data with the iSCSI command PDU), uncomment the following line:
#
#node.session.iscsi.ImmediateData = No
#
# To enable immediate data (i.e., the initiator sends unsolicited data
# with the iSCSI command packet), uncomment the following line:
#
# The default is Yes
node.session.iscsi.ImmediateData = Yes

# To specify the maximum number of unsolicited data bytes the initiator
# can send in an iSCSI PDU to a target, edit the following line.
#
# The value is the number of bytes in the range of 512 to (2^24-1) and
# the default is 262144
node.session.iscsi.FirstBurstLength = 262144

# To specify the maximum SCSI payload that the initiator will negotiate
# with the target for, edit the following line.
#
# The value is the number of bytes in the range of 512 to (2^24-1) and
# the defauls it 16776192
node.session.iscsi.MaxBurstLength = 16776192

# To specify the maximum number of data bytes the initiator can receive
# in an iSCSI PDU from a target, edit the following line.
#
# The value is the number of bytes in the range of 512 to (2^24-1) and
# the default is 131072
node.conn[0].iscsi.MaxRecvDataSegmentLength = 131072


# To specify the maximum number of data bytes the initiator can receive
# in an iSCSI PDU from a target during a discovery session, edit the
# following line.
#
# The value is the number of bytes in the range of 512 to (2^24-1) and
# the default is 32768
#
discovery.sendtargets.iscsi.MaxRecvDataSegmentLength = 32768

# To allow the targets to control the setting of the digest checking,
# with the initiator requesting a preference of enabling the checking, uncommen
# the following lines (Data digests are not supported and on ppc/ppc64
# both header and data digests are not supported.):
#node.conn[0].iscsi.HeaderDigest = CRC32C,None
#
# To allow the targets to control the setting of the digest checking,
# with the initiator requesting a preference of disabling the checking,
# uncomment the following lines:
#node.conn[0].iscsi.HeaderDigest = None,CRC32C
#
# To enable CRC32C digest checking for the header and/or data part of
# iSCSI PDUs, uncomment the following lines:
#node.conn[0].iscsi.HeaderDigest = CRC32C
#
# To disable digest checking for the header and/or data part of
# iSCSI PDUs, uncomment the following lines:
#node.conn[0].iscsi.HeaderDigest = None
#
# The default is to never use DataDigests and to allow the target to control
# the setting of the HeaderDigest checking with the initiator requesting
# a preference of disabling the checking.
"""

log = logging.getLogger('Storage.iScsi')

def isConfigured():
    if os.path.exists(ISCSID_CONF):
        tagline = misc.readfileSUDO(ISCSID_CONF)[0]
        if ISCSID_CONF_TAG in tagline:
            return True

    return False


def setupiSCSI():
    """
    Set up the iSCSI daemon configuration to the known and
    supported state. The original configuration, if any, is saved
    """
    if os.path.exists(ISCSID_CONF):
        backup = ISCSID_CONF + ".orig"
        cmd = [constants.EXT_MV, ISCSID_CONF, backup]
        rc = misc.execCmd(cmd)[0]
        if rc != 0:
            raise se.iSCSISetupError("Backup original iscsid.conf file")
    f = tempfile.NamedTemporaryFile()
    f.write(ISCSID_CONF_TEMPLATE)
    f.flush()
    cmd = [constants.EXT_CP, f.name, ISCSID_CONF]
    rc = misc.execCmd(cmd)[0]
    if rc != 0:
        raise se.iSCSISetupError("Install new iscsid.conf file")
    # f close also removes file - so close must be called after copy
    f.close()

    cmd = [constants.EXT_SERVICE, "iscsid", "stop"]
    rc = misc.execCmd(cmd)[0]
    if rc != 0:
        raise se.iSCSISetupError("Stop iscsid service")

    cmd = [constants.EXT_SERVICE, "iscsid", "force-start"]
    rc = misc.execCmd(cmd)[0]
    if rc != 0:
        raise se.iSCSISetupError("Force-start iscsid service")


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
    cmd = ISCSIADM_IFACE + ["-o", "new", "-I", initiator]
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

    cmd = SENDTARGETS_DISCOVERY + ["-p", portal]

    if initiator:
        if initiator not in getiSCSIifaces():
            addiSCSIiface(initiator)
        cmd += ["-I", initiator]

    if username or password:
        _configureAuthInformation(cmd, username, password)

    cmd.extend(AUTH_EXEC_DISCOVER)

    (rc, out, err) = misc.execCmd(cmd)
    if rc != 0:
        raise se.iSCSIDiscoveryError(portal, err)

    return rc, out

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

        cmdt = [constants.EXT_ISCSIADM, "-m", "node", "-T", iqn]

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
        if rc != 0:
            raise se.iSCSILoginError(portal)
    except se.StorageException:
        try:
            if checkSession(ip, port, iqn, tpgt, username, password):
                return 0
        except Exception:
            log.error("Could not get iscsi session list", exc_info=True)
        # Do not try to disconnect - we may remove live node!
        try:
            remiSCSINode(ip, port, iqn, tpgt, username, password, logout=False)
        except Exception:
            log.error("Could not remove iscsi node", exc_info=True)

        raise


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
        cmd = [constants.EXT_ISCSIADM, "-m", "node", "-T", iqn,
            "-p", portal, "-u"]
        rc = misc.execCmd(cmd)[0]
        if rc:
            raise se.iSCSILogoutError(portal)

    # FIXME: should we check if logout succeeds?
    cmd = [constants.EXT_ISCSIADM, "-m", "node", "-o", "delete", "-T", iqn,
        "-p", portal]
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

@misc.samplingmethod
def _getiSCSISessionList():
    """
    Collect the list of active iSCSI sessions
    """
    cmd = [constants.EXT_ISCSIADM, "-m", "session"]
    (rc, out, err) = misc.execCmd(cmd)
    if rc != 0:
        raise se.GetiSCSISessionListError

    # Parse the strings in form
    # tcp: [23] [multipass]:3260,1 iqn.1986-03.com.sun:02:9c576850-ea49-ebdc-d0af-c4db33981227
    # tcp: [24] 10.35.1.99:3260,1 iqn.2006-01.com.openfiler:clear
    # tcp: [26] 10.35.1.99:3260,1 iqn.2006-01.com.openfiler:cheesy

    sessions = []
    keys = ['connection', 'port', 'iqn', 'portal', 'user', 'password']
    user = password = ""
    for i in out:
        p, iqn = i.split()[2:]
        host, p2 = p.split(":")
        host = host.strip("[]")
        port, tpgt = p2.split(",")
        v = [host, port, iqn, tpgt, user, password]
        sessions.append(dict(zip(keys, v)))
    return sessions

def _safeGethostbyname(host):
    try:
        return socket.gethostbyname(host)
    except socket.gaierror:
        return host

def sameSession(enta, entb):
    for k, va in enta.iteritems():
        if k in ['portal', 'user', 'password']:
            continue

        if not va:
            continue

        try:
            vb = entb[k]
        except KeyError:
            return False

        # Portal is not used, user/password not relevant to existing session
        if k == "connection":
            va = _safeGethostbyname(va)
            vb = _safeGethostbyname(vb)

        elif k == 'port':
            va = int(va)
            vb = int(vb)

        if va != vb:
            log.debug("enta key %s v %s != entb v %s" % (k, va, vb))
            return False
    return True

def checkSessionList(sessionList):
    l = _getiSCSISessionList()
    result = []
    keys = ['connection', 'port', 'iqn', 'portal', 'user', 'password']
    for sessionArgs in sessionList:
        # this might contain the cid at the start. Handle bothe cases
        try:
            host, port, iqn, tpgt, user, password = sessionArgs[-6:]
        except ValueError:
            result.append(False)
            continue
        found = False
        dest = dict(zip(keys, [host, port, iqn, tpgt, user, password]))
        log.debug("checkSession: dest %s" % ([e + ": " + str(dest[e]) for e in dest if e != 'password']))
        for ent in l:
            if sameSession(ent, dest):
                result.append(True)
                found = True
                break
        if not found:
            result.append(False)
    return result

def checkSession(host, port, iqn, tpgt, user=None, password=None):
    """
    Check if a session is active
    """
    return checkSessionList([[host, port, iqn, tpgt, user, password]])[0]

def devIsiSCSI(dev):
    hostdir = os.path.realpath(os.path.join("/sys/block", dev, "device/../../.."))
    host = os.path.basename(hostdir)
    iscsi_host = os.path.join(hostdir, constants.STRG_ISCSI_HOST + host)
    scsi_host = os.path.join(hostdir, constants.STRG_SCSI_HOST + host)
    proc_name = os.path.join(scsi_host, "proc_name")
    if os.path.exists(iscsi_host) and os.path.exists(proc_name):
        with open(proc_name, "r") as f:
            return f.readline().startswith("iscsi_tcp")

    return False

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

        cmd = [constants.EXT_CAT, targetname, tpgt, user, passwd, paddr, pport,
            initiator]
        rc, out, err = misc.execCmd(cmd)
        if rc != 0 or len(out) != 7:
            raise se.MiscFileReadException()
        iqn, num, username, password, ip, port, initiator = out

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

