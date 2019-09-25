#
# Copyright 2015-2017 Red Hat, Inc.
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

from __future__ import absolute_import
from __future__ import division

import errno
import logging
import os
import selinux
import six

import blivet
import blivet.formats
import blivet.formats.fs
import blivet.size
from blivet.devices import LVMLogicalVolumeDevice
from blivet.devices import LVMThinLogicalVolumeDevice
from blivet import udev

from vdsm.common import cmdutils
from vdsm.common import commands
from vdsm.gluster import exception as ge
from vdsm.gluster import fstab
from . import gluster_mgmt_api


log = logging.getLogger("Gluster")
_pvCreateCommandPath = cmdutils.CommandPath("pvcreate",
                                            "/sbin/pvcreate",
                                            "/usr/sbin/pvcreate",)
_vgCreateCommandPath = cmdutils.CommandPath("vgcreate",
                                            "/sbin/vgcreate",
                                            "/usr/sbin/vgcreate",)
_lvconvertCommandPath = cmdutils.CommandPath("lvconvert",
                                             "/sbin/lvconvert",
                                             "/usr/sbin/lvconvert",)
_lvchangeCommandPath = cmdutils.CommandPath("lvchange",
                                            "/sbin/lvchange",
                                            "/usr/sbin/lvchange",)
_vgscanCommandPath = cmdutils.CommandPath("vgscan",
                                          "/sbin/vgscan",
                                          "/usr/sbin/vgscan",)
_semanageCommandPath = cmdutils.CommandPath("semanage",
                                            "/sbin/semanage",
                                            "/usr/sbin/semanage",)

# All size are in MiB unless otherwise specified
DEFAULT_CHUNK_SIZE_KB = 256
DEFAULT_METADATA_SIZE_KB = 16777216
MIN_VG_SIZE = 1048576
MIN_METADATA_PERCENT = 0.005
DEFAULT_FS_TYPE = "xfs"
DEFAULT_MOUNT_OPTIONS = "inode64,noatime"


# This method helps to convert the size to given Unittype.
# This is required since there an incompatible change in blivet API
# size.convertTo. Older versions required string param but newer versions
# requires the equivalent constants in blivet.size
def _getDeviceSize(device, unitType='MiB'):
    if hasattr(blivet.size, 'MiB'):
        # New Blivet requires size constants from blivet.size
        unit = getattr(blivet.size, unitType)
        return device.size.convertTo(unit)
    else:
        # Older blivet needs spec string
        return device.size.convertTo(spec=unitType)


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
        info['size'] = '%s' % _getDeviceSize(device)
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
        # lvm vg will not have sysfs path
        if hasattr(udev, 'get_device'):
            dev = udev.get_device(device.sysfsPath) or {}
        elif hasattr(udev, 'udev_get_device'):
            # The code is valid, calling udev_get_devices only if the attribute
            # exists, but pylint is confused by this.
            # TODO: rewrite so pylint does not complain.
            # pylint: disable=no-member
            dev = udev.udev_get_device(device.sysfsPath) or {}
        else:
            dev = {}
        info['fsType'] = device.format.type or dev.get('ID_FS_TYPE', '')
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


def _reset_blivet(blivetEnv):
    try:
        blivetEnv.reset()
    except (blivet.errors.UnusableConfigurationError,
            blivet.errors.StorageError) as e:
        log.error("Error: %s", e)


@gluster_mgmt_api
def storageDevicesList():
    blivetEnv = blivet.Blivet()
    _reset_blivet(blivetEnv)
    return _parseDevices(blivetEnv.devices)


@gluster_mgmt_api
def createBrick(brickName, mountPoint, devNameList, fsType=DEFAULT_FS_TYPE,
                raidParams={}):
    def _getDeviceList(devNameList):
        return [blivetEnv.devicetree.getDeviceByName(devName.split("/")[-1])
                for devName in devNameList]

    def _createPV(deviceList, alignment):
        for dev in deviceList:
            # bz#1178705: Blivet always creates pv with 1MB dataalignment
            # Workaround: Till blivet fixes the issue, we use lvm pvcreate
            rc, out, err = commands.execCmd([_pvCreateCommandPath.cmd,
                                             '--dataalignment',
                                             '%sk' % alignment,
                                             dev.path])
            if rc:
                raise ge.GlusterHostStorageDevicePVCreateFailedException(
                    dev.path, alignment, rc, out, err)
        _reset_blivet(blivetEnv)
        return _getDeviceList([dev.name for dev in deviceList])

    def _createVG(vgName, deviceList, stripeSize):
        # bz#1198568: Blivet always creates vg with 1MB stripe size
        # Workaround: Till blivet fixes the issue, use vgcreate command
        devices = ','.join([device.path for device in deviceList])
        rc, out, err = commands.execCmd([_vgCreateCommandPath.cmd,
                                         '-s', '%sk' % stripeSize,
                                         vgName, devices])
        if rc:
            raise ge.GlusterHostStorageDeviceVGCreateFailedException(
                vgName, devices, stripeSize, rc, out, err)
        blivetEnv.reset()
        return blivetEnv.devicetree.getDeviceByName(vgName)

    def _createThinPool(poolName, vg, alignment,
                        poolMetaDataSize, poolDataSize):
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
        rc, out, err = commands.execCmd([_lvconvertCommandPath.cmd,
                                         '--chunksize', '%sK' % alignment,
                                         '--thinpool', vgPoolName,
                                         '--poolmetadata',
                                         "%s/%s" % (vg.name, metaName),
                                         '--poolmetadataspar', 'n', '-y'])

        if rc:
            raise ge.GlusterHostStorageDeviceLVConvertFailedException(
                vg.path, alignment, rc, out, err)
        rc, out, err = commands.execCmd([_lvchangeCommandPath.cmd,
                                         '--zero', 'n', vgPoolName])
        if rc:
            raise ge.GlusterHostStorageDeviceLVChangeFailedException(
                vgPoolName, rc, out, err)
        _reset_blivet(blivetEnv)
        return blivetEnv.devicetree.getDeviceByName(poolLv.name)

    if os.path.ismount(mountPoint):
        raise ge.GlusterHostStorageMountPointInUseException(mountPoint)

    vgName = "vg-" + brickName
    poolName = "pool-" + brickName
    poolDataSize = 0
    count = 0
    raidType = raidParams.get('type')
    metaDataSizeKib = DEFAULT_METADATA_SIZE_KB
    if raidType == '6':
        count = raidParams['pdCount'] - 2
        alignment = raidParams['stripeSize'] * count
        chunkSize = alignment
    elif raidType == '10':
        count = raidParams['pdCount'] // 2
        alignment = raidParams['stripeSize'] * count
        chunkSize = DEFAULT_CHUNK_SIZE_KB
    else:  # Device type is JBOD
        alignment = DEFAULT_CHUNK_SIZE_KB
        chunkSize = DEFAULT_CHUNK_SIZE_KB

    blivetEnv = blivet.Blivet()
    _reset_blivet(blivetEnv)

    # get the devices list from the device name
    deviceList = _getDeviceList(devNameList)

    # raise an error when any device not actually found in the given list
    notFoundList = set(devNameList).difference(
        set([dev.name for dev in deviceList]))
    if notFoundList:
        raise ge.GlusterHostStorageDeviceNotFoundException(notFoundList)

    # raise an error when any device is used already in the given list
    inUseList = set(devNameList).difference(set([not _canCreateBrick(
        dev) or dev.name for dev in deviceList]))
    if inUseList:
        raise ge.GlusterHostStorageDeviceInUseException(inUseList)

    pvDeviceList = _createPV(deviceList, alignment)
    vg = _createVG(vgName, pvDeviceList, alignment)
    # The following calculation is based on the redhat storage performance doc
    # http://docbuilder.usersys.redhat.com/22522
    # /#chap-Configuring_Red_Hat_Storage_for_Enhancing_Performance

    # create ~16GB metadata LV (metaDataSizeKib) that has a size which is
    # a multiple of RAID stripe width if it is > minimum vg size
    # otherwise allocate a minimum of 0.5% of the data device size
    # and create data LV (poolDataSize) that has a size which is
    # a multiple of stripe width.
    vgSizeKib = int(_getDeviceSize(vg, 'KiB'))
    if _getDeviceSize(vg) < MIN_VG_SIZE:
        metaDataSizeKib = vgSizeKib * MIN_METADATA_PERCENT
    poolDataSize = vgSizeKib - metaDataSizeKib

    metaDataSizeKib = (metaDataSizeKib - (metaDataSizeKib % alignment))
    poolDataSize = (poolDataSize - (poolDataSize % alignment))

    # Creating a thin pool from the data LV and the metadata LV
    # lvconvert --chunksize alignment --thinpool VOLGROUP/thin_pool
    #     --poolmetadata VOLGROUP/metadata_device_name
    pool = _createThinPool(poolName, vg, chunkSize, metaDataSizeKib,
                           poolDataSize)
    # Size of the thin LV should be same as the size of Thinpool to avoid
    # over allocation. Refer bz#1412455 for more info.
    thinlv = LVMThinLogicalVolumeDevice(
        brickName, parents=[pool],
        size=blivet.size.Size('%d KiB' % poolDataSize),
        grow=True)
    blivetEnv.createDevice(thinlv)
    blivetEnv.doIt()

    if fsType != DEFAULT_FS_TYPE:
        log.error("fstype %s is currently unsupported" % fsType)
        raise ge.GlusterHostStorageDeviceMkfsFailedException(fsType)

    if six.PY2:
        get_format = blivet.formats.getFormat  # pylint: disable=no-member
    else:
        get_format = blivet.formats.get_format  # pylint: disable=no-member

    format = get_format(DEFAULT_FS_TYPE, device=thinlv.path,
                        mountopts=DEFAULT_MOUNT_OPTIONS)
    format._defaultFormatOptions = ["-f", "-i", "size=512", "-n", "size=8192"]
    if raidParams.get('type') == '6':
        format._defaultFormatOptions += ["-d", "sw=%s,su=%sk" % (
            count, raidParams.get('stripeSize'))]
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

    # bz#1230495: lvm devices are invisible and appears only after vgscan
    # Workaround: Till the bz gets fixed, We use vgscan to refresh LVM devices
    rc, out, err = commands.execCmd([_vgscanCommandPath.cmd])
    if rc:
        raise ge.GlusterHostStorageDeviceVGScanFailedException(rc, out, err)
    fstab.FsTab().add(thinlv.path, mountPoint,
                      DEFAULT_FS_TYPE, mntOpts=[DEFAULT_MOUNT_OPTIONS])

    # If selinux is enabled, set correct selinux labels on the brick.
    if selinux.is_selinux_enabled():
        rc, out, err = commands.execCmd([_semanageCommandPath.cmd,
                                         'fcontext', '-a', '-t',
                                         'glusterd_brick_t', mountPoint])
        if rc:
            raise ge.GlusterHostFailedToSetSelinuxContext(mountPoint, rc,
                                                          out, err)
        try:
            # mountPoint can be of 'unicode' type when its passed through
            # jsonrpc. restorecon calls into a C API that needs a char *.
            # Thus, it is necessary to encode unicode to a utf-8 string.
            selinux.restorecon(mountPoint.encode('utf-8'), recursive=True)
        except OSError as e:
            errMsg = "[Errno %s] %s: '%s'" % (e.errno, e.strerror, e.filename)
            raise ge.GlusterHostFailedToRunRestorecon(mountPoint, err=errMsg)
    return _getDeviceDict(thinlv)
