import os
import misc
from glob import glob
import errno
import re

from supervdsm import getProxy
from constants import EXT_DMSETUP

DMPATH_FORMAT = "/dev/mapper/%s"

def getDmId(deviceMultipathName):
    devlinkPath = DMPATH_FORMAT % deviceMultipathName
    if os.path.islink(devlinkPath):
        dmId = os.path.realpath(devlinkPath).split("/")[-1]
        if os.path.exists("/sys/block/%s" % dmId):
            return dmId

    # Link doesn't exists for some reason, might be a weird
    # udev configuration. Falling back to slow but sure method
    for nameFile in glob("/sys/block/dm-*/dm/name"):
        try:
            with open(nameFile, "r") as f:
                if f.read() == deviceMultipathName:
                    return nameFile.split("/")[3]
        except (IOError, OSError):
            pass

    raise OSError(errno.ENOENT, "Could not find dm device named `%s`" % deviceMultipathName)

def findDev(major, minor):
    return os.path.basename(os.path.realpath('/sys/dev/block/%d:%d' % (major, minor)))

def getSysfsPath(devName):
    if "/" in devName:
        raise ValueError("devName has an illegal format. Parameter is not a devname `%s`" % devName)

    sysfsPath = os.path.join("/sys/block/", devName)
    if sysfsPath != os.path.abspath(sysfsPath):
        raise ValueError("devName has an illegal format. Parameter is not a devname `%s`" % devName)

    if not os.path.exists(sysfsPath):
        raise OSError(errno.ENOENT, "device `%s` doesn't exists" % devName)

    return sysfsPath

def _parseDevFile(devFilePath):
    with open(devFilePath, "r") as f:
        mj, mn = f.readline().split(":")

    return (int(mj), int(mn))

def getSlaves(deviceName):
    mpName = resolveDevName(deviceName)
    sysfsPath = getSysfsPath(mpName)
    return os.listdir(os.path.join(sysfsPath, "slaves"))

def getDevName(dmId):
    nameFilePath = os.path.join(getSysfsPath(dmId), "dm", "name")
    with open(nameFilePath, "r") as f:
        return f.readline().strip()

def resolveDevName(devName):
    try:
        if os.path.exists(getSysfsPath(devName)):
            return devName
    except OSError:
        pass

    try:
        return getDmId(devName)
    except Exception:
        raise OSError(errno.ENOENT, "No such multipath device `%s`" % devName)

def isVirtualDevice(devName):
    devName = resolveDevName(devName)
    return os.path.exists(os.path.join("/sys/devices/virtual/block/", devName))

def isBlockDevice(devName):
    devName = resolveDevName(devName)
    return os.path.exists(os.path.join("/sys/block/", devName))


def getAllSlaves():
    deps = {}
    for name in getAllMappedDevices():
        deps[name] = getSlaves(name)

    return deps

def getDevNum(deviceName):
    mpName = resolveDevName(deviceName)
    sysfsPath = getSysfsPath(mpName)
    devFile = os.path.join(sysfsPath, "dev")
    return _parseDevFile(devFile)

def removeMapping(deviceName):
    return getProxy().removeDeviceMapping(deviceName)

def _removeMapping(deviceName):
    cmd = [EXT_DMSETUP, "remove", deviceName]
    rc = misc.execCmd(cmd, sudo=False)[0]
    if rc != 0:
        raise Exception("Could not remove mapping `%s`" % deviceName)

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
def _getPathsStatus():
    cmd = [EXT_DMSETUP, "status"]
    rc, out, err = misc.execCmd(cmd, sudo=False)
    if rc != 0:
        raise Exception("Could not get device statuses")

    res = {}
    for statusLine in out:
        devName, statusLine = statusLine.split(":", 1)
        for m in PATH_STATUS_RE.finditer(statusLine):
            devNum, status = m.groups()
            physdevName = findDev(*[int(i) for i in devNum.split(":")])
            res[physdevName] = {"A": "active", "F": "failed"}[status]

    return res

def getPathsStatus():
    return getProxy().getPathsStatus()

