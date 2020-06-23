#
# Copyright 2011-2017 Red Hat, Inc.
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

from __future__ import absolute_import

import errno
import logging
import os
import re

from collections import namedtuple
from glob import glob

from vdsm.common import cmdutils
from vdsm.common import supervdsm
from vdsm.common import commands
from vdsm.constants import EXT_DMSETUP
from vdsm.storage import dmsetup


DMPATH_PREFIX = "/dev/mapper/"


PathStatus = namedtuple("PathStatus", "name, status")


log = logging.getLogger("storage.devicemapper")


class Error(Exception):
    """ device mapper operation failed """


def getDmId(deviceMultipathName):
    devlinkPath = os.path.join(DMPATH_PREFIX, deviceMultipathName)
    try:
        devStat = os.stat(devlinkPath)
    except OSError:
        raise OSError(errno.ENODEV, "Could not find dm device named `%s`" %
                                    deviceMultipathName)

    return "dm-%d" % os.minor(devStat.st_rdev)


def device_name(major_minor):
    return os.path.basename(os.path.realpath('/sys/dev/block/%s' %
                                             major_minor))


def getSysfsPath(devName):
    if "/" in devName:
        raise ValueError("devName has an illegal format. "
                         "Parameter is not a devname `%s`" % devName)

    sysfsPath = os.path.join("/sys/block/", devName)
    if sysfsPath != os.path.abspath(sysfsPath):
        raise ValueError("devName has an illegal format. "
                         "Parameter is not a devname `%s`" % devName)

    if not os.path.exists(sysfsPath):
        raise OSError(errno.ENOENT, "device `%s` doesn't exist" % devName)

    return sysfsPath


def getSlaves(deviceName):
    mpName = resolveDevName(deviceName)
    sysfsPath = getSysfsPath(mpName)
    return os.listdir(os.path.join(sysfsPath, "slaves"))


def getDevName(dmId):
    nameFilePath = os.path.join(getSysfsPath(dmId), "dm", "name")
    with open(nameFilePath, "r") as f:
        return f.readline().rstrip("\n")


def resolveDevName(devName):
    try:
        if os.path.exists(getSysfsPath(devName)):
            return devName
    except OSError:
        pass

    try:
        return getDmId(devName)
    except Exception:
        raise OSError(errno.ENODEV, "No such block device `%s`" % devName)


def isVirtualDevice(devName):
    devName = resolveDevName(devName)
    return os.path.exists(os.path.join("/sys/devices/virtual/block/", devName))


def isBlockDevice(devName):
    try:
        devName = resolveDevName(devName)
        return os.path.exists(os.path.join("/sys/block/", devName))
    except OSError:
        return False


def isDmDevice(devName):
    devName = resolveDevName(devName)
    return os.path.exists("/sys/block/%s/dm" % devName)


def getAllSlaves():
    deps = {}
    for name in getAllMappedDevices():
        deps[name] = getSlaves(name)

    return deps


def removeMapping(deviceName):
    if os.geteuid() != 0:
        return supervdsm.getProxy().devicemapper_removeMapping(deviceName)

    log.info("Removing device mapping %s", deviceName)
    cmd = [EXT_DMSETUP, "remove", deviceName]
    try:
        commands.run(cmd)
    except cmdutils.Error as e:
        raise Error("Could not remove mapping: {}".format(e))


def getAllMappedDevices():
    devices = glob("/sys/devices/virtual/block/dm-*")
    names = []
    for device in devices:
        dmName = os.path.basename(device)
        names.append(getDevName(dmName))

    return tuple(names)


def getHolders(slaveName):
    slaveName = resolveDevName(slaveName)
    holdersDir = os.path.join(getSysfsPath(slaveName), "holders")
    return os.listdir(holdersDir)


def removeMappingsHoldingDevice(slaveName):
    holders = getHolders(slaveName)
    for holder in holders:
        removeMapping(getDevName(holder))

PATH_STATUS_RE = re.compile(r"(?P<devnum>\d+:\d+)\s+(?P<status>[AF])")


def getPathsStatus():
    res = {}
    for devName, statusLine in dmsetup.status(target="multipath"):
        for m in PATH_STATUS_RE.finditer(statusLine):
            devNum, status = m.groups()
            physdevName = device_name(devNum)
            res[physdevName] = {"A": "active", "F": "failed"}[status]

    return res


def multipath_status():
    res = {}
    for guid, paths in dmsetup.status(target="multipath"):
        statuses = []
        for m in PATH_STATUS_RE.finditer(paths):
            major_minor, status = m.groups()
            statuses.append(PathStatus(major_minor, status))

        res[guid] = statuses

    return res
