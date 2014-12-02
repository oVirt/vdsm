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
import logging
import re
from collections import namedtuple

from vdsm import constants
from vdsm import utils
import hba
import misc
import iscsi
import supervdsm
import devicemapper

DEV_ISCSI = "iSCSI"
DEV_FCP = "FCP"
DEV_MIXED = "MIXED"

TOXIC_CHARS = '()*+?|^$.\\'

log = logging.getLogger("Storage.Multipath")

_scsi_id = utils.CommandPath("scsi_id",
                             "/usr/lib/udev/scsi_id",  # Fedora
                             "/lib/udev/scsi_id",  # EL6, Ubuntu
                             )


def rescan():
    """
    Forces multipath daemon to rescan the list of available devices and
    refresh the mapping table. New devices can be found under /dev/mapper

    Should only be called from hsm._rescanDevices()
    """

    # First rescan iSCSI and FCP connections
    iscsi.rescan()
    hba.rescan()

    # Now let multipath daemon pick up new devices
    misc.execCmd([constants.EXT_MULTIPATH], sudo=True)


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
