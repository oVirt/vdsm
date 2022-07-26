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
multipath module provides helper procedures for configuring multipath
daemon and maintaining its state
"""
from __future__ import absolute_import
import os
import errno
from glob import glob
import logging
import re
import time

from collections import namedtuple
from contextlib import closing

from vdsm import utils
from vdsm.common import cmdutils
from vdsm.common import commands
from vdsm.common import supervdsm
from vdsm.common import udevadm
from vdsm.common.compat import subprocess
from vdsm.config import config
from vdsm.storage import devicemapper
from vdsm.storage import hba
from vdsm.storage import iscsi
from vdsm.storage import managedvolumedb

DEV_ISCSI = "iSCSI"
DEV_FCP = "FCP"
DEV_MIXED = "MIXED"
SYS_BLOCK = "/sys/block"
QUEUE = "queue"

TOXIC_CHARS = '()*+?|^$.\\'

POLL_INTERVAL = 1.0

log = logging.getLogger("storage.multipath")

_SCSI_ID = cmdutils.CommandPath("scsi_id",
                                "/usr/lib/udev/scsi_id",    # Fedora, EL7
                                "/lib/udev/scsi_id")        # Ubuntu

_MULTIPATHD = cmdutils.CommandPath("multipathd",
                                   "/usr/sbin/multipathd",  # Fedora, EL7
                                   "/sbin/multipathd")      # Ubuntu

# List of multipath devices that should never be handled by vdsm. The
# main use case is to filter out the multipath devices the host is
# booting from when configuring hypervisor to boot from SAN. This device
# must have a special rule to queue I/O when all paths have failed, and
# accessing it in vdsm commands may hang vdsm.
BLACKLIST = frozenset(
    d.strip() for d in config.get("multipath", "blacklist").split(",") if d)


class Error(Exception):
    """ multipath operation failed """


def rescan():
    """
    Forces multipath daemon to rescan the list of available devices and
    refresh the mapping table. New devices can be found under /dev/mapper

    Should only be called from hsm._rescanDevices()
    """

    # First rescan iSCSI and FCP connections
    iscsi.rescan()
    hba.rescan()

    # Now wait until multipathd is ready.
    wait_until_ready()


def wait_until_ready():
    """
    Wait until multipathd is ready after new devices were added.

    SCSI rescan or connecting to new target trigger udev events when the
    kernel add the new SCSI devices. We can use udevadm to wait for
    these events.  We see in the logs that udevadm returns quickly,
    before multipathd started to process the new devices, but we can use
    "multipathd show status" to wait until multipathd processed all the
    new devices.
    """
    timeout = config.getint('multipath', 'wait_timeout')
    start = time.monotonic()
    deadline = start + timeout

    log.info("Waiting until multipathd is ready")

    udevadm.settle(timeout)

    # We treat multipath as ready it reaches steady state - reporting
    # that it is ready in the last 2 intervals.
    ready = 0
    tries = 0

    while ready < 2:
        tries += 1

        time.sleep(POLL_INTERVAL)

        if is_ready():
            ready += 1
        else:
            ready = 0

        if time.monotonic() >= deadline:
            log.warning(
                "Timeout waiting for multipathd (tries=%s, ready=%s)",
                tries, ready)
            return

    log.info(
        "Waited %.2f seconds for multipathd (tries=%s, ready=%s)",
        time.monotonic() - start, tries, ready)


def is_ready():
    """
    Return True if multipathd does not have any uevents to process,
    blocking while multipathd is processing events.

    Typical output when multipathd is busy:

        path checker states:
        up                  2

        paths: 2
        busy: True

    Typical output when deactivating a host:

        path checker states:
        up                  2
        down                4

        paths: 2
        busy: False

    Typical output when multipathd is ready:

        path checker states:
        up                  20

        paths: 20
        busy: False

    In practice, "multipathd show status" almost never reports "busy:
    True".  But when it does it also blocks while processing the new
    devices.

    Here is an example while logging in to 2 iscsi nodes:

        # iscsiadm -m node -l; while true; do time res=$(multipathd show
            status | grep busy:); echo "$(date +%s.%N) $res"; done
        ...

        1630954630.693517697 busy: False

        real 0m0.010s
        user 0m0.005s
        sys 0m0.004s

        1630954630.705336777 busy: True

        real 0m0.793s
        user 0m0.001s
        sys 0m0.006s

        1630954631.500424141 busy: False
        ...

    So polling on "multipathd show status" is better than sleeping.
    """
    if os.geteuid() != 0:
        return supervdsm.getProxy().multipath_is_ready()

    try:
        out = commands.run([_MULTIPATHD.cmd, "show", "status"])
    except cmdutils.Error:
        log.exception("Error getting multipathd status")
        return False  # Assume busy

    status = out.decode("utf-8").lower()
    return "busy: false" in status


def show_config_local():
    """
    Obtain the local configuration of the multipath daemon.
    Must run as root.

    Returns:
        str: Output of the multipathd show command.
    """
    return commands.run(
        [_MULTIPATHD.cmd, "show", "config", "local"]).decode('ascii')


def reconfigure():
    """
    Invoke multipathd to reconfigure the multipaths.
    Must run as root.
    """
    commands.run([_MULTIPATHD.cmd, "reconfigure"])


def resize_devices():
    """
    This is needed in case a device has been increased on the storage server
    Resize multipath map if the underlying slaves are bigger than
    the map size.
    The slaves can be bigger if the LUN size has been increased on the storage
    server after the initial discovery.
    """
    log.info("Resizing multipath devices")
    with utils.stopwatch(
            "Resizing multipath devices", level=logging.INFO, log=log):
        for dmId, guid in getMPDevsIter():
            try:
                _resize_if_needed(guid)
            except Exception:
                log.exception("Could not resize device %s", guid)


def _resize_if_needed(guid):
    name = devicemapper.getDmId(guid)
    slaves = [(slave, getDeviceSize(slave))
              for slave in devicemapper.getSlaves(name)]

    if len(slaves) == 0:
        log.warning("Map %r has no slaves" % guid)
        return False

    if len(set(size for slave, size in slaves)) != 1:
        raise Error("Map %r slaves size differ %s" % (guid, slaves))

    map_size = getDeviceSize(name)
    slave_size = slaves[0][1]
    if map_size == slave_size:
        return False

    log.info("Resizing map %r (map_size=%d, slave_size=%d)",
             guid, map_size, slave_size)
    resize_map(name)
    return True


def resize_map(name):
    """
    Invoke multipathd to resize a device
    Must run as root

    Raises Error if multipathd failed to resize the map.
    """
    if os.geteuid() != 0:
        return supervdsm.getProxy().multipath_resize_map(name)

    log.debug("Resizing map %r", name)
    cmd = [_MULTIPATHD.cmd, "resize", "map", name]
    with utils.stopwatch("Resized map %r" % name, log=log):
        p = commands.start(
            cmd,
            sudo=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )
        out, err = commands.communicate(p)

        out = out.decode("utf-8")
        err = err.decode("utf-8")

        # multipathd reports some errors using non-zero exit code and
        # stderr (need to be root), but the command may return 0, and
        # the result is reported using stdout.
        if p.returncode != 0 or out != "ok\n":
            e = cmdutils.Error(cmd, p.returncode, out, err)
            raise Error("Resizing map {!r} failed: {}".format(name, e))


def deduceType(a, b):
    if a == b:
        return a
    else:
        return DEV_MIXED


def getDeviceBlockSizes(dev):
    devName = os.path.basename(dev)
    logical = read_int(os.path.join(SYS_BLOCK, devName,
                                    QUEUE, "logical_block_size"))
    physical = read_int(os.path.join(SYS_BLOCK, devName,
                                     QUEUE, "physical_block_size"))
    return (logical, physical)


def getDeviceSize(dev):
    devName = os.path.basename(dev)
    bs, phyBs = getDeviceBlockSizes(devName)
    size = bs * read_int(os.path.join(SYS_BLOCK, devName, "size"))
    return size


def getDeviceDiscardMaxBytes(physDev):
    path = os.path.join(SYS_BLOCK, physDev, QUEUE, "discard_max_bytes")
    return read_int(path)


def read_int(path):
    with open(path, "r") as f:
        data = f.readline()
    return int(data)


def get_scsi_serial(physdev):
    if os.geteuid() != 0:
        return supervdsm.getProxy().multipath_get_scsi_serial(physdev)

    blkdev = os.path.join("/dev", physdev)
    cmd = [_SCSI_ID.cmd,
           "--page=0x80",
           "--whitelisted",
           "--export",
           "--replace-whitespace",
           "--device=" + blkdev]

    try:
        out = commands.run(cmd)
    except cmdutils.Error as e:
        # Currently we haven't proper cleanup of LVs when we disconnect SD.
        # This can result in keeping multipath devices without any valid path.
        # For such devices scsi_id fails. Until we have proper cleanup in
        # place, we ignore these failing devices.
        log.debug("Ignoring scsi_id failure for device %s: %s", blkdev, e)
    else:
        for line in out.decode("utf-8").splitlines():
            if line.startswith("ID_SERIAL="):
                return line.split("=", 1)[1]
    return ""  # Fallback if command failed or no ID_SERIAL found


HBTL = namedtuple("HBTL", "host bus target lun")


def getVendor(physDev):
    with open(SYS_BLOCK + "/%s/device/vendor" % physDev, "r") as f:
        return f.read().strip()


def getModel(physDev):
    with open(SYS_BLOCK + "/%s/device/model" % physDev, "r") as f:
        return f.read().strip()


def getFwRev(physDev):
    with open(SYS_BLOCK + "/%s/device/rev" % physDev, "r") as f:
        return f.read().strip()


def getHBTL(physdev):
    hbtl = os.listdir(SYS_BLOCK + "/%s/device/scsi_disk/" % physdev)
    if len(hbtl) > 1:
        log.warn("Found more the 1 HBTL, this shouldn't happen")

    return HBTL(*hbtl[0].split(":"))


def pathListIter(filterGuids=()):
    filterLen = len(filterGuids) if filterGuids else -1
    devsFound = 0
    knownSessions = {}
    pathStatuses = devicemapper.getPathsStatus()

    for dmId, guid in getMPDevsIter():
        if devsFound == filterLen:
            break

        if filterGuids and guid not in filterGuids:
            continue

        devsFound += 1

        devInfo = {
            "guid": guid,
            "dm": dmId,
            "capacity": str(getDeviceSize(dmId)),
            "serial": get_scsi_serial(dmId),
            "paths": [],
            "connections": [],
            "devtypes": [],
            "devtype": "",
            "vendor": "",
            "product": "",
            "fwrev": "",
            "logicalblocksize": "",
            "physicalblocksize": "",
            "discard_max_bytes": getDeviceDiscardMaxBytes(dmId),
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
            pathInfo["capacity"] = str(getDeviceSize(slave))
            try:
                hbtl = getHBTL(slave)
            except OSError as e:
                if e.errno == errno.ENOENT:
                    log.warn("Device has no hbtl: %s", slave)
                    pathInfo["lun"] = 0
                else:
                    log.error("Error: %s while trying to get hbtl of device: "
                              "%s", e, slave)
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
    Collect the list of all the multipath block devices, except devices
    blacklisted in vdsm configuration, and devices owned by managed volumes.

    Return the list of device identifiers w/o "/dev/mapper" prefix
    """
    db = managedvolumedb.open()
    with closing(db):
        for dmInfoDir in glob(SYS_BLOCK + "/dm-*/dm/"):
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

            if guid in BLACKLIST:
                log.debug("Blacklisted device %r discarded", guid)
                continue

            if TOXIC_REGEX.match(guid):
                log.info("Device with unsupported GUID %s discarded", guid)
                continue

            if db.owns_multipath(guid):
                log.debug("Managed volume device %r discarded", guid)
                continue

            yield dmInfoDir.split("/")[3], guid


def devIsiSCSI(type):
    return type in [DEV_ISCSI, DEV_MIXED]


def devIsFCP(type):
    return type in [DEV_FCP, DEV_MIXED]
