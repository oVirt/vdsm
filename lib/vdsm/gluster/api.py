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
from __future__ import absolute_import

import errno
import fcntl
import os
import selinux
from functools import wraps

from vdsm.common import cmdutils
from vdsm.common.define import doneCode
from vdsm.gluster import exception as ge
from vdsm.gluster import fstab
from vdsm.storage import mount
from vdsm import constants
from vdsm import commands
from vdsm import supervdsm as svdsm
from pwd import getpwnam

from . import gluster_mgmt_api
from . import safeWrite
import logging

_SUCCESS = {'status': doneCode}
GEOREP_PUB_KEY_PATH = "/var/lib/glusterd/geo-replication/common_secret.pem.pub"
MOUNT_BROKER_ROOT = "/var/mountbroker-root"
META_VOLUME = "gluster_shared_storage"
META_VOL_MOUNT_POINT = "/var/run/gluster/shared_storage"
LOCK_FILE_DIR = META_VOL_MOUNT_POINT + "/snaps/lock_files"
LOCK_FILE = LOCK_FILE_DIR + "/lock_file"
SNAP_SCHEDULER_FLAG_FILE = META_VOL_MOUNT_POINT + "/snaps/current_scheduler"
FS_TYPE = "glusterfs"
SNAP_SCHEDULER_ALREADY_DISABLED_RC = 7


_snapSchedulerPath = cmdutils.CommandPath(
    "snap_scheduler.py",
    "/usr/sbin/snap_scheduler.py",
)

_stopAllProcessesPath = cmdutils.CommandPath(
    "stop-all-gluster-processes.sh",
    "/usr/share/glusterfs/scripts/stop-all-gluster-processes.sh",
)


GLUSTER_RPM_PACKAGES = (
    ('glusterfs', ('glusterfs',)),
    ('glusterfs-fuse', ('glusterfs-fuse',)),
    ('glusterfs-geo-replication', ('glusterfs-geo-replication',)),
    ('glusterfs-rdma', ('glusterfs-rdma',)),
    ('glusterfs-server', ('glusterfs-server',)),
    ('gluster-swift', ('gluster-swift',)),
    ('gluster-swift-account', ('gluster-swift-account',)),
    ('gluster-swift-container', ('gluster-swift-container',)),
    ('gluster-swift-doc', ('gluster-swift-doc',)),
    ('gluster-swift-object', ('gluster-swift-object',)),
    ('gluster-swift-proxy', ('gluster-swift-proxy',)),
    ('gluster-swift-plugin', ('gluster-swift-plugin',)))

GLUSTER_DEB_PACKAGES = (
    ('glusterfs', 'glusterfs-client'),
    ('glusterfs-fuse', 'libglusterfs0'),
    ('glusterfs-geo-replication', 'libglusterfs0'),
    ('glusterfs-rdma', 'libglusterfs0'),
    ('glusterfs-server', 'glusterfs-server'))


def exportAsVerb(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        rv = func(*args, **kwargs)
        if rv:
            rv.update(_SUCCESS)
            return rv
        else:
            return _SUCCESS

    wrapper.exportAsVerb = True
    return wrapper


def glusterAdditionalFeatures():
    # Check if gluster additional features are supported by this Vdsm.
    # This Check is done by seeing if sample verbs for supporting
    # gluster additional features are available in gluster api.
    glusterfeatureSampleVerbs = {
        'volumeSnapshotCreate': 'GLUSTER_SNAPSHOT',
        'volumeGeoRepSessionCreate': 'GLUSTER_GEO_REPLICATION',
        'storageDevicesList': 'GLUSTER_BRICK_MANAGEMENT'}
    additionalFeatures = []
    for feature, code in glusterfeatureSampleVerbs.iteritems():
        if feature in dir(GlusterApi):
            additionalFeatures.append(code)
    return additionalFeatures


@gluster_mgmt_api
def getGeoRepKeys():
    try:
        with open(GEOREP_PUB_KEY_PATH, 'r') as f:
            pubKeys = f.readlines()
    except IOError as e:
        raise ge.GlusterGeoRepPublicKeyFileReadErrorException(err=[str(e)])
    return pubKeys


@gluster_mgmt_api
def updateGeoRepKeys(userName, geoRepPubKeys):
    try:
        userInfo = getpwnam(userName)
        homeDir = userInfo[5]
        uid = userInfo[2]
        gid = userInfo[3]
    except KeyError as e:
        raise ge.GlusterGeoRepUserNotFoundException(err=[str(e)])

    sshDir = homeDir + "/.ssh"
    authKeysFile = sshDir + "/authorized_keys"

    if not os.path.exists(sshDir):
        try:
            os.makedirs(sshDir, 0o700)
            os.chown(sshDir, uid, gid)
            if selinux.is_selinux_enabled():
                selinux.restorecon(sshDir)
        except OSError as e:
            raise ge.GlusterGeoRepPublicKeyWriteFailedException(err=[str(e)])

    newKeys = [" ".join(l.split()[:-1]) for l in geoRepPubKeys]
    newKeyDict = dict(zip(newKeys, geoRepPubKeys))

    try:
        with open(authKeysFile) as f:
            existingKeyLines = f.readlines()
    except IOError as e:
        if e.errno == errno.ENOENT:
            existingKeyLines = []
        else:
            raise ge.GlusterGeoRepPublicKeyWriteFailedException(err=[str(e)])

    try:
        existingKeys = [" ".join(l.split()[:-1]) for l in existingKeyLines]
        existingKeyDict = dict(zip(existingKeys, existingKeyLines))

        outLines = existingKeyLines
        outKeys = set(newKeyDict).difference(set(existingKeyDict))
        outLines.extend([newKeyDict[k] for k in outKeys if newKeyDict[k]])

        safeWrite(authKeysFile, ''.join(outLines))
        os.chmod(authKeysFile, 0o600)
        os.chown(authKeysFile, uid, gid)
        if selinux.is_selinux_enabled():
            selinux.restorecon(authKeysFile)
    except (IOError, OSError) as e:
        raise ge.GlusterGeoRepPublicKeyWriteFailedException(err=[str(e)])


@gluster_mgmt_api
def createMountBrokerRoot(userName):
    try:
        getpwnam(userName)
    except KeyError as e:
        raise ge.GlusterGeoRepUserNotFoundException(err=[str(e)])

    if not os.path.exists(MOUNT_BROKER_ROOT):
        try:
            os.makedirs(MOUNT_BROKER_ROOT, 0o711)
        except OSError as e:
            raise ge.GlusterMountBrokerRootCreateFailedException(err=[str(e)])
    return


@gluster_mgmt_api
def mountMetaVolume(metaVolumeName):
    def _metaVolumeFstabUpdate(metaVolumeName):
        try:
            fs_spec = "127.0.0.1:" + metaVolumeName
            fstab.FsTab().add(fs_spec, META_VOL_MOUNT_POINT, FS_TYPE,
                              mntOpts=['defaults', '_netdev'])
        except IOError as e:
            raise ge.GlusterMetaVolumeFstabUpdateFailedException(
                err=["fstab update failed", str(e)])
        except ge.GlusterHostStorageDeviceFsTabFoundException as e:
            logging.warn(e.message)

    _metaVolumeFstabUpdate(metaVolumeName)

    if os.path.ismount(META_VOL_MOUNT_POINT):
        try:
            fs_spec = mount.getMountFromTarget(
                os.path.realpath(META_VOL_MOUNT_POINT)).fs_spec
            if fs_spec.endswith(META_VOLUME):
                logging.warn("Meta Volume %s already mounted at %s" % (
                    META_VOLUME, META_VOL_MOUNT_POINT))
                return True
            else:
                raise ge.GlusterMetaVolumeMountFailedException(
                    err=["%s already mounted at %s" % (
                        fs_spec, META_VOL_MOUNT_POINT)])
        except OSError as e:
            raise ge.GlusterMetaVolumeMountFailedException(
                err=["Failed to check if volume is already mounted",
                     str(e)])
    else:
        try:
            os.makedirs(META_VOL_MOUNT_POINT, 0o755)
        except OSError as e:
            if e.errno != errno.EEXIST:
                raise ge.GlusterMetaVolumeMountFailedException(
                    err=['Mount Point creation failed', str(e)])

    command = [constants.EXT_MOUNT, META_VOL_MOUNT_POINT]
    rc, out, err = commands.execCmd(command)
    if rc:
        raise ge.GlusterMetaVolumeMountFailedException(
            rc, out, err)
    return True


@gluster_mgmt_api
def snapshotScheduleDisable():
    command = [_snapSchedulerPath.cmd, "disable_force"]
    rc, out, err = commands.execCmd(command)
    if rc not in [0, SNAP_SCHEDULER_ALREADY_DISABLED_RC]:
        raise ge.GlusterDisableSnapshotScheduleFailedException(
            rc)
    return True


@gluster_mgmt_api
def snapshotScheduleFlagUpdate(value):
    if not os.path.exists(LOCK_FILE_DIR):
        try:
            os.makedirs(LOCK_FILE_DIR, 0o755)
        except OSError as e:
            raise ge.GlusterSnapshotScheduleFlagUpdateFailedException(
                err=[str(e)])

    try:
        f = os.open(LOCK_FILE, os.O_CREAT | os.O_RDWR | os.O_NONBLOCK)
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
            safeWrite(SNAP_SCHEDULER_FLAG_FILE, value)
            fcntl.flock(f, fcntl.LOCK_UN)
        except IOError as e:
            os.close(f)
            raise ge.GlusterSnapshotScheduleFlagUpdateFailedException(
                err=["unable to get the lock", str(e)])
        except OSError as e:
            os.close(f)
            raise ge.GlusterSnapshotScheduleFlagUpdateFailedException(
                err=[str(e)])
    except (IOError, OSError) as e:
        raise ge.GlusterSnapshotScheduleFlagUpdateFailedException(
            err=[str(e)])
    return True


@gluster_mgmt_api
def processesStop():
    command = ["/bin/sh", _stopAllProcessesPath.cmd]
    rc, out, err = commands.execCmd(command)
    if rc:
        raise ge.GlusterProcessesStopFailedException(rc)


class GlusterApi(object):
    """
    The gluster interface of vdsm.

    """

    def __init__(self):
        self.svdsmProxy = svdsm.getProxy()

    @exportAsVerb
    def volumesList(self, volumeName=None, remoteServer=None, options=None):
        return {'volumes': self.svdsmProxy.glusterVolumeInfo(volumeName,
                                                             remoteServer)}

    @exportAsVerb
    def volumeCreate(self, volumeName, brickList, replicaCount=0,
                     stripeCount=0, transportList=[],
                     force=False, arbiter=False, options=None):
        return self.svdsmProxy.glusterVolumeCreate(volumeName, brickList,
                                                   replicaCount, stripeCount,
                                                   transportList, force,
                                                   arbiter)

    @exportAsVerb
    def volumeStart(self, volumeName, force=False, options=None):
        self.svdsmProxy.glusterVolumeStart(volumeName, force)

    @exportAsVerb
    def volumeStop(self, volumeName, force=False, options=None):
        self.svdsmProxy.glusterVolumeStop(volumeName, force)

    @exportAsVerb
    def volumeDelete(self, volumeName, options=None):
        self.svdsmProxy.glusterVolumeDelete(volumeName)

    @exportAsVerb
    def volumeSet(self, volumeName, option, value, options=None):
        self.svdsmProxy.glusterVolumeSet(volumeName, option, value)

    @exportAsVerb
    def volumeSetOptionsList(self, options=None):
        return {'volumeSetOptions': self.svdsmProxy.glusterVolumeSetHelpXml()}

    @exportAsVerb
    def volumeReset(self, volumeName, option='', force=False, options=None):
        self.svdsmProxy.glusterVolumeReset(volumeName, option, force)

    @exportAsVerb
    def volumeBrickAdd(self, volumeName, brickList, replicaCount=0,
                       stripeCount=0, force=False, options=None):
        self.svdsmProxy.glusterVolumeAddBrick(volumeName,
                                              brickList,
                                              replicaCount,
                                              stripeCount,
                                              force)

    @exportAsVerb
    def volumeRebalanceStart(self, volumeName, rebalanceType="",
                             force=False, options=None):
        return self.svdsmProxy.glusterVolumeRebalanceStart(volumeName,
                                                           rebalanceType,
                                                           force)

    @exportAsVerb
    def volumeRebalanceStop(self, volumeName, force=False, options=None):
        return self.svdsmProxy.glusterVolumeRebalanceStop(volumeName, force)

    @exportAsVerb
    def volumeRebalanceStatus(self, volumeName, options=None):
        return self.svdsmProxy.glusterVolumeRebalanceStatus(volumeName)

    @exportAsVerb
    def volumeReplaceBrickCommitForce(self, volumeName, existingBrick,
                                      newBrick, options=None):
        self.svdsmProxy.glusterVolumeReplaceBrickCommitForce(volumeName,
                                                             existingBrick,
                                                             newBrick)

    @exportAsVerb
    def volumeRemoveBrickStart(self, volumeName, brickList,
                               replicaCount=0, options=None):
        return self.svdsmProxy.glusterVolumeRemoveBrickStart(volumeName,
                                                             brickList,
                                                             replicaCount)

    @exportAsVerb
    def volumeRemoveBrickStop(self, volumeName, brickList,
                              replicaCount=0, options=None):
        return self.svdsmProxy.glusterVolumeRemoveBrickStop(volumeName,
                                                            brickList,
                                                            replicaCount)

    @exportAsVerb
    def volumeRemoveBrickStatus(self, volumeName, brickList,
                                replicaCount=0, options=None):
        return self.svdsmProxy.glusterVolumeRemoveBrickStatus(volumeName,
                                                              brickList,
                                                              replicaCount)

    @exportAsVerb
    def volumeRemoveBrickCommit(self, volumeName, brickList,
                                replicaCount=0, options=None):
        self.svdsmProxy.glusterVolumeRemoveBrickCommit(volumeName,
                                                       brickList,
                                                       replicaCount)

    @exportAsVerb
    def volumeRemoveBrickForce(self, volumeName, brickList,
                               replicaCount=0, options=None):
        self.svdsmProxy.glusterVolumeRemoveBrickForce(volumeName, brickList,
                                                      replicaCount)

    def _computeVolumeStats(self, data):
        total = data.f_blocks * data.f_bsize
        free = data.f_bfree * data.f_bsize
        used = total - free
        return {'sizeTotal': str(total),
                'sizeFree': str(free),
                'sizeUsed': str(used)}

    @exportAsVerb
    def volumeStatus(self, volumeName, brick=None, statusOption=None,
                     options=None):
        status = self.svdsmProxy.glusterVolumeStatus(volumeName, brick,
                                                     statusOption)
        if statusOption == 'detail':
            data = self.svdsmProxy.glusterVolumeStatvfs(volumeName)
            status['volumeStatsInfo'] = self._computeVolumeStats(data)
        return {'volumeStatus': status}

    @exportAsVerb
    def hostAdd(self, hostName, options=None):
        self.svdsmProxy.glusterPeerProbe(hostName)

    @exportAsVerb
    def hostRemove(self, hostName, force=False, options=None):
        self.svdsmProxy.glusterPeerDetach(hostName, force)

    @exportAsVerb
    def hostRemoveByUuid(self, hostUuid, force=False, options=None):
        for hostInfo in self.svdsmProxy.glusterPeerStatus():
            if hostInfo['uuid'] == hostUuid:
                hostName = hostInfo['hostname']
                break
        else:
            raise ge.GlusterHostNotFoundException()
        self.svdsmProxy.glusterPeerDetach(hostName, force)

    @exportAsVerb
    def hostsList(self, options=None):
        """
        Returns:
            {'status': {'code': CODE, 'message': MESSAGE},
             'hosts' : [{'hostname': HOSTNAME, 'uuid': UUID,
                         'status': STATE}, ...]}
        """
        return {'hosts': self.svdsmProxy.glusterPeerStatus()}

    @exportAsVerb
    def volumeProfileStart(self, volumeName, options=None):
        self.svdsmProxy.glusterVolumeProfileStart(volumeName)

    @exportAsVerb
    def volumeProfileStop(self, volumeName, options=None):
        self.svdsmProxy.glusterVolumeProfileStop(volumeName)

    @exportAsVerb
    def volumeProfileInfo(self, volumeName, nfs=False, options=None):
        status = self.svdsmProxy.glusterVolumeProfileInfo(volumeName, nfs)
        return {'profileInfo': status}

    @exportAsVerb
    def hooksList(self, options=None):
        status = self.svdsmProxy.glusterHooksList()
        return {'hooksList': status}

    @exportAsVerb
    def hookEnable(self, glusterCmd, hookLevel, hookName, options=None):
        self.svdsmProxy.glusterHookEnable(glusterCmd, hookLevel, hookName)

    @exportAsVerb
    def hookDisable(self, glusterCmd, hookLevel, hookName, options=None):
        self.svdsmProxy.glusterHookDisable(glusterCmd, hookLevel, hookName)

    @exportAsVerb
    def hookRead(self, glusterCmd, hookLevel, hookName, options=None):
        return self.svdsmProxy.glusterHookRead(glusterCmd, hookLevel,
                                               hookName)

    @exportAsVerb
    def hookUpdate(self, glusterCmd, hookLevel, hookName, hookData,
                   hookMd5Sum, options=None):
        self.svdsmProxy.glusterHookUpdate(glusterCmd, hookLevel, hookName,
                                          hookData, hookMd5Sum)

    @exportAsVerb
    def hookAdd(self, glusterCmd, hookLevel, hookName, hookData, hookMd5Sum,
                enable=False, options=None):
        self.svdsmProxy.glusterHookAdd(glusterCmd, hookLevel, hookName,
                                       hookData, hookMd5Sum, enable)

    @exportAsVerb
    def hookRemove(self, glusterCmd, hookLevel, hookName, options=None):
        self.svdsmProxy.glusterHookRemove(glusterCmd, hookLevel, hookName)

    @exportAsVerb
    def hostUUIDGet(self, options=None):
        return {'uuid': self.svdsmProxy.glusterHostUUIDGet()}

    @exportAsVerb
    def servicesAction(self, serviceNames, action, options=None):
        status = self.svdsmProxy.glusterServicesAction(serviceNames,
                                                       action)
        return {'services': status}

    @exportAsVerb
    def servicesGet(self, serviceNames, options=None):
        status = self.svdsmProxy.glusterServicesGet(serviceNames)
        return {'services': status}

    @exportAsVerb
    def tasksList(self, taskIds=[], options=None):
        status = self.svdsmProxy.glusterTasksList(taskIds)
        return {'tasks': status}

    @exportAsVerb
    def volumeStatsInfoGet(self, volumeName, options=None):
        data = self.svdsmProxy.glusterVolumeStatvfs(volumeName)
        return self._computeVolumeStats(data)

    @exportAsVerb
    def storageDevicesList(self, options=None):
        status = self.svdsmProxy.glusterStorageDevicesList()
        return {'deviceInfo': status}

    @exportAsVerb
    def volumeGeoRepSessionStart(self, volumeName, remoteHost,
                                 remoteVolumeName,
                                 remoteUserName=None,
                                 force=False, options=None):
        self.svdsmProxy.glusterVolumeGeoRepSessionStart(volumeName,
                                                        remoteHost,
                                                        remoteVolumeName,
                                                        remoteUserName,
                                                        force)

    @exportAsVerb
    def volumeGeoRepSessionStop(self, volumeName, remoteHost,
                                remoteVolumeName, remoteUserName=None,
                                force=False, options=None):
        self.svdsmProxy.glusterVolumeGeoRepSessionStop(volumeName,
                                                       remoteHost,
                                                       remoteVolumeName,
                                                       remoteUserName,
                                                       force)

    @exportAsVerb
    def volumeGeoRepSessionList(self, volumeName=None, remoteHost=None,
                                remoteVolumeName=None, remoteUserName=None,
                                options=None):
        status = self.svdsmProxy.glusterVolumeGeoRepStatus(
            volumeName,
            remoteHost,
            remoteVolumeName,
            remoteUserName,
        )
        return {'sessions': status}

    @exportAsVerb
    def volumeGeoRepSessionStatus(self, volumeName, remoteHost,
                                  remoteVolumeName, remoteUserName=None,
                                  options=None):
        status = self.svdsmProxy.glusterVolumeGeoRepStatus(
            volumeName,
            remoteHost,
            remoteVolumeName,
            remoteUserName
        )
        return {'sessionStatus': status}

    @exportAsVerb
    def volumeGeoRepSessionPause(self, volumeName, remoteHost,
                                 remoteVolumeName, remoteUserName=None,
                                 force=False, options=None):
        self.svdsmProxy.glusterVolumeGeoRepSessionPause(volumeName,
                                                        remoteHost,
                                                        remoteVolumeName,
                                                        remoteUserName,
                                                        force)

    @exportAsVerb
    def volumeGeoRepSessionResume(self, volumeName, remoteHost,
                                  remoteVolumeName, remoteUserName=None,
                                  force=False, options=None):
        self.svdsmProxy.glusterVolumeGeoRepSessionResume(volumeName,
                                                         remoteHost,
                                                         remoteVolumeName,
                                                         remoteUserName,
                                                         force)

    @exportAsVerb
    def volumeGeoRepConfigList(self, volumeName, remoteHost,
                               remoteVolumeName, remoteUserName=None,
                               options=None):
        status = self.svdsmProxy.glusterVolumeGeoRepConfig(
            volumeName,
            remoteHost,
            remoteVolumeName,
            remoteUserName=remoteUserName
        )
        return {'sessionConfig': status}

    @exportAsVerb
    def volumeGeoRepConfigSet(self, volumeName, remoteHost, remoteVolumeName,
                              optionName, optionValue, remoteUserName=None,
                              options=None):
        self.svdsmProxy.glusterVolumeGeoRepConfig(volumeName,
                                                  remoteHost,
                                                  remoteVolumeName,
                                                  optionName,
                                                  optionValue,
                                                  remoteUserName)

    @exportAsVerb
    def volumeGeoRepConfigReset(self, volumeName, remoteHost,
                                remoteVolumeName, optionName,
                                remoteUserName=None, options=None):
        self.svdsmProxy.glusterVolumeGeoRepConfig(
            volumeName,
            remoteHost,
            remoteVolumeName,
            optionName,
            remoteUserName=remoteUserName)

    @exportAsVerb
    def volumeSnapshotCreate(self, volumeName, snapName,
                             snapDescription=None, force=False,
                             options=None):
        return self.svdsmProxy.glusterSnapshotCreate(
            volumeName,
            snapName,
            snapDescription,
            force
        )

    @exportAsVerb
    def volumeSnapshotDeleteAll(self, volumeName, options=None):
        self.svdsmProxy.glusterSnapshotDelete(volumeName=volumeName)

    @exportAsVerb
    def snapshotDelete(self, snapName, options=None):
        self.svdsmProxy.glusterSnapshotDelete(snapName=snapName)

    @exportAsVerb
    def snapshotActivate(self, snapName, force=False, options=None):
        self.svdsmProxy.glusterSnapshotActivate(snapName, force)

    @exportAsVerb
    def snapshotDeactivate(self, snapName, options=None):
        self.svdsmProxy.glusterSnapshotDeactivate(snapName)

    @exportAsVerb
    def snapshotRestore(self, snapName, options=None):
        status = self.svdsmProxy.glusterSnapshotRestore(snapName)
        return {'snapRestore': status}

    @exportAsVerb
    def snapshotConfigList(self, options=None):
        try:
            status = self.svdsmProxy.glusterSnapshotConfig()
        except ge.GlusterSnapshotConfigFailedException as e:
            raise ge.GlusterSnapshotConfigGetFailedException(rc=e.rc,
                                                             err=e.err)
        return {'snapshotConfig': status}

    @exportAsVerb
    def volumeSnapshotConfigList(self, volumeName, options=None):
        try:
            status = self.svdsmProxy.glusterSnapshotConfig(
                volumeName=volumeName)
        except ge.GlusterSnapshotConfigFailedException as e:
            raise ge.GlusterSnapshotConfigGetFailedException(rc=e.rc,
                                                             err=e.err)
        return {'snapshotConfig': status}

    @exportAsVerb
    def volumeSnapshotConfigSet(self, volumeName, optionName, optionValue,
                                options=None):
        try:
            self.svdsmProxy.glusterSnapshotConfig(volumeName=volumeName,
                                                  optionName=optionName,
                                                  optionValue=optionValue)
        except ge.GlusterSnapshotConfigFailedException as e:
            raise ge.GlusterSnapshotConfigSetFailedException(rc=e.rc,
                                                             err=e.err)

    @exportAsVerb
    def snapshotConfigSet(self, optionName, optionValue, options=None):
        try:
            self.svdsmProxy.glusterSnapshotConfig(optionName=optionName,
                                                  optionValue=optionValue)
        except ge.GlusterSnapshotConfigFailedException as e:
            raise ge.GlusterSnapshotConfigSetFailedException(rc=e.rc,
                                                             err=e.err)

    @exportAsVerb
    def volumeSnapshotList(self, volumeName=None, options=None):
        status = self.svdsmProxy.glusterSnapshotInfo(volumeName)
        return {'snapshotList': status}

    @exportAsVerb
    def createBrick(self, name, mountPoint, devList, fsType=None,
                    raidParams={}, options=None):
        status = self.svdsmProxy.glusterCreateBrick(name,
                                                    mountPoint,
                                                    devList,
                                                    fsType,
                                                    raidParams)
        return {'device': status}

    @exportAsVerb
    def geoRepKeysGet(self, options=None):
        self.svdsmProxy.glusterExecuteGsecCreate()
        pubKeys = self.svdsmProxy.glusterGetGeoRepKeys()
        return {'geoRepPubKeys': pubKeys}

    @exportAsVerb
    def geoRepKeysUpdate(self, userName, geoRepPubKeys, options=None):
        self.svdsmProxy.glusterUpdateGeoRepKeys(userName, geoRepPubKeys)

    @exportAsVerb
    def geoRepMountBrokerSetup(self, remoteUserName, remoteGroupName,
                               remoteVolumeName, partial=False, options=None):
        self.svdsmProxy.glusterCreateMountBrokerRoot(remoteUserName)
        if not partial:
            mountBrokerOptions = {'mountbroker-root': MOUNT_BROKER_ROOT,
                                  'geo-replication-log-group': remoteGroupName,
                                  'rpc-auth-allow-insecure': 'on'}
            for optionName, optionValue in mountBrokerOptions.iteritems():
                self.svdsmProxy.glusterExecuteMountBrokerOpt(optionName,
                                                             optionValue)
            self.svdsmProxy.glusterExecuteMountBrokerUserAdd(remoteUserName,
                                                             remoteVolumeName)

    @exportAsVerb
    def volumeGeoRepSessionCreate(self, volumeName, remoteHost,
                                  remotVolumeName, remoteUserName=None,
                                  force=False, options=None):
        self.svdsmProxy.glusterVolumeGeoRepSessionCreate(
            volumeName,
            remoteHost,
            remotVolumeName,
            remoteUserName,
            force
        )

    @exportAsVerb
    def volumeGeoRepSessionDelete(self, volumeName, remoteHost,
                                  remoteVolumeName, remoteUserName=None,
                                  options=None):
        self.svdsmProxy.glusterVolumeGeoRepSessionDelete(
            volumeName,
            remoteHost,
            remoteVolumeName,
            remoteUserName
        )

    @exportAsVerb
    def volumeEmptyCheck(self, volumeName, options=None):
        status = self.svdsmProxy.glusterVolumeEmptyCheck(volumeName)
        return {'volumeEmptyCheck': status}

    @exportAsVerb
    def metaVolumeMount(self, metaVolumeName=META_VOLUME, options=None):
        self.svdsmProxy.glusterMountMetaVolume(metaVolumeName)

    @exportAsVerb
    def snapshotScheduleOverride(self, force=True, options=None):
        self.svdsmProxy.glusterSnapshotScheduleFlagUpdate("ovirt")
        if force:
            self.svdsmProxy.glusterSnapshotScheduleDisable()

    @exportAsVerb
    def snapshotScheduleReset(self, options=None):
        self.svdsmProxy.glusterSnapshotScheduleFlagUpdate("none")

    @exportAsVerb
    def processesStop(self):
        self.svdsmProxy.glusterProcessesStop()

    @exportAsVerb
    def volumeHealInfo(self, volumeName, options=None):
        return {'healInfo': self.svdsmProxy.glusterVolumeHealInfo(volumeName)}


def getGlusterMethods(gluster):
    l = []
    for name in dir(gluster):
        func = getattr(gluster, name)
        if getattr(func, 'exportAsVerb', False) is True:
            l.append((func, 'gluster%s%s' % (name[0].upper(), name[1:])))
    return tuple(l)
