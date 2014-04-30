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
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA
#
# Refer to the README and COPYING files for full details of the license
#

"""
multipath module provides helper procedures for configuring multipath
daemon and maintaining its state
"""
import os
import errno
from glob import glob
import tempfile
import logging
import re
from collections import namedtuple

from vdsm import constants
from vdsm import utils
import misc
import iscsi
import supervdsm
import devicemapper

import storage_exception as se

DEV_ISCSI = "iSCSI"
DEV_FCP = "FCP"
DEV_MIXED = "MIXED"

MAX_CONF_COPIES = 5

TOXIC_CHARS = '()*+?|^$.\\'

MPATH_CONF = "/etc/multipath.conf"

OLD_TAGS = ["# RHAT REVISION 0.2", "# RHEV REVISION 0.3",
            "# RHEV REVISION 0.4", "# RHEV REVISION 0.5",
            "# RHEV REVISION 0.6", "# RHEV REVISION 0.7",
            "# RHEV REVISION 0.8", "# RHEV REVISION 0.9"]
MPATH_CONF_TAG = "# RHEV REVISION 1.0"
MPATH_CONF_PRIVATE_TAG = "# RHEV PRIVATE"
STRG_MPATH_CONF = (
    "\n\n"
    "defaults {\n"
    "    polling_interval        5\n"
    "    getuid_callout          \"%(scsi_id_path)s --whitelisted "
    "--replace-whitespace --device=/dev/%%n\"\n"
    "    no_path_retry           fail\n"
    "    user_friendly_names     no\n"
    "    flush_on_last_del       yes\n"
    "    fast_io_fail_tmo        5\n"
    "    dev_loss_tmo            30\n"
    "    max_fds                 4096\n"
    "}\n"
    "\n"
    "devices {\n"
    "device {\n"
    "    vendor                  \"HITACHI\"\n"
    "    product                 \"DF.*\"\n"
    "    getuid_callout          \"%(scsi_id_path)s --whitelisted "
    "--replace-whitespace --device=/dev/%%n\"\n"
    "}\n"
    "device {\n"
    "    vendor                  \"COMPELNT\"\n"
    "    product                 \"Compellent Vol\"\n"
    "    no_path_retry           fail\n"
    "}\n"
    "}"
)
MPATH_CONF_TEMPLATE = MPATH_CONF_TAG + STRG_MPATH_CONF

log = logging.getLogger("Storage.Multipath")

_scsi_id = utils.CommandPath("scsi_id",
                             "/sbin/scsi_id",  # EL6
                             "/usr/lib/udev/scsi_id",  # Fedora
                             "/lib/udev/scsi_id",  # Ubuntu
                             )


def rescan():
    """
    Forces multipath daemon to rescan the list of available devices and
    refresh the mapping table. New devices can be found under /dev/mapper

    Should only be called from hsm._rescanDevices()
    """

    # First ask iSCSI to rescan all its sessions
    iscsi.rescan()

    # Now let multipath daemon pick up new devices
    misc.execCmd([constants.EXT_MULTIPATH], sudo=True)


def isEnabled():
    """
    Check the multipath daemon configuration. The configuration file
    /etc/multipath.conf should contain private tag in form
    "RHEV REVISION X.Y" for this check to succeed.
    If the tag above is followed by tag "RHEV PRIVATE" the configuration
    should be preserved at all cost.

    """
    if os.path.exists(MPATH_CONF):
        first = second = ''
        svdsm = supervdsm.getProxy()
        mpathconf = svdsm.readMultipathConf()
        try:
            first = mpathconf[0]
            second = mpathconf[1]
        except IndexError:
            pass
        if MPATH_CONF_PRIVATE_TAG in second:
            log.info("Manual override for multipath.conf detected - "
                     "preserving current configuration")
            if MPATH_CONF_TAG not in first:
                log.warning("This manual override for multipath.conf "
                            "was based on downrevved template. "
                            "You are strongly advised to "
                            "contact your support representatives")
            return True

        if MPATH_CONF_TAG in first:
            log.debug("Current revision of multipath.conf detected, "
                      "preserving")
            return True

        for tag in OLD_TAGS:
            if tag in first:
                log.info("Downrev multipath.conf detected, upgrade required")
                return False

    log.debug("multipath Defaulting to False")
    return False


def setupMultipath():
    """
    Set up the multipath daemon configuration to the known and
    supported state. The original configuration, if any, is saved
    """
    if os.path.exists(MPATH_CONF):
        misc.rotateFiles(
            os.path.dirname(MPATH_CONF),
            os.path.basename(MPATH_CONF), MAX_CONF_COPIES,
            cp=True, persist=True)
    with tempfile.NamedTemporaryFile() as f:
        f.write(MPATH_CONF_TEMPLATE % {'scsi_id_path': _scsi_id.cmd})
        f.flush()
        cmd = [constants.EXT_CP, f.name, MPATH_CONF]
        rc = misc.execCmd(cmd, sudo=True)[0]
        if rc != 0:
            raise se.MultipathSetupError()
    misc.persistFile(MPATH_CONF)

    # Flush all unused multipath device maps
    misc.execCmd([constants.EXT_MULTIPATH, "-F"], sudo=True)

    cmd = [constants.EXT_VDSM_TOOL, "service-reload", "multipathd"]
    rc = misc.execCmd(cmd, sudo=True)[0]
    if rc != 0:
        raise se.MultipathReloadError()


def deduceType(a, b):
    if a == b:
        return a
    else:
        return DEV_MIXED


def getDeviceBlockSizes(dev):
    devName = os.path.basename(dev)
    logical = int(file(os.path.join("/sys/block/", devName,
                                    "queue", "logical_block_size")).read())
    physical = int(file(os.path.join("/sys/block/", devName,
                                     "queue", "physical_block_size")).read())
    return (logical, physical)


def getDeviceSize(dev):
    devName = os.path.basename(dev)
    bs, phyBs = getDeviceBlockSizes(devName)
    size = bs * int(file(os.path.join("/sys/block/", devName, "size")).read())
    return size


def getScsiSerial(physdev):
    blkdev = os.path.join("/dev", physdev)
    cmd = [_scsi_id.cmd,
           "--page=0x80",
           "--whitelisted",
           "--export",
           "--replace-whitespace",
           "--device=" + blkdev]
    (rc, out, err) = misc.execCmd(cmd)
    if rc == 0:
        for line in out:
            if line.startswith("ID_SERIAL="):
                return line.split("=")[1]
    return ""

HBTL = namedtuple("HBTL", "host bus target lun")


def getVendor(physDev):
    with open("/sys/block/%s/device/vendor" % physDev, "r") as f:
        return f.read().strip()


def getModel(physDev):
    with open("/sys/block/%s/device/model" % physDev, "r") as f:
        return f.read().strip()


def getFwRev(physDev):
    with open("/sys/block/%s/device/rev" % physDev, "r") as f:
        return f.read().strip()


def getHBTL(physdev):
    hbtl = os.listdir("/sys/block/%s/device/scsi_disk/" % physdev)
    if len(hbtl) > 1:
        log.warn("Found more the 1 HBTL, this shouldn't happen")

    return HBTL(*hbtl[0].split(":"))


def pathListIter(filterGuids=None):
    filteringOn = filterGuids is not None
    filterLen = len(filterGuids) if filteringOn else -1
    devsFound = 0

    knownSessions = {}

    svdsm = supervdsm.getProxy()
    pathStatuses = devicemapper.getPathsStatus()

    for dmId, guid in getMPDevsIter():
        if devsFound == filterLen:
            break

        if filteringOn and guid not in filterGuids:
            continue

        devsFound += 1

        devInfo = {
            "guid": guid,
            "dm": dmId,
            "capacity": str(getDeviceSize(dmId)),
            "serial": svdsm.getScsiSerial(dmId),
            "paths": [],
            "connections": [],
            "devtypes": [],
            "devtype": "",
            "vendor": "",
            "product": "",
            "fwrev": "",
            "logicalblocksize": "",
            "physicalblocksize": "",
        }

        for slave in devicemapper.getSlaves(dmId):
            if not devicemapper.isBlockDevice(slave):
                log.warning("No such physdev '%s' is ignored" % slave)
                continue

            if not devInfo["vendor"]:
                try:
                    devInfo["vendor"] = getVendor(slave)
                except Exception:
                    log.warn("Problem getting vendor from device `%s`",
                             slave, exc_info=True)

            if not devInfo["product"]:
                try:
                    devInfo["product"] = getModel(slave)
                except Exception:
                    log.warn("Problem getting model name from device `%s`",
                             slave, exc_info=True)

            if not devInfo["fwrev"]:
                try:
                    devInfo["fwrev"] = getFwRev(slave)
                except Exception:
                    log.warn("Problem getting fwrev from device `%s`",
                             slave, exc_info=True)

            if (not devInfo["logicalblocksize"] or
                    not devInfo["physicalblocksize"]):
                try:
                    logBlkSize, phyBlkSize = getDeviceBlockSizes(slave)
                    devInfo["logicalblocksize"] = str(logBlkSize)
                    devInfo["physicalblocksize"] = str(phyBlkSize)
                except Exception:
                    log.warn("Problem getting blocksize from device `%s`",
                             slave, exc_info=True)

            pathInfo = {}
            pathInfo["physdev"] = slave
            pathInfo["state"] = pathStatuses.get(slave, "failed")
            try:
                hbtl = getHBTL(slave)
            except OSError as e:
                if e.errno == errno.ENOENT:
                    log.warn("Device has no hbtl: %s", slave)
                    pathInfo["lun"] = 0
                else:
                    log.error("Error: %s while trying to get hbtl of device: "
                              "%s", str(e.message), slave)
                    raise
            else:
                pathInfo["lun"] = hbtl.lun

            if iscsi.devIsiSCSI(slave):
                devInfo["devtypes"].append(DEV_ISCSI)
                pathInfo["type"] = DEV_ISCSI
                sessionID = iscsi.getiScsiSession(slave)
                if sessionID not in knownSessions:
                    # FIXME: This entire part is for BC. It should be moved to
                    # hsm and not preserved for new APIs. New APIs should keep
                    # numeric types and sane field names.
                    sess = iscsi.getSessionInfo(sessionID)
                    sessionInfo = {
                        "connection": sess.target.portal.hostname,
                        "port": str(sess.target.portal.port),
                        "iqn": sess.target.iqn,
                        "portal": str(sess.target.tpgt),
                        "initiatorname": sess.iface.name
                    }

                    # Note that credentials must be sent back in order for
                    # the engine to tell vdsm how to reconnect later
                    if sess.credentials:
                        cred = sess.credentials
                        sessionInfo['user'] = cred.username
                        sessionInfo['password'] = cred.password

                    knownSessions[sessionID] = sessionInfo
                devInfo["connections"].append(knownSessions[sessionID])
            else:
                devInfo["devtypes"].append(DEV_FCP)
                pathInfo["type"] = DEV_FCP

            if devInfo["devtype"] == "":
                devInfo["devtype"] = pathInfo["type"]
            elif (devInfo["devtype"] != DEV_MIXED and
                  devInfo["devtype"] != pathInfo["type"]):
                devInfo["devtype"] == DEV_MIXED

            devInfo["paths"].append(pathInfo)

        yield devInfo


TOXIC_REGEX = re.compile(r"[%s]" % re.sub(r"[\-\\\]]",
                         lambda m: "\\" + m.group(),
                         TOXIC_CHARS))


def getMPDevNamesIter():
    for _, name in getMPDevsIter():
        yield os.path.join(devicemapper.DMPATH_PREFIX, name)


def getMPDevsIter():
    """
    Collect the list of all the multipath block devices.
    Return the list of device identifiers w/o "/dev/mapper" prefix
    """
    for dmInfoDir in glob("/sys/block/dm-*/dm/"):
        uuidFile = os.path.join(dmInfoDir, "uuid")
        try:
            with open(uuidFile, "r") as uf:
                uuid = uf.read().strip()
        except (OSError, IOError):
            continue

        if not uuid.startswith("mpath-"):
            continue

        nameFile = os.path.join(dmInfoDir, "name")
        try:
            with open(nameFile, "r") as nf:
                guid = nf.read().rstrip("\n")
        except (OSError, IOError):
            continue

        if TOXIC_REGEX.match(guid):
            log.info("Device with unsupported GUID %s discarded", guid)
            continue

        yield dmInfoDir.split("/")[3], guid


def devIsiSCSI(type):
    return type in [DEV_ISCSI, DEV_MIXED]


def devIsFCP(type):
    return type in [DEV_FCP, DEV_MIXED]
