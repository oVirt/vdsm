# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import
from __future__ import division

import math
import logging

from vdsm.gluster import exception as ge


log = logging.getLogger("Gluster")


def can_fence_host(vdsmProxy, hostUuid, skipFencingIfGlusterBricksUp,
                   skipFencingIfGlusterQuorumNotMet):
    volumesList = _getVolumeInfo(vdsmProxy)
    for volumeName in volumesList:
        volStatus = _getVolumeStatus(vdsmProxy, volumeName)
        if not volStatus:
            msg = ("Failed to get volume status for volume %s" % volumeName)
            return False, msg
        if skipFencingIfGlusterBricksUp:
            for brick in volStatus.get('bricks'):
                if hostUuid == brick.get('hostuuid') \
                        and brick.get('status') == 'ONLINE':
                    msg = ("Gluster brick '%s' is ONLINE." %
                           brick.get('brick'))
                    return False, msg
        if skipFencingIfGlusterQuorumNotMet \
                and int(volumesList.get(volumeName).
                        get('replicaCount')) > 1:
            if not _is_gluster_quorum_met(volumesList.get(volumeName),
                                          volStatus, hostUuid):
                msg = ("Gluster Quorum not met for volume %s" % volumeName)
                return False, msg
    return True, "Verified all gluster fencing policies and host can be fenced"


def _is_gluster_quorum_met(volumeInfo, volStatus, hostUuid):
    replicaCount = int(volumeInfo.get('replicaCount'))
    subVolumes = int(volumeInfo.get('brickCount')) // replicaCount
    quorumType = volumeInfo.get('options').get('cluster.quorum-type')
    if quorumType == "fixed":
        quorumCount = volumeInfo.get('cluster.quorum-count')
    elif quorumType == "auto":
        quorumCount = math.ceil(float(replicaCount) / 2)
    else:
        return True
    for index in range(0, subVolumes):
        subVolume = \
            volumeInfo.get('bricksInfo')[
                index * replicaCount: index * replicaCount + replicaCount]

        bricksRemainingUp = 0
        bricksGoingDown = 0

        for brick in subVolume:
            brick_status = _get_brick(brick.get('hostUuid'),
                                      brick.get('name'),
                                      volStatus)
            if brick_status.get('status') == 'ONLINE':
                if brick.get('hostUuid') == hostUuid:
                    bricksGoingDown += 1
                else:
                    bricksRemainingUp += 1
        if bricksGoingDown > 0 and bricksRemainingUp < quorumCount:
            return False
    return True


def _get_brick(hostUuid, brickName, volStatus):
    bricks = [brick for brick in volStatus.get('bricks')
              if brick.get('hostuuid') == hostUuid and
              brick.get('brick') == brickName]
    if bricks:
        return bricks[0]
    else:
        return {}


def _getVolumeInfo(vdsmProxy):
    try:
        return vdsmProxy.glusterVolumeInfo()
    except ge.GlusterCmdExecFailedException as e:
        log.warning("Failed to check gluster related fencing "
                    "policies: %s", e)
        return {}


def _getVolumeStatus(vdsmProxy, volumeName):
    try:
        return vdsmProxy.glusterVolumeStatus(volumeName)
    except ge.GlusterCmdExecFailedException as e:
        log.warning("Failed to check gluster related fencing "
                    "policies: %s", e)
        return {}
