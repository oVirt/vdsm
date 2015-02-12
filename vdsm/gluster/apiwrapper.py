#
# Copyright 2014 Red Hat, Inc.
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
from clientIF import clientIF
from gluster.api import GlusterApi


class GlusterApiBase(object):
    ctorArgs = []

    def __init__(self):
        self._cif = clientIF.getInstance()
        self._gluster = GlusterApi(self._cif, self._cif.log)


class GlusterHook(GlusterApiBase):
    def __init__(self):
        GlusterApiBase.__init__(self)

    def list(self):
        return self._gluster.hooksList()

    def enable(self, glusterCmd, hookLevel, hookName):
        return self._gluster.hookEnable(glusterCmd, hookLevel, hookName)

    def disable(self, glusterCmd, hookLevel, hookName):
        return self._gluster.hookDisable(glusterCmd, hookLevel, hookName)

    def read(self, glusterCmd, hookLevel, hookName):
        return self._gluster.hookRead(glusterCmd, hookLevel, hookName)

    def update(self, glusterCmd, hookLevel, hookName, hookData,
               hookMd5Sum):
        return self._gluster.hookUpdate(glusterCmd, hookLevel, hookName,
                                        hookData, hookMd5Sum)

    def add(self, glusterCmd, hookLevel, hookName, hookData, hookMd5Sum,
            enable=False):
        return self._gluster.hookAdd(glusterCmd, hookLevel, hookName,
                                     hookData, hookMd5Sum, enable)

    def remove(self, glusterCmd, hookLevel, hookName):
        return self._gluster.hookRemove(glusterCmd, hookLevel, hookName)


class GlusterHost(GlusterApiBase):
    def __init__(self):
        GlusterApiBase.__init__(self)

    def uuid(self):
        return self._gluster.hostUUIDGet()

    def add(self, hostName):
        return self._gluster.hostAdd(hostName)

    def remove(self, hostName, force=False):
        return self._gluster.hostRemove(hostName, force)

    def removeByUuid(self, hostUuid, force=False):
        return self._gluster.hostRemoveByUuid(hostUuid, force)

    def list(self):
        return self._gluster.hostsList()

    def storageDevicesList(self, options=None):
        return self._gluster.storageDevicesList()

    def createBrick(self, name, mountPoint, devList, fsType=None,
                    raidParams={}):
        return self._gluster.createBrick(name, mountPoint,
                                         devList, fsType, raidParams)


class GlusterService(GlusterApiBase):
    def __init__(self):
        GlusterApiBase.__init__(self)

    def get(self, serviceNames):
        return self._gluster.servicesGet(serviceNames)

    def action(self, serviceNames, action):
        return self._gluster.servicesAction(serviceNames, action)


class GlusterTask(GlusterApiBase):
    def __init__(self):
        GlusterApiBase.__init__(self)

    def list(self, taskIds=[]):
        return self._gluster.tasksList(taskIds)


class GlusterVolume(GlusterApiBase):
    def __init__(self):
        GlusterApiBase.__init__(self)

    def status(self, volumeName, brick=None, statusOption=None):
        return self._gluster.volumeStatus(volumeName, brick, statusOption)

    def list(self, volumeName=None, remoteServer=None):
        return self._gluster.volumesList(volumeName, remoteServer)

    def create(self, volumeName, brickList, replicaCount=0, stripeCount=0,
               transportList=[], force=False):
        return self._gluster.volumeCreate(volumeName, brickList, replicaCount,
                                          stripeCount, transportList, force)

    def start(self, volumeName, force=False):
        return self._gluster.volumeStart(volumeName, force)

    def stop(self, volumeName, force=False):
        return self._gluster.volumeStop(volumeName, force)

    def delete(self, volumeName):
        return self._gluster.volumeDelete(volumeName)

    def set(self, volumeName, option, value):
        return self._gluster.volumeSet(volumeName, option, value)

    def setOptionsList(self):
        return self._gluster.volumeSetOptionsList()

    def reset(self, volumeName, option='', force=False):
        return self._gluster.volumeReset(volumeName, option, force)

    def addBrick(self, volumeName, brickList, replicaCount=0, stripeCount=0,
                 force=False):
        return self._gluster.volumeBrickAdd(volumeName, brickList,
                                            replicaCount, stripeCount, force)

    def removeBrickStart(self, volumeName, brickList, replicaCount=0):
        return self._gluster.volumeRemoveBrickStart(volumeName, brickList,
                                                    replicaCount)

    def removeBrickStop(self, volumeName, brickList, replicaCount=0):
        return self._gluster.volumeRemoveBrickStop(volumeName, brickList,
                                                   replicaCount)

    def removeBrickStatus(self, volumeName, brickList, replicaCount=0):
        return self._gluster.volumeRemoveBrickStatus(volumeName, brickList,
                                                     replicaCount)

    def removeBrickCommit(self, volumeName, brickList, replicaCount=0):
        return self._gluster.volumeRemoveBrickCommit(volumeName, brickList,
                                                     replicaCount)

    def removeBrickForce(self, volumeName, brickList, replicaCount=0):
        return self._gluster.volumeRemoveBrickForce(volumeName, brickList,
                                                    replicaCount)

    def replaceBrickStart(self, volumeName, existingBrick, newBrick):
        return self._gluster.volumeReplaceBrickStart(volumeName, existingBrick,
                                                     newBrick)

    def profileInfo(self, volumeName, nfs=False):
        return self._gluster.volumeProfileInfo(volumeName, nfs)

    def profileStart(self, volumeName):
        return self._gluster.volumeProfileStart(volumeName)

    def profileStop(self, volumeName):
        return self._gluster.volumeProfileStop(volumeName)

    def rebalanceStart(self, volumeName, rebalanceType="", force=False):
        return self._gluster.volumeRebalanceStart(volumeName, rebalanceType,
                                                  force)

    def rebalanceStop(self, volumeName, force=False):
        return self._gluster.volumeRebalanceStop(volumeName, force)

    def rebalanceStatus(self, volumeName):
        return self._gluster.volumeRebalanceStatus(volumeName)

    def geoRepSessionStart(self, volumeName, remoteHost,
                           remoteVolumeName, force=False):
        return self._gluster.volumeGeoRepSessionStart(volumeName,
                                                      remoteHost,
                                                      remoteVolumeName,
                                                      force)

    def geoRepSessionStop(self, volumeName, remoteHost,
                          remoteVolumeName, force=False):
        return self._gluster.volumeGeoRepSessionStop(volumeName,
                                                     remoteHost,
                                                     remoteVolumeName,
                                                     force)

    def geoRepSessionStatus(self, masterVolumeName, remoteHost,
                            remoteVolumeName):
        return self._gluster.volumeGeoRepSessionStatus(masterVolumeName,
                                                       remoteHost,
                                                       remoteVolumeName)

    def geoRepSessionList(self, masterVolumeName=None, remoteHost=None,
                          remoteVolumeName=None):
        return self._gluster.volumeGeoRepSessionList(masterVolumeName,
                                                     remoteHost,
                                                     remoteVolumeName)

    def geoRepSessionPause(self, volumeName, remoteHost,
                           remoteVolumeName, force=False):
        return self._gluster.volumeGeoRepSessionPause(volumeName,
                                                      remoteHost,
                                                      remoteVolumeName,
                                                      force)

    def geoRepSessionResume(self, volumeName, remoteHost,
                            remoteVolumeName, force=False):
        return self._gluster.volumeGeoRepSessionResume(volumeName,
                                                       remoteHost,
                                                       remoteVolumeName,
                                                       force)

    def geoRepConfigList(self, volumeName, remoteHost, remoteVolumeName):
        return self._gluster.volumeGeoRepConfigList(volumeName, remoteHost,
                                                    remoteVolumeName)

    def geoRepConfigSet(self, volumeName, remoteHost, remoteVolumeName,
                        optionName, optionValue):
        return self._gluster.volumeGeoRepConfigSet(volumeName, remoteHost,
                                                   remoteVolumeName,
                                                   optionName, optionValue)

    def geoRepConfigReset(self, volumeName, remoteHost,
                          remoteVolumeName, optionName):
        return self._gluster.volumeGeoRepConfigReset(volumeName,
                                                     remoteHost,
                                                     remoteVolumeName,
                                                     optionName)

    def snapshotCreate(self, volumeName,
                       snapName, snapDescription=None,
                       force=False):
        return self._gluster.volumeSnapshotCreate(volumeName, snapName,
                                                  snapDescription, force)

    def snapshotDeleteAll(self, volumeName):
        return self._gluster.volumeSnapshotDeleteAll(volumeName)

    def snapshotConfigSet(self, volumeName, optionName, optionValue):
        return self._gluster.volumeSnapshotConfigSet(volumeName, optionName,
                                                     optionValue)

    def snapshotConfigList(self, volumeName):
        return self._gluster.volumeSnapshotConfigList(volumeName)

    def snapshotList(self, volumeName=None):
        return self._gluster.volumeSnapshotList(volumeName)


class GlusterSnapshot(GlusterApiBase):
    def __init__(self):
        GlusterApiBase.__init__(self)

    def delete(self, snapName):
        return self._gluster.snapshotDelete(snapName)

    def activate(self, snapName, force=False):
        return self._gluster.snapshotActivate(snapName, force)

    def deactivate(self, snapName):
        return self._gluster.snapshotDeactivate(snapName)

    def restore(self, snapName):
        return self._gluster.snapshotRestore(snapName)

    def configList(self):
        return self._gluster.snapshotConfigList()

    def configSet(self, optionName, optionValue):
        return self._gluster.snapshotConfigSet(optionName, optionValue)
