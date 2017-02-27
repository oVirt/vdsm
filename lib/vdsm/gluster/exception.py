#
# Copyright 2012-2016 Red Hat, Inc.
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

########################################################
#
#  Set of user defined gluster exceptions.
#
########################################################

########################################################
#
# IMPORTANT NOTE: USE CODES BETWEEN 4100 AND 4800
#
########################################################
#
from __future__ import absolute_import

from vdsm.common.exception import VdsmException


class GlusterException(VdsmException):
    code = 4100
    message = "Gluster Exception"

    def __init__(self, rc=0, out=(), err=()):
        self.rc = rc
        self.out = out
        self.err = err

    def __str__(self):
        o = '\n'.join(self.out)
        e = '\n'.join(self.err)
        if o and e:
            m = o + '\n' + e
        else:
            m = o or e

        s = self.message
        if m:
            s += '\nerror: ' + m
        if self.rc:
            s += '\nreturn code: %s' % self.rc

        return s

    def info(self):
        return {'code': self.code,
                'message': str(self),
                'rc': self.rc,
                'out': self.out,
                'err': self.err}


# General
class GlusterGeneralException(GlusterException):
    code = 4101
    message = "Gluster General Exception"


class GlusterPermissionDeniedException(GlusterGeneralException):
    code = 4102
    message = "Permission denied"


class GlusterSyntaxErrorException(GlusterGeneralException):
    code = 4103
    message = "Syntax error"


class GlusterMissingArgumentException(GlusterGeneralException):
    code = 4104
    message = "Missing argument"

    def __init__(self, *args, **kwargs):
        GlusterGeneralException.__init__(self)
        self.message = 'Missing argument: args=%s, kwargs=%s' % (args, kwargs)


class GlusterCmdExecFailedException(GlusterGeneralException):
    code = 4105
    message = "Command execution failed"


class GlusterXmlErrorException(GlusterGeneralException):
    code = 4106
    message = "XML error"


class GlusterCmdFailedException(GlusterGeneralException):
    code = 4107
    message = "Command failed"


class GlusterProcessesStopFailedException(GlusterGeneralException):
    code = 4579
    message = "Failed to stop gluster processes"


# Volume
class GlusterVolumeException(GlusterException):
    code = 4111
    message = "Gluster Volume Exception"


class GlusterVolumeNameErrorException(GlusterVolumeException):
    code = 4112
    message = "Volume name error"


class GlusterBrickNameErrorException(GlusterVolumeException):
    code = 4113
    message = "Brick name error"


class GlusterVolumeAlreadyExistException(GlusterVolumeException):
    code = 4114
    message = "Volume already exist"


class GlusterBrickCreationFailedException(GlusterVolumeException):
    code = 4115
    message = "Brick creation failed"


class GlusterInvalidTransportException(GlusterVolumeException):
    code = 4116
    message = "Invalid transport"


class GlusterPeerNotFriendException(GlusterVolumeException):
    code = 4117
    message = "Peer not found"


class GlusterInvalidStripeCountException(GlusterVolumeException):
    code = 4118
    message = "Invalid stripe count"


class GlusterInvalidReplicaCountException(GlusterVolumeException):
    code = 4119
    message = "Invalid replica count"


class GlusterInsufficientBrickException(GlusterVolumeException):
    code = 4120
    message = "Insufficient brick"


class GlusterBrickInUseException(GlusterVolumeException):
    code = 4121
    message = "Brick already in use"


class GlusterVolumeCreateFailedException(GlusterVolumeException):
    code = 4122
    message = "Volume create failed"


class GlusterVolumeNotFoundException(GlusterVolumeException):
    code = 4123
    message = "Volume not found"


class GlusterVolumeAlreadyStartedException(GlusterVolumeException):
    code = 4124
    message = "Volume already started"


class GlusterVolumeStartFailedException(GlusterVolumeException):
    code = 4125
    message = "Volume start failed"


class GlusterVolumeAlreadyStoppedException(GlusterVolumeException):
    code = 4126
    message = "Volume already stopped"


class GlusterVolumeStopFailedException(GlusterVolumeException):
    code = 4127
    message = "Volume stop failed"


class GlusterVolumeBrickAddFailedException(GlusterVolumeException):
    code = 4128
    message = "Volume add brick failed"


class GlusterVolumeInvalidOptionException(GlusterVolumeException):
    code = 4129
    message = "Invalid volume option"


class GlusterVolumeInvalidOptionValueException(GlusterVolumeException):
    code = 4130
    message = "Invalid value of volume option"


class GlusterVolumeSetFailedException(GlusterVolumeException):
    code = 4131
    message = "Volume set failed"


class GlusterBrickNotFoundException(GlusterVolumeException):
    code = 4132
    message = "Brick not found"


class GlusterVolumeRebalanceUnknownTypeException(GlusterVolumeException):
    code = 4133
    message = "Unknown rebalance type"


class GlusterVolumeRebalanceAlreadyStartedException(GlusterVolumeException):
    code = 4134
    message = "Volume rebalance already started"


class GlusterVolumeRebalanceStartFailedException(GlusterVolumeException):
    code = 4135
    message = "Volume rebalance start failed"


class GlusterVolumeRebalanceAlreadyStoppedException(GlusterVolumeException):
    code = 4136
    message = "Volume rebalance already stopped"


class GlusterVolumeRebalanceStopFailedException(GlusterVolumeException):
    code = 4137
    message = "Volume rebalance stop failed"


class GlusterVolumeRebalanceStatusFailedException(GlusterVolumeException):
    code = 4138
    message = "Volume rebalance status failed"


class GlusterVolumeDeleteFailedException(GlusterVolumeException):
    code = 4139
    message = "Volume delete failed"


class GlusterVolumeReplaceBrickCommitForceFailedException(
        GlusterVolumeException):
    code = 4148
    message = "Volume replace brick commit force failed"


class GlusterVolumesListFailedException(GlusterVolumeException):
    code = 4149
    message = "Volume list failed"


class GlusterVolumeRemoveBrickStartFailedException(GlusterVolumeException):
    code = 4140
    message = "Volume remove brick start failed"


class GlusterVolumeRemoveBrickStopFailedException(GlusterVolumeException):
    code = 4150
    message = "Volume remove brick stop failed"


class GlusterVolumeRemoveBrickStatusFailedException(GlusterVolumeException):
    code = 4152
    message = "Volume remove brick status failed"


class GlusterVolumeRemoveBrickCommitFailedException(GlusterVolumeException):
    code = 4153
    message = "Volume remove brick commit failed"


class GlusterVolumeSetHelpXmlFailedException(GlusterVolumeException):
    code = 4154
    message = "Volume set help-xml failed"


class GlusterVolumeResetFailedException(GlusterVolumeException):
    code = 4155
    message = "Volume reset failed"


class GlusterVolumeRemoveBrickForceFailedException(GlusterVolumeException):
    code = 4156
    message = "Volume remove brick force failed"


class GlusterVolumeStatusFailedException(GlusterVolumeException):
    code = 4157
    message = "Volume status failed"


class GlusterVolumeProfileStartFailedException(GlusterVolumeException):
    code = 4158
    message = "Volume profile start failed"


class GlusterVolumeProfileStopFailedException(GlusterVolumeException):
    code = 4159
    message = "Volume profile stop failed"


class GlusterVolumeProfileInfoFailedException(GlusterVolumeException):
    code = 4160
    message = "Volume profile info failed"


class GlusterVolumeTasksFailedException(GlusterVolumeException):
    code = 4161
    message = "Volume tasks list failed"


class GlusterVolumeHealInfoFailedException(GlusterVolumeException):
    code = 4162
    message = "Volume heal info failed"


# Host
class GlusterHostException(GlusterException):
    code = 4400
    message = "Gluster host exception"


class GlusterHostInvalidNameException(GlusterHostException):
    code = 4401
    message = "Invalid host name"


class GlusterHostAlreadyAddedException(GlusterHostException):
    code = 4402
    message = "Host already added"


class GlusterHostNotFoundException(GlusterHostException):
    code = 4403
    message = "Host not found"


class GlusterHostAddFailedException(GlusterHostException):
    code = 4404
    message = "Add host failed"


class GlusterHostInUseException(GlusterHostException):
    code = 4405
    message = "Host in use"


class GlusterHostRemoveFailedException(GlusterHostException):
    code = 4406
    message = "Remove host failed"


class GlusterHostsListFailedException(GlusterHostException):
    code = 4407
    message = "Hosts list failed"


class GlusterHostUUIDNotFoundException(GlusterHostException):
    code = 4408
    message = "Host UUID not found"


class GlusterHostStorageDeviceNotFoundException(GlusterHostException):
    code = 4409

    def __init__(self, deviceList=None):
        GlusterHostException.__init__(self)
        self.message = "Device(s) %s not found" % deviceList


class GlusterHostStorageDeviceInUseException(GlusterHostException):
    code = 4410

    def __init__(self, deviceList=None):
        GlusterHostException.__init__(self)
        self.message = "Device(s) %s already in use" % deviceList


class GlusterHostStorageDeviceMountFailedException(GlusterHostException):
    code = 4411

    def __init__(self, device=None, mountPoint=None,
                 fsType=None, mountOpts=None):
        GlusterHostException.__init__(self)
        self.message = "Failed to mount device %s on mount point %s using " \
                       "fs-type %s with mount options %s" % (
                           device, mountPoint, fsType, mountOpts)


class GlusterHostStorageDeviceFsTabFoundException(GlusterHostException):
    code = 4412

    def __init__(self, device=None):
        GlusterHostException.__init__(self)
        self.message = "fstab entry for device %s already exists" % device


class GlusterHostStorageDevicePVCreateFailedException(GlusterHostException):
    code = 4413

    def __init__(self, device=None, alignment=None, rc=0, out=(), err=()):
        self.rc = rc
        self.out = out
        self.err = err
        self.message = "Failed to create LVM PV for device %s with " \
                       "data alignment %s" % (device, alignment)


class GlusterHostStorageDeviceLVConvertFailedException(GlusterHostException):
    code = 4414

    def __init__(self, device=None, alignment=None, rc=0, out=(), err=()):
        self.rc = rc
        self.out = out
        self.err = err
        self.message = "Failed to run lvconvert for device %s with " \
                       "data alignment %s" % (device, alignment)


class GlusterHostStorageDeviceLVChangeFailedException(GlusterHostException):
    code = 4415

    def __init__(self, poolName=None, rc=0, out=(), err=()):
        self.rc = rc
        self.out = out
        self.err = err
        self.message = "Failed to run lvchange for the thin pool: %s" % (
            poolName)


class GlusterHostStorageDeviceMakeDirsFailedException(GlusterHostException):
    code = 4416
    message = "Make directories failed"


class GlusterHostStorageMountPointInUseException(GlusterHostException):
    code = 4417

    def __init__(self, mountPoint=None):
        GlusterHostException.__init__(self)
        self.message = "Mount point %s is in use" % mountPoint


class GlusterHostStorageDeviceVGCreateFailedException(GlusterHostException):
    code = 4418

    def __init__(self, name=None, devices=None, stripeSize=None,
                 rc=0, out=(), err=()):
        self.rc = rc
        self.out = out
        self.err = err
        self.message = "Failed to create LVM VG:%s for devices %s with " \
                       "stripe size %s" % (name, devices, stripeSize)


class GlusterHostStorageDeviceVGScanFailedException(GlusterHostException):
    code = 4419
    message = "vgscan failed"


class GlusterHostFailedToSetSelinuxContext(GlusterHostException):
    code = 4420

    def __init__(self, brickMountPoint=None, rc=0, out=(), err=()):
        self.rc = rc
        self.out = out
        self.err = err
        self.message = "Failed to set selinux context on the brick : %s" \
                       % brickMountPoint


class GlusterHostFailedToRunRestorecon(GlusterHostException):
    code = 4421

    def __init__(self, brickMountPoint=None, rc=0, out=(), err=()):
        self.rc = rc
        self.out = out
        self.err = err
        self.message = "Failed to run restorecon on the brick : %s" \
                       % brickMountPoint


class GlusterHostStorageDeviceMkfsFailedException(GlusterHostException):
    code = 4422

    def __init__(self, fsType=None):
        GlusterHostException.__init__(self)
        self.message = "fstype %s is currently unsupported" % fsType


# Hook
class GlusterHookException(GlusterException):
    code = 4500
    message = "Gluster Hook Exception"


class GlusterHookListException(GlusterException):
    code = 4501
    message = "List gluster hook failed"


class GlusterHookEnableFailedException(GlusterHookException):
    code = 4502
    message = "Enable gluster hook failed"


class GlusterHookDisableFailedException(GlusterHookException):
    code = 4503
    message = "Disable gluster hook failed"


class GlusterHookNotFoundException(GlusterHookException):
    code = 4504

    def __init__(self, glusterCmd=None, level=None, hookName=None):
        GlusterHookException.__init__(self)
        self.glusterCmd = glusterCmd
        self.level = level
        self.hookName = hookName
        self.message = \
            'Hook %s of command %s, level %s not found' % \
            (hookName, glusterCmd, level)


class GlusterHookReadFailedException(GlusterHookException):
    code = 4505
    message = "Hook read failed"


class GlusterHookUpdateFailedException(GlusterHookException):
    code = 4506
    message = "Hook update failed"


class GlusterHookAlreadyExistException(GlusterHookException):
    code = 4507

    def __init__(self, glusterCmd=None, level=None, hookName=None):
        GlusterHookException.__init__(self)
        self.glusterCmd = glusterCmd
        self.level = level
        self.hookName = hookName
        self.message = \
            'Hook %s of command %s, level %s already exist' % \
            (hookName, glusterCmd, level)


class GlusterHookCheckSumMismatchException(GlusterException):
    code = 4508

    def __init__(self, computedMd5Sum=None, expectedMd5Sum=None):
        GlusterHookException.__init__(self)
        self.computedMd5Sum = computedMd5Sum
        self.expectedMd5Sum = expectedMd5Sum
        self.message = 'Hook file check sum:%s mismatch %s' % (computedMd5Sum,
                                                               expectedMd5Sum)


class GlusterHookAddFailedException(GlusterHookException):
    code = 4509
    message = "Hook add failed"


class GlusterHookRemoveFailedException(GlusterHookException):
    code = 4510
    message = "Hook remove failed"


class GlusterServiceException(GlusterException):
    code = 4550
    message = "Gluster Service Exception"


class GlusterServiceActionNotSupportedException(GlusterServiceException):
    code = 4551

    def __init__(self, action=''):
        GlusterServiceException.__init__(self)
        prefix = "%s: " % (action)
        self.message = prefix + "Service action is not supported"
        self.err = [self.message]


class GlusterLibgfapiException(GlusterException):
    code = 4570
    message = "Gluster Libgfapi Exception"


class GlfsStatvfsException(GlusterLibgfapiException):
    code = 4571
    message = "Failed to get Gluster volume Size info"


class GlfsInitException(GlusterLibgfapiException):
    code = 4572
    message = "glfs init failed"


class GlfsFiniException(GlusterLibgfapiException):
    code = 4573
    message = "glfs fini failed"


class GlusterVolumeEmptyCheckFailedException(GlusterVolumeException):
    code = 4574
    message = "Failed to Check if gluster volume is empty"


class GlusterMetaVolumeMountFailedException(GlusterVolumeException):
    code = 4575
    message = "Failed to mount meta volume"


class GlusterMetaVolumeFstabUpdateFailedException(GlusterVolumeException):
    code = 4576
    message = "Failed to Update fstab entry for meta-volume"


class GlusterSnapshotScheduleFlagUpdateFailedException(
        GlusterVolumeException):
    code = 4577
    message = "Failed to update snapshot schedule flag"


class GlusterDisableSnapshotScheduleFailedException(
        GlusterVolumeException):
    code = 4578
    message = "Failed to disable snapshot schedule through cli"


# geo-replication
class GlusterGeoRepException(GlusterException):
    code = 4200
    message = "Gluster Geo-Replication Exception"


class GlusterVolumeGeoRepSessionStartFailedException(GlusterGeoRepException):
    code = 4201
    message = "Volume geo-replication start failed"


class GlusterVolumeGeoRepSessionStopFailedException(GlusterGeoRepException):
    code = 4202
    message = "Volume geo-replication stop failed"


class GlusterGeoRepStatusFailedException(GlusterGeoRepException):
    code = 4203
    message = "Geo Rep status failed"


class GlusterVolumeGeoRepSessionPauseFailedException(GlusterGeoRepException):
    code = 4204
    message = "Volume geo-replication session pause failed"


class GlusterVolumeGeoRepSessionResumeFailedException(GlusterGeoRepException):
    code = 4205
    message = "Volume geo-replication session resume failed"


class GlusterGeoRepConfigFailedException(GlusterGeoRepException):
    code = 4206
    message = "Volume geo-replication config failed"


class GlusterGeoRepPublicKeyFileCreateFailedException(
        GlusterGeoRepException):
    code = 4207
    message = "Creation of public key file failed"


class GlusterGeoRepPublicKeyFileReadErrorException(GlusterGeoRepException):
    code = 4208
    message = "Failed to read public key file"


class GlusterGeoRepUserNotFoundException(GlusterGeoRepException):
    code = 4209
    message = "geo rep user does not exist"


class GlusterGeoRepPublicKeyWriteFailedException(GlusterGeoRepException):
    code = 4210
    message = "geo rep public keys write failed"


class GlusterGeoRepExecuteMountBrokerOptFailedException(
        GlusterGeoRepException):
    code = 4211
    message = "geo rep mount broker option set failed"


class GlusterGeoRepExecuteMountBrokerUserAddFailedException(
        GlusterGeoRepException):
    code = 4212
    message = "geo rep mount broker user add failed"


class GlusterMountBrokerRootCreateFailedException(
        GlusterGeoRepException):
    code = 4213
    message = "geo rep mount broker root create failed"


class GlusterGeoRepSessionCreateFailedException(GlusterGeoRepException):
    code = 4214
    message = "Geo Rep session Creation failed"


class GlusterGeoRepSessionDeleteFailedException(GlusterGeoRepException):
    code = 4215
    message = "Geo Rep session deletion failed"


# Volume Snapshot
class GlusterSnapshotException(GlusterException):
    code = 4700
    message = "Gluster Volume Snapshot Exception"


class GlusterSnapshotCreateFailedException(
        GlusterSnapshotException):
    code = 4701
    message = "Snapshot create failed"


class GlusterSnapshotDeleteFailedException(
        GlusterSnapshotException):
    code = 4702
    message = "Snapshot delete failed"


class GlusterSnapshotActivateFailedException(GlusterSnapshotException):
    code = 4703
    message = "Snapshot activate failed"


class GlusterSnapshotDeactivateFailedException(GlusterSnapshotException):
    code = 4704
    message = "Snapshot de-activate failed"


class GlusterSnapshotRestoreFailedException(GlusterSnapshotException):
    code = 4705
    message = "Snapshot restore failed"


class GlusterSnapshotConfigFailedException(
        GlusterSnapshotException):
    code = 4706
    message = "Snapshot config failed"


class GlusterSnapshotConfigSetFailedException(
        GlusterSnapshotException):
    code = 4707
    message = "Snapshot config set failed"


class GlusterSnapshotConfigGetFailedException(
        GlusterSnapshotException):
    code = 4708
    message = "Snapshot config get failed"


class GlusterSnapshotInfoFailedException(GlusterSnapshotException):
    code = 4709
    message = "Snapshot Info failed"
