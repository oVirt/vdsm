#
# Copyright 2015 Red Hat, Inc.
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
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA  02110-1301 USA
#
# Refer to the README and COPYING files for full details of the license
#

import errno
import logging
import os

import blivet
import blivet.formats
import blivet.formats.fs
import blivet.size
from blivet.devices import LVMVolumeGroupDevice
from blivet.devices import LVMThinPoolDevice
from blivet.devices import LVMLogicalVolumeDevice
from blivet.devices import LVMThinLogicalVolumeDevice

import storage.lvm as lvm
from vdsm import utils

import fstab
import exception as ge
from . import makePublic


log = logging.getLogger("Gluster")
_lvconvertCommandPath = utils.CommandPath("lvconvert",
                                          "/sbin/lvconvert",
                                          "/usr/sbin/lvconvert",)
_lvchangeCommandPath = utils.CommandPath("lvchange",
                                         "/sbin/lvchange",
                                         "/usr/sbin/lvchange",)

# All size are in MiB unless otherwise specified
DEFAULT_CHUNK_SIZE_KB = 256
DEFAULT_METADATA_SIZE_KB = 16777216
MIN_VG_SIZE = 1048576
MIN_METADATA_PERCENT = 0.005
DEFAULT_FS_TYPE = "xfs"
DEFAULT_MOUNT_OPTIONS = "inode64,noatime"


def _getDeviceDict(device, createBrick=False):
    info = {'name': device.name,
            'devPath': device.path,
            'devUuid': device.uuid or '',
            'bus': device.bus or '',
            'model': '',
            'fsType': '',
            'mountPoint': '',
            'uuid': '',
            'createBrick': createBrick}
    if isinstance(device.size, blivet.size.Size):
        info['size'] = '%s' % device.size.convertTo(spec="MiB")
    else:
        info['size'] = '%s' % device.size
    if not info['bus'] and device.parents:
        info['bus'] = device.parents[0].bus
    if device.model:
        info['model'] = "%s (%s)" % (device.model, device.type)
    else:
        info['model'] = device.type
    if device.format:
        info['uuid'] = device.format.uuid or ''
        info['fsType'] = device.format.type or ''
    if hasattr(device.format, 'mountpoint'):
        info['mountPoint'] = device.format.mountpoint or ''
    return info


def _parseDevices(devices):
    deviceList = []
    for device in devices:
        deviceList.append(_getDeviceDict(device, _canCreateBrick(device)))
    return deviceList


def _canCreateBrick(device):
    if not device or device.kids > 0 or device.format.type or \
       hasattr(device.format, 'mountpoint') or \
       device.type in ['cdrom', 'lvmvg', 'lvmthinpool', 'lvmlv', 'lvmthinlv']:
        return False
    return True


@makePublic
def storageDevicesList():
    blivetEnv = blivet.Blivet()
    blivetEnv.reset()
    return _parseDevices(blivetEnv.devices)


@makePublic
def createBrick(brickName, mountPoint, devNameList, fsType=DEFAULT_FS_TYPE,
                raidParams={}):
    def _getDeviceList(devNameList):
        return [blivetEnv.devicetree.getDeviceByName(devName.split("/")[-1])
                for devName in devNameList]

    def _makePartition(deviceList):
        pvDeviceList = []
        doPartitioning = False
        for dev in deviceList:
            if dev.type not in ['disk', 'dm-multipath']:
                pvDeviceList.append(dev)
            else:
                blivetEnv.initializeDisk(dev)
                part = blivetEnv.newPartition(fmt_type="lvmpv", grow=True,
                                              parents=[dev])
                blivetEnv.createDevice(part)
                pvDeviceList.append(part)
                doPartitioning = True

        if doPartitioning:
            blivet.partitioning.doPartitioning(blivetEnv)
        return pvDeviceList

    def _createPV(deviceList, alignment=0):
        def _createAlignedPV(deviceList, alignment):
            for dev in deviceList:
                rc, out, err = lvm._createpv(
                    [dev.path], metadataSize=0,
                    options=('--dataalignment', '%sK' % alignment))
                if rc:
                    raise ge.GlusterHostStorageDevicePVCreateFailedException(
                        dev.path, alignment, rc, out, err)

            blivetEnv.reset()
            return _getDeviceList([dev.name for dev in deviceList])

        if alignment:
            blivetEnv.doIt()
            return _createAlignedPV(deviceList, alignment)

        for dev in deviceList:
            lvmpv = blivet.formats.getFormat("lvmpv", device=dev.path)
            blivetEnv.formatDevice(dev, lvmpv)

        blivet.partitioning.doPartitioning(blivetEnv)
        return deviceList

    def _createVG(vgName, deviceList, stripeSize=0):
        if stripeSize:
            vg = LVMVolumeGroupDevice(
                vgName, peSize=blivet.size.Size('%s KiB' % stripeSize),
                parents=deviceList)
        else:
            vg = LVMVolumeGroupDevice(vgName, parents=deviceList)

        blivetEnv.createDevice(vg)
        return vg

    def _createThinPool(poolName, vg, alignment=0,
                        poolMetaDataSize=0, poolDataSize=0):
        if not alignment:
            # bz#1180228: blivet doesn't handle percentage-based sizes properly
            # Workaround: Till the bz gets fixed, we take only 99% size from vg
            pool = LVMThinPoolDevice(poolName, parents=[vg],
                                     size=(vg.size * 99 / 100),
                                     grow=True)
            blivetEnv.createDevice(pool)
            return pool
        else:
            metaName = "meta-%s" % poolName
            vgPoolName = "%s/%s" % (vg.name, poolName)
            metaLv = LVMLogicalVolumeDevice(
                metaName, parents=[vg],
                size=blivet.size.Size('%d KiB' % poolMetaDataSize))
            poolLv = LVMLogicalVolumeDevice(
                poolName, parents=[vg],
                size=blivet.size.Size('%d KiB' % poolDataSize))
            blivetEnv.createDevice(metaLv)
            blivetEnv.createDevice(poolLv)
            blivetEnv.doIt()

            # bz#1100514: LVM2 currently only supports physical extent sizes
            # that are a power of 2. Till that support is available we need
            # to use lvconvert to achive that.
            # bz#1179826: blivet doesn't support lvconvert functionality.
            # Workaround: Till the bz gets fixed, lvconvert command is used
            rc, out, err = utils.execCmd([_lvconvertCommandPath.cmd,
                                          '--chunksize', '%sK' % alignment,
                                          '--thinpool', vgPoolName,
                                          '--poolmetadata',
                                          "%s/%s" % (vg.name, metaName),
                                          '--poolmetadataspar', 'n', '-y'])

            if rc:
                raise ge.GlusterHostStorageDeviceLVConvertFailedException(
                    vg.path, alignment, rc, out, err)

            rc, out, err = utils.execCmd([_lvchangeCommandPath.cmd,
                                          '--zero', 'n', vgPoolName])
            if rc:
                raise ge.GlusterHostStorageDeviceLVChangeFailedException(
                    vgPoolName, rc, out, err)

            blivetEnv.reset()
            return blivetEnv.devicetree.getDeviceByName(poolLv.name)

    if os.path.ismount(mountPoint):
        raise ge.GlusterHostStorageMountPointInUseException(
            mountPoint, rc, out, err)

    vgName = "vg-" + brickName
    poolName = "pool-" + brickName
    alignment = 0
    chunkSize = 0
    poolDataSize = 0
    count = 0
    metaDataSize = DEFAULT_METADATA_SIZE_KB
    if raidParams.get('type') == '6':
        count = raidParams['pdCount'] - 2
        alignment = raidParams['stripeSize'] * count
        chunkSize = alignment
    elif raidParams.get('type') == '10':
        count = raidParams['pdCount'] / 2
        alignment = raidParams['stripeSize'] * count
        chunkSize = DEFAULT_CHUNK_SIZE_KB

    blivetEnv = blivet.Blivet()
    blivetEnv.reset()

    deviceList = _getDeviceList(devNameList)

    notFoundList = set(devNameList).difference(
        set([dev.name for dev in deviceList]))
    if notFoundList:
        raise ge.GlusterHostStorageDeviceNotFoundException(notFoundList)

    inUseList = set(devNameList).difference(set([not _canCreateBrick(
        dev) or dev.name for dev in deviceList]))
    if inUseList:
        raise ge.GlusterHostStorageDeviceInUseException(inUseList)

    pvDeviceList = _makePartition(deviceList)
    pvDeviceList = _createPV(pvDeviceList, alignment)
    vg = _createVG(vgName, pvDeviceList, raidParams.get('stripeSize', 0))

    # The following calculation is based on the redhat storage performance doc
    # http://docbuilder.usersys.redhat.com/22522
    # /#chap-Configuring_Red_Hat_Storage_for_Enhancing_Performance

    if alignment:
        vgSizeKib = int(vg.size.convertTo(spec="KiB"))
        if vg.size.convertTo(spec='MiB') < MIN_VG_SIZE:
            metaDataSize = vgSizeKib * MIN_METADATA_PERCENT
        poolDataSize = vgSizeKib - metaDataSize
        metaDataSize = (metaDataSize - (metaDataSize % alignment))
        poolDataSize = (poolDataSize - (poolDataSize % alignment))

    pool = _createThinPool(poolName, vg, chunkSize, metaDataSize, poolDataSize)
    thinlv = LVMThinLogicalVolumeDevice(brickName, parents=[pool],
                                        size=pool.size, grow=True)
    blivetEnv.createDevice(thinlv)
    blivetEnv.doIt()

    if fsType != DEFAULT_FS_TYPE:
        log.error("fstype %s is currently unsupported" % fsType)
        raise ge.GlusterHostStorageDeviceMkfsFailedException(
            thinlv.path, alignment, raidParams.get('stripeSize', 0), fsType)

    format = blivet.formats.getFormat(DEFAULT_FS_TYPE, device=thinlv.path)
    if alignment:
        format._defaultFormatOptions = [
            "-f", "-K", "-i", "size=512",
            "-d", "sw=%s,su=%sk" % (count, raidParams.get('stripeSize')),
            "-n", "size=8192"]
    blivetEnv.formatDevice(thinlv, format)
    blivetEnv.doIt()

    try:
        os.makedirs(mountPoint)
    except OSError as e:
        if errno.EEXIST != e.errno:
            errMsg = "[Errno %s] %s: '%s'" % (e.errno, e.strerror, e.filename)
            raise ge.GlusterHostStorageDeviceMakeDirsFailedException(
                err=[errMsg])
    thinlv.format.setup(mountpoint=mountPoint)
    blivetEnv.doIt()
    fstab.FsTab().add(thinlv.path, mountPoint, DEFAULT_FS_TYPE)
    return _getDeviceDict(thinlv)
