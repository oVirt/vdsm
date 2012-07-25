#
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

import re
import xml.etree.cElementTree as etree
from functools import wraps

from vdsm import utils
import exception as ge
from hostname import getHostNameFqdn, HostNameException

_glusterCommandPath = utils.CommandPath("gluster",
                                        "/usr/sbin/gluster",
                                        )
_brickCountRegEx = re.compile('(\d+) x (\d+) = (\d+)')


def _getGlusterVolCmd():
    return [_glusterCommandPath.cmd, "--mode=script", "volume"]


def _getGlusterPeerCmd():
    return [_glusterCommandPath.cmd, "--mode=script", "peer"]


def exportToSuperVdsm(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        return func(*args, **kwargs)

    wrapper.superVdsm = True
    return wrapper


class BrickStatus:
    PAUSED = 'PAUSED'
    COMPLETED = 'COMPLETED'
    RUNNING = 'RUNNING'
    UNKNOWN = 'UNKNOWN'
    NA = 'NA'


class HostStatus:
    CONNECTED = 'CONNECTED'
    DISCONNECTED = 'DISCONNECTED'
    UNKNOWN = 'UNKNOWN'


def _execGluster(cmd):
    return utils.execCmd(cmd)


def _parseVolumeInfo(out):
    if not out[0].strip():
        del out[0]
    if out[-1].strip():
        out += [""]

    if out[0].strip().upper() == "NO VOLUMES PRESENT":
        return {}

    volumeInfoDict = {}
    volumeInfo = {}
    volumeName = None
    brickList = []
    volumeOptions = {}
    for line in out:
        line = line.strip()
        if not line:
            if volumeName and volumeInfo:
                volumeInfo["bricks"] = brickList
                volumeInfo["options"] = volumeOptions
                volumeInfoDict[volumeName] = volumeInfo
                volumeInfo = {}
                volumeName = None
                brickList = []
                volumeOptions = {}
            continue

        tokens = line.split(":", 1)
        key = tokens[0].strip().upper()
        if key == "BRICKS":
            continue
        elif key == "OPTIONS RECONFIGURED":
            continue
        elif key == "VOLUME NAME":
            volumeName = tokens[1].strip()
            volumeInfo["volumeName"] = volumeName
        elif key == "VOLUME ID":
            volumeInfo["uuid"] = tokens[1].strip()
        elif key == "TYPE":
            volumeInfo["volumeType"] = tokens[1].strip().upper()
        elif key == "STATUS":
            volumeInfo["volumeStatus"] = tokens[1].strip().upper()
        elif key == "TRANSPORT-TYPE":
            volumeInfo["transportType"] = tokens[1].strip().upper().split(',')
        elif key.startswith("BRICK"):
            brickList.append(tokens[1].strip())
        elif key == "NUMBER OF BRICKS":
            volumeInfo["brickCount"] = tokens[1].strip()
        else:
            volumeOptions[tokens[0].strip()] = tokens[1].strip()

    for volumeName, volumeInfo in volumeInfoDict.iteritems():
        if volumeInfo["volumeType"] == "REPLICATE":
            volumeInfo["replicaCount"] = volumeInfo["brickCount"]
        elif volumeInfo["volumeType"] == "STRIPE":
            volumeInfo["stripeCount"] = volumeInfo["brickCount"]
        elif volumeInfo["volumeType"] == "DISTRIBUTED-REPLICATE":
            m = _brickCountRegEx.match(volumeInfo["brickCount"])
            if m:
                volumeInfo["replicaCount"] = m.groups()[1]
            else:
                volumeInfo["replicaCount"] = ""

        elif volumeInfo["volumeType"] == "DISTRIBUTED-STRIPE":
            m = _brickCountRegEx.match(volumeInfo["brickCount"])
            if m:
                volumeInfo["stripeCount"] = m.groups()[1]
            else:
                volumeInfo["stripeCount"] = ""
    return volumeInfoDict


@exportToSuperVdsm
def volumeInfo(volumeName=None):
    """
    Returns:
        {VOLUMENAME: {'brickCount': BRICKCOUNT,
                      'bricks': [BRICK1, BRICK2, ...],
                      'options': {OPTION: VALUE, ...},
                      'transportType': [TCP,RDMA, ...],
                      'uuid': UUID,
                      'volumeName': NAME,
                      'volumeStatus': STATUS,
                      'volumeType': TYPE}, ...}
    """
    command = _getGlusterVolCmd() + ["info"]
    if volumeName:
        command.append(volumeName)
    rc, out, err = _execGluster(command)
    if rc:
        raise ge.GlusterVolumesListFailedException(rc, out, err)
    else:
        return _parseVolumeInfo(out)


@exportToSuperVdsm
def volumeCreate(volumeName, brickList, replicaCount=0, stripeCount=0,
                 transportList=[]):
    command = _getGlusterVolCmd() + ["create", volumeName]
    if stripeCount:
        command += ["stripe", "%s" % stripeCount]
    if replicaCount:
        command += ["replica", "%s" % replicaCount]
    if transportList:
        command += ["transport", ','.join(transportList)]
    command += brickList

    rc, out, err = _execGluster(command)
    if rc:
        raise ge.GlusterVolumeCreateFailedException(rc, out, err)
    else:
        return True


@exportToSuperVdsm
def volumeStart(volumeName, force=False):
    command = _getGlusterVolCmd() + ["start", volumeName]
    if force:
        command.append('force')
    rc, out, err = _execGluster(command)
    if rc:
        raise ge.GlusterVolumeStartFailedException(rc, out, err)
    else:
        return True


@exportToSuperVdsm
def volumeStop(volumeName, force=False):
    command = _getGlusterVolCmd() + ["stop", volumeName]
    if force:
        command.append('force')
    rc, out, err = _execGluster(command)
    if rc:
        raise ge.GlusterVolumeStopFailedException(rc, out, err)
    else:
        return True


@exportToSuperVdsm
def volumeDelete(volumeName):
    rc, out, err = _execGluster(_getGlusterVolCmd() + ["delete", volumeName])
    if rc:
        raise ge.GlusterVolumeDeleteFailedException(rc, out, err)
    else:
        return True


@exportToSuperVdsm
def volumeSet(volumeName, option, value):
    rc, out, err = _execGluster(_getGlusterVolCmd() + ["set", volumeName,
                                                       option, value])
    if rc:
        raise ge.GlusterVolumeSetFailedException(rc, out, err)
    else:
        return True


def _parseVolumeSetHelpXml(out):
    optionList = []
    tree = etree.fromstring('\n'.join(out))
    for el in tree.findall('option'):
        option = {}
        for ch in el.getchildren():
            option[ch.tag] = ch.text or ''
        optionList.append(option)
    return optionList


@exportToSuperVdsm
def volumeSetHelpXml():
    rc, out, err = _execGluster(_getGlusterVolCmd() + ["set", 'help-xml'])
    if rc:
        raise ge.GlusterVolumeSetHelpXmlFailedException(rc, out, err)
    else:
        return _parseVolumeSetHelpXml(out)


@exportToSuperVdsm
def volumeReset(volumeName, option='', force=False):
    command = _getGlusterVolCmd() + ['reset', volumeName]
    if option:
        command.append(option)
    if force:
        command.append('force')
    rc, out, err = _execGluster(command)
    if rc:
        raise ge.GlusterVolumeResetFailedException(rc, out, err)
    else:
        return True


@exportToSuperVdsm
def volumeAddBrick(volumeName, brickList,
                   replicaCount=0, stripeCount=0):
    command = _getGlusterVolCmd() + ["add-brick", volumeName]
    if stripeCount:
        command += ["stripe", "%s" % stripeCount]
    if replicaCount:
        command += ["replica", "%s" % replicaCount]
    command += brickList

    rc, out, err = _execGluster(command)
    if rc:
        raise ge.GlusterVolumeBrickAddFailedException(rc, out, err)
    else:
        return True


@exportToSuperVdsm
def volumeRebalanceStart(volumeName, rebalanceType="", force=False):
    command = _getGlusterVolCmd() + ["rebalance", volumeName]
    if rebalanceType:
        command.append(rebalanceType)
    command.append("start")
    if force:
        command.append("force")
    rc, out, err = _execGluster(command)
    if rc:
        raise ge.GlusterVolumeRebalanceStartFailedException(rc, out, err)
    else:
        return True


@exportToSuperVdsm
def volumeRebalanceStop(volumeName, force=False):
    command = _getGlusterVolCmd() + ["rebalance", volumeName, "stop"]
    if force:
        command.append('force')
    rc, out, err = _execGluster(command)
    if rc:
        raise ge.GlusterVolumeRebalanceStopFailedException(rc, out, err)
    else:
        return True


@exportToSuperVdsm
def volumeRebalanceStatus(volumeName):
    rc, out, err = _execGluster(_getGlusterVolCmd() + ["rebalance", volumeName,
                                                       "status"])
    if rc:
        raise ge.GlusterVolumeRebalanceStatusFailedException(rc, out, err)
    if 'in progress' in out[0]:
        return BrickStatus.RUNNING, "\n".join(out)
    elif 'complete' in out[0]:
        return BrickStatus.COMPLETED, "\n".join(out)
    else:
        return BrickStatus.UNKNOWN, "\n".join(out)


@exportToSuperVdsm
def volumeReplaceBrickStart(volumeName, existingBrick, newBrick):
    rc, out, err = _execGluster(_getGlusterVolCmd() + ["replace-brick",
                                                       volumeName,
                                                       existingBrick, newBrick,
                                                       "start"])
    if rc:
        raise ge.GlusterVolumeReplaceBrickStartFailedException(rc, out, err)
    else:
        return True


@exportToSuperVdsm
def volumeReplaceBrickAbort(volumeName, existingBrick, newBrick):
    rc, out, err = _execGluster(_getGlusterVolCmd() + ["replace-brick",
                                                       volumeName,
                                                       existingBrick, newBrick,
                                                       "abort"])
    if rc:
        raise ge.GlusterVolumeReplaceBrickAbortFailedException(rc, out, err)
    else:
        return True


@exportToSuperVdsm
def volumeReplaceBrickPause(volumeName, existingBrick, newBrick):
    rc, out, err = _execGluster(_getGlusterVolCmd() + ["replace-brick",
                                                       volumeName,
                                                       existingBrick, newBrick,
                                                       "pause"])
    if rc:
        raise ge.GlusterVolumeReplaceBrickPauseFailedException(rc, out, err)
    else:
        return True


@exportToSuperVdsm
def volumeReplaceBrickStatus(volumeName, existingBrick, newBrick):
    rc, out, err = _execGluster(_getGlusterVolCmd() + ["replace-brick",
                                                       volumeName,
                                                       existingBrick, newBrick,
                                                       "status"])
    if rc:
        raise ge.GlusterVolumeReplaceBrickStatusFailedException(rc, out,
                                                                err)
    message = "\n".join(out)
    statLine = out[0].strip().upper()
    if BrickStatus.PAUSED in statLine:
        return BrickStatus.PAUSED, message
    elif statLine.endswith('MIGRATION COMPLETE'):
        return BrickStatus.COMPLETED, message
    elif statLine.startswith('NUMBER OF FILES MIGRATED'):
        return BrickStatus.RUNNING, message
    elif statLine.endswith("UNKNOWN"):
        return BrickStatus.UNKNOWN, message
    else:
        return BrickStatus.NA, message


@exportToSuperVdsm
def volumeReplaceBrickCommit(volumeName, existingBrick, newBrick,
                             force=False):
    command = _getGlusterVolCmd() + ["replace-brick", volumeName,
                                     existingBrick, newBrick, "commit"]
    if force:
        command.append('force')
    rc, out, err = _execGluster(command)
    if rc:
        raise ge.GlusterVolumeReplaceBrickCommitFailedException(rc, out,
                                                                err)
    else:
        return True


@exportToSuperVdsm
def volumeRemoveBrickStart(volumeName, brickList, replicaCount=0):
    command = _getGlusterVolCmd() + ["remove-brick", volumeName]
    if replicaCount:
        command += ["replica", "%s" % replicaCount]
    command += brickList + ["start"]

    rc, out, err = _execGluster(command)
    if rc:
        raise ge.GlusterVolumeRemoveBrickStartFailedException(rc, out, err)
    else:
        return True


@exportToSuperVdsm
def volumeRemoveBrickStop(volumeName, brickList, replicaCount=0):
    command = _getGlusterVolCmd() + ["remove-brick", volumeName]
    if replicaCount:
        command += ["replica", "%s" % replicaCount]
    command += brickList + ["stop"]
    rc, out, err = _execGluster(command)

    if rc:
        raise ge.GlusterVolumeRemoveBrickStopFailedException(rc, out, err)
    else:
        return True


@exportToSuperVdsm
def volumeRemoveBrickStatus(volumeName, brickList, replicaCount=0):
    command = _getGlusterVolCmd() + ["remove-brick", volumeName]
    if replicaCount:
        command += ["replica", "%s" % replicaCount]
    command += brickList + ["status"]
    rc, out, err = _execGluster(command)

    if rc:
        raise ge.GlusterVolumeRemoveBrickStatusFailedException(rc, out, err)
    else:
        return "\n".join(out)


@exportToSuperVdsm
def volumeRemoveBrickCommit(volumeName, brickList, replicaCount=0):
    command = _getGlusterVolCmd() + ["remove-brick", volumeName]
    if replicaCount:
        command += ["replica", "%s" % replicaCount]
    command += brickList + ["commit"]
    rc, out, err = _execGluster(command)

    if rc:
        raise ge.GlusterVolumeRemoveBrickCommitFailedException(rc, out, err)
    else:
        return True


@exportToSuperVdsm
def volumeRemoveBrickForce(volumeName, brickList, replicaCount=0):
    command = _getGlusterVolCmd() + ["remove-brick", volumeName]
    if replicaCount:
        command += ["replica", "%s" % replicaCount]
    command += brickList + ["force"]
    rc, out, err = _execGluster(command)

    if rc:
        raise ge.GlusterVolumeRemoveBrickForceFailedException(rc, out, err)
    else:
        return True


@exportToSuperVdsm
def peerProbe(hostName):
    rc, out, err = _execGluster(_getGlusterPeerCmd() + ["probe", hostName])
    if rc:
        raise ge.GlusterHostAddFailedException(rc, out, err)
    else:
        return True


@exportToSuperVdsm
def peerDetach(hostName, force=False):
    command = _getGlusterPeerCmd() + ["detach", hostName]
    if force:
        command.append('force')
    rc, out, err = _execGluster(command)
    if rc:
        raise ge.GlusterHostRemoveFailedException(rc, out, err)
    else:
        return True


def _getGlusterHostName():
    try:
        return getHostNameFqdn()
    except HostNameException:
        return ''


def _getGlusterUuid():
    try:
        with open('/var/lib/glusterd/glusterd.info') as f:
            return dict(map(lambda x: x.strip().split('=', 1),
                            f)).get('UUID', '')
    except IOError:
        return ''


def _parsePeerStatus(out, gHostName, gUuid, gStatus):
    if not out[0].strip():
        del out[0]
    if out[-1].strip():
        out += [""]

    hostList = [{'hostname': gHostName, 'uuid': gUuid, 'status': gStatus}]
    if out[0].strip().upper() == "NO PEERS PRESENT":
        return hostList
    hostName = uuid = status = None
    for line in out:
        line = line.strip()
        if not line:
            if hostName != None and uuid != None and status != None:
                hostList.append({'hostname': hostName, 'uuid': uuid,
                                 'status': status})
                hostName = uuid = status = None
        tokens = line.split(":", 1)
        key = tokens[0].strip().upper()
        if key == "HOSTNAME":
            hostName = tokens[1].strip()
        elif key == "UUID":
            uuid = tokens[1].strip()
        elif key == "STATE":
            statusValue = tokens[1].strip()
            if '(Connected)' in statusValue:
                status = HostStatus.CONNECTED
            elif '(Disconnected)' in statusValue:
                status = HostStatus.DISCONNECTED
            else:
                status = HostStatus.UNKNOWN
    return hostList


@exportToSuperVdsm
def peerStatus():
    """
    Returns:
        [{'hostname': HOSTNAME, 'uuid': UUID, 'status': STATE}, ...]
    """
    rc, out, err = _execGluster(_getGlusterPeerCmd() + ["status"])
    if rc:
        raise ge.GlusterHostListFailedException(rc, out, err)
    else:
        return _parsePeerStatus(out, _getGlusterHostName(),
                                _getGlusterUuid(), HostStatus.CONNECTED)
