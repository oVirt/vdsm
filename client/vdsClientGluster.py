# Copyright 2012 Red Hat, Inc.
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

import hashlib
import base64
import pprint as pp

from vdsClient import service


class GlusterService(service):
    def __init__(self):
        service.__init__(self)

    def do_glusterVolumeCreate(self, args):
        params = self._eqSplit(args)
        brickList = params.get('bricks', '').split(',')
        volumeName = params.get('volumeName', '')
        replicaCount = params.get('replica', '')
        arbiter = (params.get('arbiter', 'no').upper() == 'YES')
        stripeCount = params.get('stripe', '')
        transport = params.get('transport', '')
        transportList = transport.strip().split(',') if transport else []
        force = (params.get('force', 'no').upper() == 'YES')

        status = self.s.glusterVolumeCreate(volumeName, brickList,
                                            replicaCount, stripeCount,
                                            transportList, force, arbiter)
        pp.pprint(status)
        return status['status']['code'], status['status']['message']

    def do_glusterVolumesList(self, args):
        params = self._eqSplit(args)
        volumeName = params.get('volumeName', '')
        remoteServer = params.get('remoteServer', '')

        status = self.s.glusterVolumesList(volumeName, remoteServer)
        pp.pprint(status)
        return status['status']['code'], status['status']['message']

    def do_glusterVolumeStart(self, args):
        params = self._eqSplit(args)
        volumeName = params.get('volumeName', '')
        force = (params.get('force', 'no').upper() == 'YES')

        status = self.s.glusterVolumeStart(volumeName, force)
        return status['status']['code'], status['status']['message']

    def do_glusterVolumeStop(self, args):
        params = self._eqSplit(args)
        volumeName = params.get('volumeName', '')
        force = (params.get('force', 'no').upper() == 'YES')

        status = self.s.glusterVolumeStop(volumeName, force)
        return status['status']['code'], status['status']['message']

    def do_glusterVolumeBrickAdd(self, args):
        params = self._eqSplit(args)
        brickList = params.get('bricks', '').split(',')
        volumeName = params.get('volumeName', '')
        replicaCount = params.get('replica', '')
        stripeCount = params.get('stripe', '')
        force = (params.get('force', 'no').upper() == 'YES')

        status = self.s.glusterVolumeBrickAdd(volumeName, brickList,
                                              replicaCount, stripeCount, force)
        pp.pprint(status)
        return status['status']['code'], status['status']['message']

    def do_glusterVolumeSet(self, args):
        params = self._eqSplit(args)
        volumeName = params.get('volumeName', '')
        option = params.get('option', '')
        value = params.get('value', '')

        status = self.s.glusterVolumeSet(volumeName, option, value)
        return status['status']['code'], status['status']['message']

    def do_glusterVolumeSetOptionsList(self, args):
        status = self.s.glusterVolumeSetOptionsList()
        pp.pprint(status)
        return status['status']['code'], status['status']['message']

    def do_glusterVolumeReset(self, args):
        params = self._eqSplit(args)
        volumeName = params.get('volumeName', '')
        option = params.get('option', '')
        force = (params.get('force', 'no').upper() == 'YES')

        status = self.s.glusterVolumeReset(volumeName, option, force)
        return status['status']['code'], status['status']['message']

    def do_glusterHostAdd(self, args):
        params = self._eqSplit(args)
        hostName = params.get('hostName', '')

        status = self.s.glusterHostAdd(hostName)
        return status['status']['code'], status['status']['message']

    def do_glusterVolumeRebalanceStart(self, args):
        params = self._eqSplit(args)
        volumeName = params.get('volumeName', '')
        rebalanceType = params.get('rebalanceType', '')
        force = (params.get('force', 'no').upper() == 'YES')

        status = self.s.glusterVolumeRebalanceStart(volumeName,
                                                    rebalanceType,
                                                    force)
        pp.pprint(status)
        return status['status']['code'], status['status']['message']

    def do_glusterVolumeRebalanceStop(self, args):
        params = self._eqSplit(args)
        volumeName = params.get('volumeName', '')
        force = (params.get('force', 'no').upper() == 'YES')

        status = self.s.glusterVolumeRebalanceStop(volumeName, force)
        pp.pprint(status)
        return status['status']['code'], status['status']['message']

    def do_glusterVolumeRebalanceStatus(self, args):
        params = self._eqSplit(args)
        volumeName = params.get('volumeName', '')
        status = self.s.glusterVolumeRebalanceStatus(volumeName)
        pp.pprint(status)
        return status['status']['code'], status['status']['message']

    def do_glusterVolumeDelete(self, args):
        params = self._eqSplit(args)
        volumeName = params.get('volumeName', '')

        status = self.s.glusterVolumeDelete(volumeName)
        return status['status']['code'], status['status']['message']

    def do_glusterHostRemove(self, args):
        params = self._eqSplit(args)
        hostName = params.get('hostName', '')
        force = (params.get('force', 'no').upper() == 'YES')

        status = self.s.glusterHostRemove(hostName, force)
        return status['status']['code'], status['status']['message']

    def do_glusterHostRemoveByUuid(self, args):
        params = self._eqSplit(args)
        hostUuid = params.get('hostUuid', '')
        force = (params.get('force', 'no').upper() == 'YES')

        status = self.s.glusterHostRemoveByUuid(hostUuid, force)
        return status['status']['code'], status['status']['message']

    def do_glusterVolumeReplaceBrickCommitForce(self, args):
        params = self._eqSplit(args)
        volumeName = params.get('volumeName', '')
        existingBrick = params.get('existingBrick', '')
        newBrick = params.get('newBrick', '')

        status = self.s.glusterVolumeReplaceBrickCommitForce(volumeName,
                                                             existingBrick,
                                                             newBrick)
        return status['status']['code'], status['status']['message']

    def do_glusterVolumeRemoveBrickStart(self, args):
        params = self._eqSplit(args)
        volumeName = params.get('volumeName', '')
        brickList = params.get('bricks', '').split(',')
        replicaCount = params.get('replica', '')

        status = self.s.glusterVolumeRemoveBrickStart(volumeName,
                                                      brickList,
                                                      replicaCount)
        pp.pprint(status)
        return status['status']['code'], status['status']['message']

    def do_glusterVolumeRemoveBrickStop(self, args):
        params = self._eqSplit(args)
        volumeName = params.get('volumeName', '')
        brickList = params.get('bricks', '').split(',')
        replicaCount = params.get('replica', '')

        status = self.s.glusterVolumeRemoveBrickStop(volumeName,
                                                     brickList,
                                                     replicaCount)
        pp.pprint(status)
        return status['status']['code'], status['status']['message']

    def do_glusterVolumeRemoveBrickStatus(self, args):
        params = self._eqSplit(args)
        volumeName = params.get('volumeName', '')
        brickList = params.get('bricks', '').split(',')
        replicaCount = params.get('replica', '')

        status = self.s.glusterVolumeRemoveBrickStatus(volumeName,
                                                       brickList,
                                                       replicaCount)
        pp.pprint(status)
        return status['status']['code'], status['status']['message']

    def do_glusterVolumeRemoveBrickCommit(self, args):
        params = self._eqSplit(args)
        volumeName = params.get('volumeName', '')
        brickList = params.get('bricks', '').split(',')
        replicaCount = params.get('replica', '')

        status = self.s.glusterVolumeRemoveBrickCommit(volumeName,
                                                       brickList,
                                                       replicaCount)
        return status['status']['code'], status['status']['message']

    def do_glusterVolumeRemoveBrickForce(self, args):
        params = self._eqSplit(args)
        volumeName = params.get('volumeName', '')
        brickList = params.get('bricks', '').split(',')
        replicaCount = params.get('replica', '')

        status = self.s.glusterVolumeRemoveBrickForce(volumeName,
                                                      brickList,
                                                      replicaCount)
        return status['status']['code'], status['status']['message']

    def do_glusterVolumeStatus(self, args):
        params = self._eqSplit(args)
        volumeName = params.get('volumeName', '')
        brick = params.get('brick', '')
        option = params.get('option', '')

        status = self.s.glusterVolumeStatus(volumeName, brick, option)
        pp.pprint(status)
        return status['status']['code'], status['status']['message']

    def do_glusterHostsList(self, args):
        status = self.s.glusterHostsList()
        pp.pprint(status)
        return status['status']['code'], status['status']['message']

    def do_glusterVolumeProfileStart(self, args):
        params = self._eqSplit(args)
        volumeName = params.get('volumeName', '')

        status = self.s.glusterVolumeProfileStart(volumeName)
        return status['status']['code'], status['status']['message']

    def do_glusterVolumeProfileStop(self, args):
        params = self._eqSplit(args)
        volumeName = params.get('volumeName', '')

        status = self.s.glusterVolumeProfileStop(volumeName)
        return status['status']['code'], status['status']['message']

    def do_glusterVolumeProfileInfo(self, args):
        params = self._eqSplit(args)
        volumeName = params.get('volumeName', '')
        nfs = (params.get('nfs', 'no').upper() == 'YES')

        status = self.s.glusterVolumeProfileInfo(volumeName, nfs)
        pp.pprint(status)
        return status['status']['code'], status['status']['message']

    def do_glusterHooksList(self, args):
        status = self.s.glusterHooksList()
        pp.pprint(status)
        return status['status']['code'], status['status']['message']

    def do_glusterHookEnable(self, args):
        params = self._eqSplit(args)
        glusterCmd = params.get('command', '')
        level = params.get('level', '')
        hookName = params.get('hookName', '')

        status = self.s.glusterHookEnable(glusterCmd, level, hookName)
        return status['status']['code'], status['status']['message']

    def do_glusterHookDisable(self, args):
        params = self._eqSplit(args)
        glusterCmd = params.get('command', '')
        level = params.get('level', '')
        hookName = params.get('hookName', '')

        status = self.s.glusterHookDisable(glusterCmd, level, hookName)
        return status['status']['code'], status['status']['message']

    def do_glusterHookRead(self, args):
        params = self._eqSplit(args)
        glusterCmd = params.get('command', '')
        level = params.get('level', '')
        hookName = params.get('hookName', '')

        status = self.s.glusterHookRead(glusterCmd, level, hookName)
        pp.pprint(status)
        return status['status']['code'], status['status']['message']

    def do_glusterHookUpdate(self, args):
        params = self._eqSplit(args)
        glusterCmd = params.get('command', '')
        level = params.get('level', '')
        hookName = params.get('hookName', '')
        hookFile = params.get('hookFile', '')
        with open(hookFile, 'r') as f:
            hookData = f.read()
        content = base64.b64encode(hookData)
        md5sum = hashlib.md5(hookData).hexdigest()

        status = self.s.glusterHookUpdate(glusterCmd, level, hookName,
                                          content, md5sum)
        pp.pprint(status)
        return status['status']['code'], status['status']['message']

    def do_glusterHookAdd(self, args):
        params = self._eqSplit(args)
        glusterCmd = params.get('command', '')
        level = params.get('level', '')
        hookName = params.get('hookName', '')
        hookFile = params.get('hookFile', '')
        hookEnable = False
        if params.get('enable', '').upper() == 'TRUE':
            hookEnable = True
        with open(hookFile, 'r') as f:
            hookData = f.read()
        md5sum = hashlib.md5(hookData).hexdigest()
        content = base64.b64encode(hookData)

        status = self.s.glusterHookAdd(glusterCmd, level, hookName,
                                       content, md5sum, hookEnable)
        pp.pprint(status)
        return status['status']['code'], status['status']['message']

    def do_glusterHookRemove(self, args):
        params = self._eqSplit(args)
        glusterCmd = params.get('command', '')
        level = params.get('level', '')
        hookName = params.get('hookName', '')

        status = self.s.glusterHookRemove(glusterCmd, level, hookName)
        pp.pprint(status)
        return status['status']['code'], status['status']['message']

    def do_glusterHostUUIDGet(self, args):
        status = self.s.glusterHostUUIDGet()
        pp.pprint(status)
        return status['status']['code'], status['status']['message']

    def do_glusterServicesAction(self, args):
        params = self._eqSplit(args)
        serviceNames = params.get('serviceNames', '').split(',')
        action = params.get('action', '')

        status = self.s.glusterServicesAction(serviceNames, action)
        pp.pprint(status)
        return status['status']['code'], status['status']['message']

    def do_glusterServicesGet(self, args):
        params = self._eqSplit(args)
        serviceNames = params.get('serviceNames', '').split(',')

        status = self.s.glusterServicesGet(serviceNames)
        pp.pprint(status)
        return status['status']['code'], status['status']['message']

    def do_glusterTasksList(self, args):
        params = self._eqSplit(args)
        taskIds = params.get('taskIds', '')
        if taskIds:
            taskIds = taskIds.split(",")
        else:
            taskIds = []

        status = self.s.glusterTasksList(taskIds)
        pp.pprint(status)
        return status['status']['code'], status['status']['message']

    def do_glusterVolumeStatsInfoGet(self, args):
        params = self._eqSplit(args)
        volumeName = params.get('volumeName', '')

        status = self.s.glusterVolumeStatsInfoGet(volumeName)
        pp.pprint(status)
        return status['status']['code'], status['status']['message']

    def do_glusterStorageDevicesList(self, args):
        status = self.s.glusterStorageDevicesList()
        pp.pprint(status)
        return status['status']['code'], status['status']['message']

    def do_glusterVolumeGeoRepSessionStart(self, args):
        params = self._eqSplit(args)
        volumeName = params.get('volumeName', '')
        remoteUserName = params.get('remoteUserName', '')
        remoteHost = params.get('remoteHost', '')
        remoteVolumeName = params.get('remoteVolumeName', '')
        force = (params.get('force', 'no').upper() == 'YES')

        status = self.s.glusterVolumeGeoRepSessionStart(volumeName,
                                                        remoteHost,
                                                        remoteVolumeName,
                                                        remoteUserName,
                                                        force)
        pp.pprint(status)
        return status['status']['code'], status['status']['message']

    def do_glusterVolumeGeoRepSessionStop(self, args):
        params = self._eqSplit(args)
        volumeName = params.get('volumeName', '')
        remoteHost = params.get('remoteHost', '')
        remoteUserName = params.get('remoteUserName', '')
        remoteVolumeName = params.get('remoteVolumeName', '')
        force = (params.get('force', 'no').upper() == 'YES')

        status = self.s.glusterVolumeGeoRepSessionStop(volumeName,
                                                       remoteHost,
                                                       remoteVolumeName,
                                                       remoteUserName,
                                                       force)
        pp.pprint(status)
        return status['status']['code'], status['status']['message']

    def do_glusterVolumeGeoRepSessionList(self, args):
        params = self._eqSplit(args)
        volumeName = params.get('volumeName', '')
        remoteHost = params.get('remoteHost', '')
        remoteUserName = params.get('remoteUserName', '')
        remoteVolumeName = params.get('remoteVolumeName', '')

        status = self.s.glusterVolumeGeoRepSessionList(volumeName, remoteHost,
                                                       remoteVolumeName,
                                                       remoteUserName)
        pp.pprint(status)
        return status['status']['code'], status['status']['message']

    def do_glusterVolumeGeoRepSessionStatus(self, args):
        params = self._eqSplit(args)
        volumeName = params.get('volumeName', '')
        remoteHost = params.get('remoteHost', '')
        remoteUserName = params.get('remoteUserName', '')
        remoteVolumeName = params.get('remoteVolumeName', '')

        status = self.s.glusterVolumeGeoRepSessionStatus(volumeName,
                                                         remoteHost,
                                                         remoteVolumeName,
                                                         remoteUserName)

        pp.pprint(status)
        return status['status']['code'], status['status']['message']

    def do_glusterVolumeGeoRepSessionPause(self, args):
        params = self._eqSplit(args)
        volumeName = params.get('volumeName', '')
        remoteHost = params.get('remoteHost', '')
        remoteVolumeName = params.get('remoteVolumeName', '')
        remoteUserName = params.get('remoteUserName', '')
        force = (params.get('force', 'no').upper() == 'YES')

        status = self.s.glusterVolumeGeoRepSessionPause(volumeName,
                                                        remoteHost,
                                                        remoteVolumeName,
                                                        remoteUserName,
                                                        force)
        pp.pprint(status)
        return status['status']['code'], status['status']['message']

    def do_glusterVolumeGeoRepSessionResume(self, args):
        params = self._eqSplit(args)
        volumeName = params.get('volumeName', '')
        remoteHost = params.get('remoteHost', '')
        remoteVolumeName = params.get('remoteVolumeName', '')
        remoteUserName = params.get('remoteUserName', '')
        force = (params.get('force', 'no').upper() == 'YES')

        status = self.s.glusterVolumeGeoRepSessionResume(volumeName,
                                                         remoteHost,
                                                         remoteVolumeName,
                                                         remoteUserName,
                                                         force)
        pp.pprint(status)
        return status['status']['code'], status['status']['message']

    def do_glusterVolumeGeoRepConfigList(self, args):
        params = self._eqSplit(args)
        volumeName = params.get('volumeName', '')
        remoteHost = params.get('remoteHost', '')
        remoteUserName = params.get('remoteUserName', '')
        remoteVolumeName = params.get('remoteVolumeName', '')

        status = self.s.glusterVolumeGeoRepConfigList(volumeName,
                                                      remoteHost,
                                                      remoteVolumeName,
                                                      remoteUserName)
        pp.pprint(status)
        return status['status']['code'], status['status']['message']

    def do_glusterVolumeGeoRepConfigSet(self, args):
        params = self._eqSplit(args)
        volumeName = params.get('volumeName', '')
        remoteHost = params.get('remoteHost', '')
        remoteVolumeName = params.get('remoteVolumeName', '')
        remoteUserName = params.get('remoteUserName', '')
        optionName = params.get('optionName', '')
        optionValue = params.get('optionValue', '')

        status = self.s.glusterVolumeGeoRepConfigSet(volumeName,
                                                     remoteHost,
                                                     remoteVolumeName,
                                                     optionName,
                                                     optionValue,
                                                     remoteUserName)
        pp.pprint(status)
        return status['status']['code'], status['status']['message']

    def do_glusterVolumeGeoRepConfigReset(self, args):
        params = self._eqSplit(args)
        volumeName = params.get('volumeName', '')
        remoteHost = params.get('remoteHost', '')
        remoteVolumeName = params.get('remoteVolumeName', '')
        remoteUserName = params.get('remoteUserName', '')
        optionName = params.get('optionName', '')

        status = self.s.glusterVolumeGeoRepConfigReset(volumeName,
                                                       remoteHost,
                                                       remoteVolumeName,
                                                       optionName,
                                                       remoteUserName)
        pp.pprint(status)
        return status['status']['code'], status['status']['message']

    def do_glusterVolumeSnapshotCreate(self, args):
        params = self._eqSplit(args)
        volumeName = params.get('volumeName', '')
        snapName = params.get('snapName', '')
        snapDescription = params.get('snapDescription', '')
        force = (params.get('force', 'no').upper() == 'YES')

        status = self.s.glusterVolumeSnapshotCreate(volumeName,
                                                    snapName,
                                                    snapDescription,
                                                    force)
        pp.pprint(status)
        return status['status']['code'], status['status']['message']

    def do_glusterSnapshotDelete(self, args):
        params = self._eqSplit(args)
        snapName = params.get('snapName', '')

        status = self.s.glusterSnapshotDelete(snapName)
        pp.pprint(status)
        return status['status']['code'], status['status']['message']

    def do_glusterVolumeSnapshotDeleteAll(self, args):
        params = self._eqSplit(args)
        volumeName = params.get('volumeName', '')

        status = self.s.glusterVolumeSnapshotDeleteAll(volumeName)
        pp.pprint(status)
        return status['status']['code'], status['status']['message']

    def do_glusterSnapshotActivate(self, args):
        params = self._eqSplit(args)
        snapName = params.get('snapName', '')
        force = (params.get('force', 'no').upper() == 'YES')

        status = self.s.glusterSnapshotActivate(snapName, force)
        pp.pprint(status)
        return status['status']['code'], status['status']['message']

    def do_glusterSnapshotDeactivate(self, args):
        params = self._eqSplit(args)
        snapName = params.get('snapName', '')

        status = self.s.glusterSnapshotDeactivate(snapName)
        pp.pprint(status)
        return status['status']['code'], status['status']['message']

    def do_glusterSnapshotRestore(self, args):
        params = self._eqSplit(args)
        snapName = params.get('snapName', '')

        status = self.s.glusterSnapshotRestore(snapName)
        pp.pprint(status)
        return status['status']['code'], status['status']['message']

    def do_glusterVolumeSnapshotConfigList(self, args):
        params = self._eqSplit(args)
        volumeName = params.get('volumeName', '')

        status = self.s.glusterVolumeSnapshotConfigList(volumeName)

        pp.pprint(status)
        return status['status']['code'], status['status']['message']

    def do_glusterSnapshotConfigList(self, args):
        status = self.s.glusterSnapshotConfigList()
        pp.pprint(status)
        return status['status']['code'], status['status']['message']

    def do_glusterVolumeSnapshotConfigSet(self, args):
        params = self._eqSplit(args)
        volumeName = params.get('volumeName', '')
        optionName = params.get('optionName', '')
        optionValue = params.get('optionValue', '')

        status = self.s.glusterVolumeSnapshotConfigSet(volumeName,
                                                       optionName,
                                                       optionValue)
        pp.pprint(status)
        return status['status']['code'], status['status']['message']

    def do_glusterSnapshotConfigSet(self, args):
        params = self._eqSplit(args)
        optionName = params.get('optionName', '')
        optionValue = params.get('optionValue', '')

        status = self.s.glusterSnapshotConfigSet(optionName, optionValue)
        pp.pprint(status)
        return status['status']['code'], status['status']['message']

    def do_glusterVolumeSnapshotList(self, args):
        params = self._eqSplit(args)
        volumeName = params.get('volumeName', '')

        status = self.s.glusterVolumeSnapshotList(volumeName)
        pp.pprint(status)
        return status['status']['code'], status['status']['message']

    def do_glusterCreateBrick(self, args):
        params = self._eqSplit(args)
        devList = params.get('devices', '').split(',')
        brickName = params.get('brickName', '')
        mountPoint = params.get('mountPoint', '')
        fsType = params.get('fsType', '')
        raidType = params.get('raidType', '')
        raidParams = {}
        if raidType:
            raidParams['type'] = raidType.upper()
            raidParams['stripeSize'] = int(params.get('stripeSize', 0))
            raidParams['pdCount'] = int(params.get('pdCount', 0))

        status = self.s.glusterCreateBrick(brickName, mountPoint,
                                           devList, fsType, raidParams)
        pp.pprint(status)
        return status['status']['code'], status['status']['message']

    def do_glusterGeoRepKeysGet(self, args):
        status = self.s.glusterGeoRepKeysGet()
        pp.pprint(status)
        return status['status']['code'], status['status']['message']

    def do_glusterGeoRepKeysUpdate(self, args):
        params = self._eqSplit(args)
        userName = params.get('userName', '')
        geoRepPubKeys = params.get('geoRepPubKeys', '')

        status = self.s.glusterGeoRepKeysUpdate(userName,
                                                geoRepPubKeys.split('\\n'))
        pp.pprint(status)
        return status['status']['code'], status['status']['message']

    def do_glusterGeoRepMountBrokerSetup(self, args):
        params = self._eqSplit(args)
        remoteUserName = params.get('remoteUserName', '')
        remoteVolumeName = params.get('remoteVolumeName', '')
        remoteGroupName = params.get('remoteGroupName', '')
        partial = (params.get('partial', 'no').upper() == 'YES')

        status = self.s.glusterGeoRepMountBrokerSetup(remoteUserName,
                                                      remoteGroupName,
                                                      remoteVolumeName,
                                                      partial)
        pp.pprint(status)
        return status['status']['code'], status['status']['message']

    def do_glusterVolumeGeoRepSessionCreate(self, args):
        params = self._eqSplit(args)
        volumeName = params.get('volumeName', '')
        remoteUserName = params.get('remoteUserName', '')
        remoteHost = params.get('remoteHost', '')
        remoteVolumeName = params.get('remoteVolumeName', '')
        force = (params.get('force', 'no').upper() == 'YES')

        status = self.s.glusterVolumeGeoRepSessionCreate(volumeName,
                                                         remoteHost,
                                                         remoteVolumeName,
                                                         remoteUserName,
                                                         force)
        pp.pprint(status)
        return status['status']['code'], status['status']['message']

    def do_glusterVolumeGeoRepSessionDelete(self, args):
        params = self._eqSplit(args)
        volumeName = params.get('volumeName', '')
        remoteUserName = params.get('remoteUserName', '')
        remoteHost = params.get('remoteHost', '')
        remoteVolumeName = params.get('remoteVolumeName', '')

        status = self.s.glusterVolumeGeoRepSessionDelete(volumeName,
                                                         remoteHost,
                                                         remoteVolumeName,
                                                         remoteUserName)
        pp.pprint(status)
        return status['status']['code'], status['status']['message']

    def do_glusterVolumeEmptyCheck(self, args):
        params = self._eqSplit(args)
        volumeName = params.get('volumeName', '')

        status = self.s.glusterVolumeEmptyCheck(volumeName)

        pp.pprint(status)
        return status['status']['code'], status['status']['message']

    def do_glusterMetaVolumeMount(self, args):
        params = self._eqSplit(args)
        metaVolumeName = params.get('metaVolumeName', '')

        status = self.s.glusterMetaVolumeMount(metaVolumeName)
        pp.pprint(status)
        return status['status']['code'], status['status']['message']

    def do_glusterSnapshotScheduleOverride(self, args):
        params = self._eqSplit(args)
        force = (params.get('force', 'no').upper() == 'YES')

        status = self.s.glusterSnapshotScheduleOverride(force)
        pp.pprint(status)
        return status['status']['code'], status['status']['message']

    def do_glusterSnapshotScheduleReset(self, args):
        status = self.s.glusterSnapshotScheduleReset()
        pp.pprint(status)
        return status['status']['code'], status['status']['message']

    def do_glusterProcessesStop(self, args):
        status = self.s.glusterProcessesStop()
        pp.pprint(status)
        return status['status']['code'], status['status']['message']

    def do_glusterVolumeHealInfo(self, args):
        params = self._eqSplit(args)
        volumeName = params.get('volumeName', '')

        status = self.s.glusterVolumeHealInfo(volumeName)
        pp.pprint(status)
        return status['status']['code'], status['status']['message']


def getGlusterCmdDict(serv):
    return \
        {'glusterVolumeCreate': (
            serv.do_glusterVolumeCreate,
            ('volumeName=<volume_name> bricks=<brick[,brick, ...]> '
             '[replica=<count>] [arbiter={yes|no}] [stripe=<count>] '
             '[transport={tcp|rdma}] [force={yes|no}]\n\t'
             '<volume_name> is name of new volume',
             '<brick[,brick, ...]> is brick(s) which will be used to '
             'create volume',
             'create gluster volume'
             )),
         'glusterVolumesList': (
             serv.do_glusterVolumesList,
             ('[volumeName=<volume_name>]\n\t'
              '[remoteServer=<remote_server]\n\t'
              '<volume_name> is existing volume name '
              '<remote_server> is a remote host name ',
              'list all or given gluster volume details'
              )),
         'glusterVolumeStart': (
             serv.do_glusterVolumeStart,
             ('volumeName=<volume_name> [force={yes|no}]\n\t'
              '<volume_name> is existing volume name',
              'start gluster volume'
              )),
         'glusterVolumeStop': (
             serv.do_glusterVolumeStop,
             ('volumeName=<volume_name> [force={yes|no}]\n\t'
              '<volume_name> is existing volume name',
              'stop gluster volume'
              )),
         'glusterVolumeBrickAdd': (
             serv.do_glusterVolumeBrickAdd,
             ('volumeName=<volume_name> bricks=<brick[,brick, ...]> '
              '[replica=<count>] [stripe=<count>] [force={yes|no}]\n\t'
              '<volume_name> is existing volume name\n\t'
              '<brick[,brick, ...]> is new brick(s) which will be added to '
              'the volume',
              'add bricks to gluster volume'
              )),
         'glusterVolumeSet': (
             serv.do_glusterVolumeSet,
             ('volumeName=<volume_name> option=<option> value=<value>\n\t'
              '<volume_name> is existing volume name\n\t'
              '<option> is volume option\n\t'
              '<value> is value to volume option',
              'set gluster volume option'
              )),
         'glusterVolumeSetOptionsList': (
             serv.do_glusterVolumeSetOptionsList,
             ('',
              'list gluster volume set options'
              )),
         'glusterVolumeReset': (
             serv.do_glusterVolumeReset,
             ('volumeName=<volume_name> [option=<option>] [force={yes|no}]\n\t'
              '<volume_name> is existing volume name',
              'reset gluster volume or volume option'
              )),
         'glusterHostAdd': (
             serv.do_glusterHostAdd,
             ('hostName=<host>\n\t'
              '<host> is hostname or ip address of new server',
              'add new server to gluster cluster'
              )),
         'glusterVolumeRebalanceStart': (
             serv.do_glusterVolumeRebalanceStart,
             ('volumeName=<volume_name> [rebalanceType=fix-layout] '
              '[force={yes|no}]\n\t'
              '<volume_name> is existing volume name',
              'start volume rebalance'
              )),
         'glusterVolumeRebalanceStop': (
             serv.do_glusterVolumeRebalanceStop,
             ('volumeName=<volume_name> [force={yes|no}]\n\t'
              '<volume_name> is existing volume name',
              'stop volume rebalance'
              )),
         'glusterVolumeRebalanceStatus': (
             serv.do_glusterVolumeRebalanceStatus,
             ('volumeName=<volume_name>\n\t'
              '<volume_name> is existing volume name',
              'get volume rebalance status'
              )),
         'glusterVolumeDelete': (
             serv.do_glusterVolumeDelete,
             ('volumeName=<volume_name> \n\t<volume_name> is existing '
              'volume name',
              'delete gluster volume'
              )),
         'glusterHostRemove': (
             serv.do_glusterHostRemove,
             ('hostName=<host> [force={yes|no}]\n\t'
              '<host> is hostname or ip address of a server in '
              'gluster cluster',
              'remove server from gluster cluster'
              )),
         'glusterHostRemoveByUuid': (
             serv.do_glusterHostRemoveByUuid,
             ('hostUuid=<hostUuid> [force={yes|no}]\n\t'
              '<hostUuid> is UUID of the host in '
              'gluster cluster',
              'remove server from gluster cluster'
              )),
         'glusterVolumeReplaceBrickCommitForce': (
             serv.do_glusterVolumeReplaceBrickCommitForce,
             ('volumeName=<volume_name> existingBrick=<existing_brick> '
              'newBrick=<new_brick> \n\t'
              '<volume_name> is existing volume name\n\t'
              '<existing_brick> is existing brick\n\t'
              '<new_brick> is new brick',
              'commit volume replace brick'
              )),
         'glusterVolumeRemoveBrickStart': (
             serv.do_glusterVolumeRemoveBrickStart,
             ('volumeName=<volume_name> bricks=<brick[,brick, ...]> '
              '[replica=<count>]\n\t'
              '<volume_name> is existing volume name\n\t'
              '<brick[,brick, ...]> is existing brick(s)',
              'start volume remove bricks'
              )),
         'glusterVolumeRemoveBrickStop': (
             serv.do_glusterVolumeRemoveBrickStop,
             ('volumeName=<volume_name> bricks=<brick[,brick, ...]> '
              '[replica=<count>]\n\t'
              '<volume_name> is existing volume name\n\t'
              '<brick[,brick, ...]> is existing brick(s)',
              'stop volume remove bricks'
              )),
         'glusterVolumeRemoveBrickStatus': (
             serv.do_glusterVolumeRemoveBrickStatus,
             ('volumeName=<volume_name> bricks=<brick[,brick, ...]> '
              '[replica=<count>]\n\t'
              '<volume_name> is existing volume name\n\t'
              '<brick[,brick, ...]> is existing brick(s)',
              'get volume remove bricks status'
              )),
         'glusterVolumeRemoveBrickCommit': (
             serv.do_glusterVolumeRemoveBrickCommit,
             ('volumeName=<volume_name> bricks=<brick[,brick, ...]> '
              '[replica=<count>]\n\t'
              '<volume_name> is existing volume name\n\t'
              '<brick[,brick, ...]> is existing brick(s)',
              'commit volume remove bricks'
              )),
         'glusterVolumeRemoveBrickForce': (
             serv.do_glusterVolumeRemoveBrickForce,
             ('volumeName=<volume_name> bricks=<brick[,brick, ...]> '
              '[replica=<count>]\n\t'
              '<volume_name> is existing volume name\n\t'
              '<brick[,brick, ...]> is existing brick(s)',
              'force volume remove bricks'
              )),
         'glusterVolumeStatus': (
             serv.do_glusterVolumeStatus,
             ('volumeName=<volume_name> [brick=<existing_brick>] '
              '[option={detail | clients | mem}]\n\t'
              '<volume_name> is existing volume name\n\t'
              'option=detail gives brick detailed status\n\t'
              'option=clients gives clients status\n\t'
              'option=mem gives memory status\n\t',
              'get volume status of given volume with its all brick or '
              'specified brick'
              )),
         'glusterHostsList': (
             serv.do_glusterHostsList,
             ('',
              'list host info'
              )),
         'glusterVolumeProfileStart': (
             serv.do_glusterVolumeProfileStart,
             ('volumeName=<volume_name>\n\t'
              '<volume_name> is existing volume name',
              'start gluster volume profile'
              )),
         'glusterVolumeProfileStop': (
             serv.do_glusterVolumeProfileStop,
             ('volumeName=<volume_name>\n\t'
              '<volume_name> is existing volume name',
              'stop gluster volume profile'
              )),
         'glusterVolumeProfileInfo': (
             serv.do_glusterVolumeProfileInfo,
             ('volumeName=<volume_name> [nfs={yes|no}]\n\t'
              '<volume_name> is existing volume name',
              'get gluster volume profile info'
              )),
         'glusterHooksList': (
             serv.do_glusterHooksList,
             ('',
              'list hooks info'
              )),
         'glusterHookEnable': (
             serv.do_glusterHookEnable,
             ('command=<gluster_command> level={pre|post} '
              'hookName=<hook_name>\n\t'
              '<hook_name> is an existing hook name',
              'Enable hook script'
              )),
         'glusterHookDisable': (
             serv.do_glusterHookDisable,
             ('command=<gluster_command> level={pre|post} '
              'hookName=<hook_name>\n\t'
              '<hook_name> is an existing hook name',
              'Disable hook script'
              )),
         'glusterHookRead': (
             serv.do_glusterHookRead,
             ('command=<gluster_command> level={pre|post} '
              'hookName=<hook_name>\n\t'
              '<hook_name> is an existing hook name',
              'Read hook script'
              )),
         'glusterHookUpdate': (
             serv.do_glusterHookUpdate,
             ('command=<gluster_command> level={pre|post} '
              'hookName=<hook_name> hookFile=<hook_file>\n\t'
              '<hook_name> is an existing hook name',
              '<hook_file> is the input hook file name contains hook data',
              'Update hook script'
              )),
         'glusterHookAdd': (
             serv.do_glusterHookAdd,
             ('command=<gluster_command> level={pre|post} '
              'hookName=<hook_name> hookFile=<hook_file> '
              ' enable={true|false}\n\t'
              '<hook_name> is a new hook name',
              '<hook_file> is the input hook file name contains hook data',
              'Add hook script'
              )),
         'glusterHookRemove': (
             serv.do_glusterHookRemove,
             ('command=<gluster_command> level={pre|post} '
              'hookName=<hook_name>\n\t'
              '<hook_name> is an existing hook name',
              'Remove hook script'
              )),
         'glusterHostUUIDGet': (
             serv.do_glusterHostUUIDGet,
             ('',
              'get gluster UUID of the host'
              )),
         'glusterServicesAction': (
             serv.do_glusterServicesAction,
             ('serviceNames=<service1[,service2,..]> action=<action>\n\t',
              'serviceNames - list of services on which action needs '
              'to be performed',
              'action can be start/stop or restart',
              'Performs start/stop/restart of gluster services'
              )),
         'glusterServicesGet': (
             serv.do_glusterServicesGet,
             ('serviceNames=<service1[,service2,..]>',
              'Returns status of all gluster services if serviceName is '
              'not set'
              '(swift, glusterd, smb, memcached)'
              )),
         'glusterTasksList': (
             serv.do_glusterTasksList,
             ('[taskIds=<task_id1,task_id2,..>]',
              'list all or given gluster tasks'
              )),
         'glusterVolumeStatsInfoGet': (
             serv.do_glusterVolumeStatsInfoGet,
             ('volumeName=<volume name>',
              'Returns total, free and used space(bytes) of gluster volume'
              )),
         'glusterStorageDevicesList': (
             serv.do_glusterStorageDevicesList,
             ('',
              'list all disk info of the host'
              )),
         'glusterVolumeGeoRepSessionStart': (
             serv.do_glusterVolumeGeoRepSessionStart,
             ('volumeName=<volume_name> '
              'remoteHost=<remote_host> '
              'remoteVolumeName=<remote_volume_name> '
              'remoteUserName=<remote_user_name>'
              '[force={yes|no}]\n\t'
              '<remote_host> is IP/dns name of host in remote Gluster cluster.'
              '<remote_volume_name> volume name in remote gluster cluster.',
              'start the geo-replication session'
              )),
         'glusterVolumeGeoRepSessionStop': (
             serv.do_glusterVolumeGeoRepSessionStop,
             ('volumeName=<volume_name> '
              'remoteHost=<remote_host> '
              'remoteVolumeName=<remote_volume_name> '
              'remoteUserName=<remote_user_name>'
              '[force={yes|no}]\n\t'
              '<remote_host> is IP/dns name of host in remote Gluster cluster.'
              '<remote_volume_name> volume name in remote gluster cluster.',
              'stop the geo-replication session'
              )),
         'glusterVolumeGeoRepSessionList': (
             serv.do_glusterVolumeGeoRepSessionList,
             ('volumeName=<volume_name> '
              'remoteHost=<remote_host> '
              'remoteVolumeName=<remote_volume_name> '
              'remoteUserName=<remote_user_name>'
              '<remote_host> is IP/dns name of host in remote Gluster cluster.'
              '<remote_volume_name> volume name in remote gluster cluster.',
              'list the geo-replication sessions'
              )),
         'glusterVolumeGeoRepSessionStatus': (
             serv.do_glusterVolumeGeoRepSessionStatus,
             ('volumeName=<volume_name> '
              'remoteHost=<remote_host> '
              'remoteVolumeName=<remote_volume_name> '
              'remoteUserName=<remote_user_name>'
              '<remote_host> is IP/dns name of host in remote Gluster cluster.'
              '<remote_volume_name> volume name in remote gluster cluster.',
              'get the geo-replication session status'
              )),
         'glusterVolumeGeoRepSessionPause': (
             serv.do_glusterVolumeGeoRepSessionPause,
             ('volumeName=<volume_name> '
              'remoteHost=<remote_host> '
              'remoteVolumeName=<remote_volume_name> '
              'remoteUserName=<remote_user_name>'
              '[force={yes|no}]\n\t'
              '<remote_host> is IP/dns name of host in remote Gluster cluster.'
              '<remote_volume_name> volume name in remote gluster cluster.',
              'pause the geo-replication session'
              )),
         'glusterVolumeGeoRepSessionResume': (
             serv.do_glusterVolumeGeoRepSessionResume,
             ('volumeName=<volume_name> '
              'remoteHost=<remote_host> '
              'remoteVolumeName=<remote_volume_name> '
              'remoteUserName=<remote_user_name>'
              '[force={yes|no}]\n\t'
              '<remote_host> is IP/dns name of host in remote Gluster cluster.'
              '<remote_volume_name> volume name in remote gluster cluster.',
              'resume the geo-replication session'
              )),
         'glusterVolumeGeoRepConfigList': (
             serv.do_glusterVolumeGeoRepConfigList,
             ('volumeName=<volume_name> '
              'remoteHost=<remote_host> '
              'remoteVolumeName=<remote_volume_name> '
              'remoteUserName=<remote_user_name>'
              '<remote_host> is IP/dns name of host in remote Gluster cluster.'
              '<remote_volume_name> volume name in remote gluster cluster.',
              'Get the list of geo-replication configuration for a session'
              )),
         'glusterVolumeGeoRepConfigSet': (
             serv.do_glusterVolumeGeoRepConfigSet,
             ('volumeName=<volume_name> '
              'remoteHost=<remote_host> '
              'remoteVolumeName=<remote_volume_name> '
              'optionName=<option_name>'
              'optionValue=<option_value>'
              'remoteUserName=<remote_user_name>'
              '<remote_host> is IP/dns name of host in remote Gluster cluster.'
              '<remote_volume_name> volume name in remote gluster cluster.',
              'set a geo-replication configuration for a session'
              )),
         'glusterVolumeGeoRepConfigReset': (
             serv.do_glusterVolumeGeoRepConfigReset,
             ('volumeName=<volume_name> '
              'remoteHost=<remote_host> '
              'remoteVolumeName=<remote_volume_name> '
              'optionName=<option_name>'
              'remoteUserName=<remote_user_name>'
              '<remote_host> is IP/dns name of host in remote Gluster cluster.'
              '<remote_volume_name> volume name in remote gluster cluster.',
              'Reset a geo-replication configuration to its default value'
              )),
         'glusterVolumeSnapshotCreate': (
             serv.do_glusterVolumeSnapshotCreate,
             ('volumeName=<volume_name> snapName=<snap_name> '
              '[snapDescription=<description of snapshot>] '
              '[force={yes|no}]',
              'create gluster volume snapshot'
              )),
         'glusterVolumeSnapshotDeleteAll': (
             serv.do_glusterVolumeSnapshotDeleteAll,
             ('volumeName=<volume name>',
              'delete all snapshots for given volume'
              )),
         'glusterVolumeSnapshotConfigList': (
             serv.do_glusterVolumeSnapshotConfigList,
             ('volumeName=<volume_name>',
              'get gluster volume snapshot configuration'
              )),
         'glusterVolumeSnapshotConfigSet': (
             serv.do_glusterVolumeSnapshotConfigSet,
             ('volumeName=<volume_name>'
              'optionName=<option_name>'
              'optionValue=<option_value>',
              'Set gluster snapshot configuration at volume leval'
              )),
         'glusterSnapshotDelete': (
             serv.do_glusterSnapshotDelete,
             ('snapName=<snap_name>',
              'delete gluster volume snapshot'
              )),
         'glusterSnapshotActivate': (
             serv.do_glusterSnapshotActivate,
             ('snapName=<snap_name> '
              '[force={yes|no}]',
              'activate snapshot'
              )),
         'glusterSnapshotDeactivate': (
             serv.do_glusterSnapshotDeactivate,
             ('snapName=<snap_name>',
              'de-activate snapshot'
              )),
         'glusterSnapshotRestore': (
             serv.do_glusterSnapshotRestore,
             ('snapName=snap_name',
              'restore snapshot'
              )),
         'glusterSnapshotConfigList': (
             serv.do_glusterSnapshotConfigList,
             ('',
              'get gluster volume snapshot configuration'
              )),
         'glusterSnapshotConfigSet': (
             serv.do_glusterSnapshotConfigSet,
             ('optionName=<option_name>'
              'optionValue=<option_value>',
              'Set gluster snapshot configuration at cluster leval'
              )),
         'glusterVolumeSnapshotList': (
             serv.do_glusterVolumeSnapshotList,
             ('[volumeName=<volume_name>]',
              'snapshot list for given volume'
              )),
         'glusterCreateBrick': (
             serv.do_glusterCreateBrick,
             ('brickName=<brick_name> mountPoint=<mountPoint> '
              'devices=<device[,device, ...]> '
              '[raidType=<raid_type>] [stripeSize=<stripe_size>] '
              '[fsType=<fs_type>] [pdCount=<pd_count>] \n\n'
              '<brick_name> is the name of the brick\n'
              '<mountPoint> device mount point\n'
              '<device[,device, ...]> is the list of device name(s)\n'
              '<fsType> is the file system type of the brick \n'
              '<raid_type> is the type of raid like 6 or 10 or 0\n'
              '<stripe_size> is the stripe unit size\n'
              '<pd_count> is the total number of physical '
              'disks used in the raid\n'
              '<raid_type>, <stripe_size> and <pd_count> '
              'are the optional parameters\n',
              'This will create a brick using given input devices'
              )),
         'glusterGeoRepKeysGet': (
             serv.do_glusterGeoRepKeysGet,
             ('',
              'get geo replication public keys for all nodes in cluster'
              )),
         'glusterGeoRepKeysUpdate': (
             serv.do_glusterGeoRepKeysUpdate,
             ('userName=user_name'
              'geoRepPubKeys=geo_replication_pub_keys',
              'update geo replication public keys to authorized'
              ' keys file of user'
              )),
         'glusterGeoRepMountBrokerSetup': (
             serv.do_glusterGeoRepMountBrokerSetup,
             ('remoteUserName=remote_user_name'
              'remoteVolumeName=remote_volume_name'
              'remoteGroupName=remote_group_name',
              '[partial={yes|no}]'
              'setup mount broker for geo replication'
              )),
         'glusterVolumeGeoRepSessionCreate': (
             serv.do_glusterVolumeGeoRepSessionCreate,
             ('volumeName=<master_volume_name> '
              'remoteUserName=<remote_user_name>'
              'remoteHost=<slave_host_name> '
              'remoteVolumeName=<slave_volume_name> '
              '[force={yes|no}]\n\t'
              '<master_volume_name>existing volume name in the master node\n\t'
              '<slave_host_name>is remote slave host name or ip\n\t'
              '<slave_volume_name>existing volume name in the slave node',
              'Create the geo-replication session'
              )),
         'glusterVolumeGeoRepSessionDelete': (
             serv.do_glusterVolumeGeoRepSessionDelete,
             ('volumeName=<master_volume_name> '
              'remoteUserName=<remote_user_name>'
              'remoteHost=<slave_host_name> '
              'remoteVolumeName=<slave_volume_name> '
              '<master_volume_name>existing volume name in the master node\n\t'
              '<slave_host_name>is remote slave host name or ip\n\t'
              '<slave_volume_name>existing volume name in the slave node',
              'Delete the geo-replication session'
              )),
         'glusterVolumeEmptyCheck': (
             serv.do_glusterVolumeEmptyCheck,
             ('volumeName=<volume name>',
              'Check if the given volume is empty or not'
              )),
         'glusterMetaVolumeMount': (
             serv.do_glusterMetaVolumeMount,
             ('[volumeName=<volume name>]',
              'mount the meta-volume'
              )),
         'glusterSnapshotScheduleOverride': (
             serv.do_glusterSnapshotScheduleOverride,
             ('[force={yes|no}]\n\t',
              'override gluster snapshot scheduling'
              )),
         'glusterSnapshotScheduleReset': (
             serv.do_glusterSnapshotScheduleReset,
             ('',
              'Reset gluster snapshot scheduling'
              )),
         'glusterProcessesStop': (
             serv.do_glusterProcessesStop,
             ('',
              'Stop gluster processes'
              )),
         'glusterVolumeHealInfo': (
             serv.do_glusterVolumeHealInfo,
             ('[volumeName=<volume_name>]\n\t'
              '<volume_name> is existing volume name ',
              'lists self-heal info for the gluster volume'
              ))
         }
