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

import xml.etree.cElementTree as etree

from vdsm import utils
from vdsm import netinfo
import exception as ge
from hostname import getHostNameFqdn, HostNameException
from . import makePublic

_glusterCommandPath = utils.CommandPath("gluster",
                                        "/usr/sbin/gluster",
                                        )


if hasattr(etree, 'ParseError'):
    _etreeExceptions = (etree.ParseError, AttributeError, ValueError)
else:
    _etreeExceptions = (SyntaxError, AttributeError, ValueError)


def _getGlusterVolCmd():
    return [_glusterCommandPath.cmd, "--mode=script", "volume"]


def _getGlusterPeerCmd():
    return [_glusterCommandPath.cmd, "--mode=script", "peer"]


def _getGlusterSystemCmd():
    return [_glusterCommandPath.cmd, "system::"]


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


class VolumeStatus:
    ONLINE = 'ONLINE'
    OFFLINE = 'OFFLINE'


class TransportType:
    TCP = 'TCP'
    RDMA = 'RDMA'


class TaskType:
    REBALANCE = 'REBALANCE'
    REPLACE_BRICK = 'REPLACE_BRICK'
    REMOVE_BRICK = 'REMOVE_BRICK'


def _execGluster(cmd):
    return utils.execCmd(cmd)


def _execGlusterXml(cmd):
    cmd.append('--xml')
    rc, out, err = utils.execCmd(cmd)
    if rc != 0:
        raise ge.GlusterCmdExecFailedException(rc, out, err)
    try:
        tree = etree.fromstring('\n'.join(out))
        rv = int(tree.find('opRet').text)
        msg = tree.find('opErrstr').text
        errNo = int(tree.find('opErrno').text)
    except _etreeExceptions:
        raise ge.GlusterXmlErrorException(err=out)
    if rv == 0:
        return tree
    else:
        if errNo != 0:
            rv = errNo
        raise ge.GlusterCmdFailedException(rc=rv, err=[msg])


def _getLocalIpAddress():
    for ip in netinfo.getIpAddresses():
        if not ip.startswith('127.'):
            return ip
    return ''


def _getGlusterHostName():
    try:
        return getHostNameFqdn()
    except HostNameException:
        return ''


@makePublic
def hostUUIDGet():
    command = _getGlusterSystemCmd() + ["uuid", "get"]
    rc, out, err = _execGluster(command)
    if rc == 0:
        for line in out:
            if line.startswith('UUID: '):
                return line[6:]

    raise ge.GlusterHostUUIDNotFoundException()


def _parseVolumeStatus(tree):
    status = {'name': tree.find('volStatus/volumes/volume/volName').text,
              'bricks': [],
              'nfs': [],
              'shd': []}
    hostname = _getLocalIpAddress() or _getGlusterHostName()
    for el in tree.findall('volStatus/volumes/volume/node'):
        value = {}

        for ch in el.getchildren():
            value[ch.tag] = ch.text or ''

        if value['path'] == 'localhost':
            value['path'] = hostname

        if value['status'] == '1':
            value['status'] = 'ONLINE'
        else:
            value['status'] = 'OFFLINE'

        if value['hostname'] == 'NFS Server':
            status['nfs'].append({'hostname': value['path'],
                                  'port': value['port'],
                                  'status': value['status'],
                                  'pid': value['pid']})
        elif value['hostname'] == 'Self-heal Daemon':
            status['shd'].append({'hostname': value['path'],
                                  'status': value['status'],
                                  'pid': value['pid']})
        else:
            status['bricks'].append({'brick': '%s:%s' % (value['hostname'],
                                                         value['path']),
                                     'port': value['port'],
                                     'status': value['status'],
                                     'pid': value['pid']})
    return status


def _parseVolumeStatusDetail(tree):
    status = {'name': tree.find('volStatus/volumes/volume/volName').text,
              'bricks': []}
    for el in tree.findall('volStatus/volumes/volume/node'):
        value = {}

        for ch in el.getchildren():
            value[ch.tag] = ch.text or ''

        sizeTotal = int(value['sizeTotal'])
        value['sizeTotal'] = sizeTotal / (1024.0 * 1024.0)
        sizeFree = int(value['sizeFree'])
        value['sizeFree'] = sizeFree / (1024.0 * 1024.0)
        status['bricks'].append({'brick': '%s:%s' % (value['hostname'],
                                                     value['path']),
                                 'sizeTotal': '%.3f' % (value['sizeTotal'],),
                                 'sizeFree': '%.3f' % (value['sizeFree'],),
                                 'device': value['device'],
                                 'blockSize': value['blockSize'],
                                 'mntOptions': value['mntOptions'],
                                 'fsName': value['fsName']})
    return status


def _parseVolumeStatusClients(tree):
    status = {'name': tree.find('volStatus/volumes/volume/volName').text,
              'bricks': []}
    for el in tree.findall('volStatus/volumes/volume/node'):
        hostname = el.find('hostname').text
        path = el.find('path').text

        clientsStatus = []
        for c in el.findall('clientsStatus/client'):
            clientValue = {}
            for ch in c.getchildren():
                clientValue[ch.tag] = ch.text or ''
            clientsStatus.append({'hostname': clientValue['hostname'],
                                  'bytesRead': clientValue['bytesRead'],
                                  'bytesWrite': clientValue['bytesWrite']})

        status['bricks'].append({'brick': '%s:%s' % (hostname, path),
                                 'clientsStatus': clientsStatus})
    return status


def _parseVolumeStatusMem(tree):
    status = {'name': tree.find('volStatus/volumes/volume/volName').text,
              'bricks': []}
    for el in tree.findall('volStatus/volumes/volume/node'):
        brick = {'brick': '%s:%s' % (el.find('hostname').text,
                                     el.find('path').text),
                 'mallinfo': {},
                 'mempool': []}

        for ch in el.find('memStatus/mallinfo').getchildren():
            brick['mallinfo'][ch.tag] = ch.text or ''

        for c in el.findall('memStatus/mempool/pool'):
            mempool = {}
            for ch in c.getchildren():
                mempool[ch.tag] = ch.text or ''
            brick['mempool'].append(mempool)

        status['bricks'].append(brick)
    return status


@makePublic
def volumeStatus(volumeName, brick=None, option=None):
    """
    Get volume status

    Arguments:
       * VolumeName
       * brick
       * option = 'detail' or 'clients' or 'mem' or None
    Returns:
       When option=None,
         {'name': NAME,
          'bricks': [{'brick': BRICK,
                      'port': PORT,
                      'status': STATUS,
                      'pid': PID}, ...],
          'nfs': [{'hostname': HOST,
                   'port': PORT,
                   'status': STATUS,
                   'pid': PID}, ...],
          'shd: [{'hostname': HOST,
                  'status': STATUS,
                  'pid': PID}, ...]}

      When option='detail',
         {'name': NAME,
          'bricks': [{'brick': BRICK,
                      'sizeTotal': SIZE,
                      'sizeFree': FREESIZE,
                      'device': DEVICE,
                      'blockSize': BLOCKSIZE,
                      'mntOptions': MOUNTOPTIONS,
                      'fsName': FSTYPE}, ...]}

       When option='clients':
         {'name': NAME,
          'bricks': [{'brick': BRICK,
                      'clientsStatus': [{'hostname': HOST,
                                         'bytesRead': BYTESREAD,
                                         'bytesWrite': BYTESWRITE}, ...]},
                    ...]}

       When option='mem':
         {'name': NAME,
          'bricks': [{'brick': BRICK,
                      'mallinfo': {'arena': int,
                                   'fordblks': int,
                                   'fsmblks': int,
                                   'hblkhd': int,
                                   'hblks': int,
                                   'keepcost': int,
                                   'ordblks': int,
                                   'smblks': int,
                                   'uordblks': int,
                                   'usmblks': int},
                      'mempool': [{'allocCount': int,
                                   'coldCount': int,
                                   'hotCount': int,
                                   'maxAlloc': int,
                                   'maxStdAlloc': int,
                                   'name': NAME,
                                   'padddedSizeOf': int,
                                   'poolMisses': int},...]}, ...]}
    """
    command = _getGlusterVolCmd() + ["status", volumeName]
    if brick:
        command.append(brick)
    if option:
        command.append(option)
    try:
        xmltree = _execGlusterXml(command)
    except ge.GlusterCmdFailedException as e:
        raise ge.GlusterVolumeStatusFailedException(rc=e.rc, err=e.err)
    try:
        if option == 'detail':
            return _parseVolumeStatusDetail(xmltree)
        elif option == 'clients':
            return _parseVolumeStatusClients(xmltree)
        elif option == 'mem':
            return _parseVolumeStatusMem(xmltree)
        else:
            return _parseVolumeStatus(xmltree)
    except _etreeExceptions:
        raise ge.GlusterXmlErrorException(err=[etree.tostring(xmltree)])


def _parseVolumeInfo(tree):
    """
        {VOLUMENAME: {'brickCount': BRICKCOUNT,
                      'bricks': [BRICK1, BRICK2, ...],
                      'options': {OPTION: VALUE, ...},
                      'transportType': [TCP,RDMA, ...],
                      'uuid': UUID,
                      'volumeName': NAME,
                      'volumeStatus': STATUS,
                      'volumeType': TYPE}, ...}
    """
    volumes = {}
    for el in tree.findall('volInfo/volumes/volume'):
        value = {}
        value['volumeName'] = el.find('name').text
        value['uuid'] = el.find('id').text
        value['volumeType'] = el.find('typeStr').text.upper().replace('-', '_')
        status = el.find('statusStr').text.upper()
        if status == 'STARTED':
            value["volumeStatus"] = VolumeStatus.ONLINE
        else:
            value["volumeStatus"] = VolumeStatus.OFFLINE
        value['brickCount'] = el.find('brickCount').text
        value['distCount'] = el.find('distCount').text
        value['stripeCount'] = el.find('stripeCount').text
        value['replicaCount'] = el.find('replicaCount').text
        transportType = el.find('transport').text
        if transportType == '0':
            value['transportType'] = [TransportType.TCP]
        elif transportType == '1':
            value['transportType'] = [TransportType.RDMA]
        else:
            value['transportType'] = [TransportType.TCP, TransportType.RDMA]
        value['bricks'] = []
        value['options'] = {}
        value['bricksInfo'] = []
        for b in el.findall('bricks/brick'):
            value['bricks'].append(b.text)
        for o in el.findall('options/option'):
            value['options'][o.find('name').text] = o.find('value').text
        for d in el.findall('bricks/brick'):
            brickDetail = {}
            #this try block is to maintain backward compatibility
            #it returns an empty list when gluster doesnot return uuid
            try:
                brickDetail['name'] = d.find('name').text
                brickDetail['hostUuid'] = d.find('hostUuid').text
                value['bricksInfo'].append(brickDetail)
            except AttributeError:
                break
        volumes[value['volumeName']] = value
    return volumes


def _parseVolumeProfileInfo(tree, nfs):
    bricks = []
    if nfs:
        brickKey = 'nfs'
        bricksKey = 'nfsServers'
    else:
        brickKey = 'brick'
        bricksKey = 'bricks'
    for brick in tree.findall('volProfile/brick'):
        fopCumulative = []
        blkCumulative = []
        fopInterval = []
        blkInterval = []
        brickName = brick.find('brickName').text
        if brickName == 'localhost':
            brickName = _getLocalIpAddress() or _getGlusterHostName()
        for block in brick.findall('cumulativeStats/blockStats/block'):
            blkCumulative.append({'size': block.find('size').text,
                                  'read': block.find('reads').text,
                                  'write': block.find('writes').text})
        for fop in brick.findall('cumulativeStats/fopStats/fop'):
            fopCumulative.append({'name': fop.find('name').text,
                                  'hits': fop.find('hits').text,
                                  'latencyAvg': fop.find('avgLatency').text,
                                  'latencyMin': fop.find('minLatency').text,
                                  'latencyMax': fop.find('maxLatency').text})
        for block in brick.findall('intervalStats/blockStats/block'):
            blkInterval.append({'size': block.find('size').text,
                                'read': block.find('reads').text,
                                'write': block.find('writes').text})
        for fop in brick.findall('intervalStats/fopStats/fop'):
            fopInterval.append({'name': fop.find('name').text,
                                'hits': fop.find('hits').text,
                                'latencyAvg': fop.find('avgLatency').text,
                                'latencyMin': fop.find('minLatency').text,
                                'latencyMax': fop.find('maxLatency').text})
        bricks.append(
            {brickKey: brickName,
             'cumulativeStats': {
                 'blockStats': blkCumulative,
                 'fopStats': fopCumulative,
                 'duration': brick.find('cumulativeStats/duration').text,
                 'totalRead': brick.find('cumulativeStats/totalRead').text,
                 'totalWrite': brick.find('cumulativeStats/totalWrite').text},
             'intervalStats': {
                 'blockStats': blkInterval,
                 'fopStats': fopInterval,
                 'duration': brick.find('intervalStats/duration').text,
                 'totalRead': brick.find('intervalStats/totalRead').text,
                 'totalWrite': brick.find('intervalStats/totalWrite').text}})
    status = {'volumeName': tree.find("volProfile/volname").text,
              bricksKey: bricks}
    return status


@makePublic
def volumeInfo(volumeName=None, remoteServer=None):
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
    if remoteServer:
        command += ['--remote-host=%s' % remoteServer]
    if volumeName:
        command.append(volumeName)
    try:
        xmltree = _execGlusterXml(command)
    except ge.GlusterCmdFailedException as e:
        raise ge.GlusterVolumesListFailedException(rc=e.rc, err=e.err)
    try:
        return _parseVolumeInfo(xmltree)
    except _etreeExceptions:
        raise ge.GlusterXmlErrorException(err=[etree.tostring(xmltree)])


@makePublic
def volumeCreate(volumeName, brickList, replicaCount=0, stripeCount=0,
                 transportList=[], force=False):
    command = _getGlusterVolCmd() + ["create", volumeName]
    if stripeCount:
        command += ["stripe", "%s" % stripeCount]
    if replicaCount:
        command += ["replica", "%s" % replicaCount]
    if transportList:
        command += ["transport", ','.join(transportList)]
    command += brickList

    if force:
        command.append('force')

    try:
        xmltree = _execGlusterXml(command)
    except ge.GlusterCmdFailedException as e:
        raise ge.GlusterVolumeCreateFailedException(rc=e.rc, err=e.err)
    try:
        return {'uuid': xmltree.find('volCreate/volume/id').text}
    except _etreeExceptions:
        raise ge.GlusterXmlErrorException(err=[etree.tostring(xmltree)])


@makePublic
def volumeStart(volumeName, force=False):
    command = _getGlusterVolCmd() + ["start", volumeName]
    if force:
        command.append('force')
    rc, out, err = _execGluster(command)
    if rc:
        raise ge.GlusterVolumeStartFailedException(rc, out, err)
    else:
        return True


@makePublic
def volumeStop(volumeName, force=False):
    command = _getGlusterVolCmd() + ["stop", volumeName]
    if force:
        command.append('force')
    try:
        _execGlusterXml(command)
        return True
    except ge.GlusterCmdFailedException as e:
        raise ge.GlusterVolumeStopFailedException(rc=e.rc, err=e.err)


@makePublic
def volumeDelete(volumeName):
    command = _getGlusterVolCmd() + ["delete", volumeName]
    try:
        _execGlusterXml(command)
        return True
    except ge.GlusterCmdFailedException as e:
        raise ge.GlusterVolumeDeleteFailedException(rc=e.rc, err=e.err)


@makePublic
def volumeSet(volumeName, option, value):
    command = _getGlusterVolCmd() + ["set", volumeName, option, value]
    try:
        _execGlusterXml(command)
        return True
    except ge.GlusterCmdFailedException as e:
        raise ge.GlusterVolumeSetFailedException(rc=e.rc, err=e.err)


def _parseVolumeSetHelpXml(out):
    optionList = []
    tree = etree.fromstring('\n'.join(out))
    for el in tree.findall('option'):
        option = {}
        for ch in el.getchildren():
            option[ch.tag] = ch.text or ''
        optionList.append(option)
    return optionList


@makePublic
def volumeSetHelpXml():
    rc, out, err = _execGluster(_getGlusterVolCmd() + ["set", 'help-xml'])
    if rc:
        raise ge.GlusterVolumeSetHelpXmlFailedException(rc, out, err)
    else:
        return _parseVolumeSetHelpXml(out)


@makePublic
def volumeReset(volumeName, option='', force=False):
    command = _getGlusterVolCmd() + ['reset', volumeName]
    if option:
        command.append(option)
    if force:
        command.append('force')
    try:
        _execGlusterXml(command)
        return True
    except ge.GlusterCmdFailedException as e:
        raise ge.GlusterVolumeResetFailedException(rc=e.rc, err=e.err)


@makePublic
def volumeAddBrick(volumeName, brickList,
                   replicaCount=0, stripeCount=0, force=False):
    command = _getGlusterVolCmd() + ["add-brick", volumeName]
    if stripeCount:
        command += ["stripe", "%s" % stripeCount]
    if replicaCount:
        command += ["replica", "%s" % replicaCount]
    command += brickList
    if force:
        command.append('force')
    try:
        _execGlusterXml(command)
        return True
    except ge.GlusterCmdFailedException as e:
        raise ge.GlusterVolumeBrickAddFailedException(rc=e.rc, err=e.err)


@makePublic
def volumeRebalanceStart(volumeName, rebalanceType="", force=False):
    command = _getGlusterVolCmd() + ["rebalance", volumeName]
    if rebalanceType:
        command.append(rebalanceType)
    command.append("start")
    if force:
        command.append("force")
    try:
        xmltree = _execGlusterXml(command)
    except ge.GlusterCmdFailedException, e:
        raise ge.GlusterVolumeRebalanceStartFailedException(rc=e.rc,
                                                            err=e.err)
    try:
        return {'taskId': xmltree.find('volRebalance/task-id').text}
    except _etreeExceptions:
        raise ge.GlusterXmlErrorException(err=[etree.tostring(xmltree)])


@makePublic
def volumeRebalanceStop(volumeName, force=False):
    command = _getGlusterVolCmd() + ["rebalance", volumeName, "stop"]
    if force:
        command.append('force')
    try:
        xmltree = _execGlusterXml(command)
    except ge.GlusterCmdFailedException, e:
        raise ge.GlusterVolumeRebalanceStopFailedException(rc=e.rc,
                                                           err=e.err)

    try:
        return _parseVolumeRebalanceRemoveBrickStatus(xmltree, 'rebalance')
    except _etreeExceptions:
        raise ge.GlusterXmlErrorException(err=[etree.tostring(xmltree)])


@makePublic
def _parseVolumeRebalanceRemoveBrickStatus(xmltree, mode):
    """
    returns {'hosts': [{'name': NAME,
                       'filesScanned': INT AS STRING,
                       'filesMoved': INT AS STRING,
                       'filesFailed': INT AS STRING,
                       'filesSkipped': INT AS STRING,
                       'totalSizeMoved': INT AS STRING,
                       'status': STRING},...]
             'summary': {'filesScanned': INT AS STRING,
                         'filesMoved': INT AS STRING,
                         'filesFailed': INT AS STRING,
                         'filesSkipped': INT AS STRING,
                         'totalSizeMoved': INT AS STRING,
                         'status': STRING}}
    """
    if mode == 'rebalance':
        tree = xmltree.find('volRebalance')
    elif mode == 'remove-brick':
        tree = xmltree.find('volRemoveBrick')
    else:
        return

    status = {
        'summary': {
            'filesScanned': tree.find('aggregate/lookups').text,
            'filesMoved': tree.find('aggregate/files').text,
            'filesFailed': tree.find('aggregate/failures').text,
            'filesSkipped': tree.find('aggregate/failures').text,
            'totalSizeMoved': tree.find('aggregate/size').text,
            'status': tree.find('aggregate/statusStr').text.upper()},
        'hosts': []}

    for el in tree.findall('node'):
        status['hosts'].append({'name': el.find('nodeName').text,
                                'filesScanned': el.find('lookups').text,
                                'filesMoved': el.find('files').text,
                                'filesFailed': el.find('failures').text,
                                'filesSkipped': el.find('failures').text,
                                'totalSizeMoved': el.find('size').text,
                                'status': el.find('statusStr').text.upper()})

    return status


@makePublic
def volumeRebalanceStatus(volumeName):
    command = _getGlusterVolCmd() + ["rebalance", volumeName, "status"]
    try:
        xmltree = _execGlusterXml(command)
    except ge.GlusterCmdFailedException, e:
        raise ge.GlusterVolumeRebalanceStatusFailedException(rc=e.rc,
                                                             err=e.err)
    try:
        return _parseVolumeRebalanceRemoveBrickStatus(xmltree, 'rebalance')
    except _etreeExceptions:
        raise ge.GlusterXmlErrorException(err=[etree.tostring(xmltree)])


@makePublic
def volumeReplaceBrickStart(volumeName, existingBrick, newBrick):
    command = _getGlusterVolCmd() + ["replace-brick", volumeName,
                                     existingBrick, newBrick, "start"]
    try:
        xmltree = _execGlusterXml(command)
    except ge.GlusterCmdFailedException, e:
        raise ge.GlusterVolumeReplaceBrickStartFailedException(rc=e.rc,
                                                               err=e.err)
    try:
        return {'taskId': xmltree.find('volReplaceBrick/task-id').text}
    except _etreeExceptions:
        raise ge.GlusterXmlErrorException(err=[etree.tostring(xmltree)])


@makePublic
def volumeReplaceBrickAbort(volumeName, existingBrick, newBrick):
    command = _getGlusterVolCmd() + ["replace-brick", volumeName,
                                     existingBrick, newBrick, "abort"]
    try:
        _execGlusterXml(command)
        return True
    except ge.GlusterCmdFailedException, e:
        raise ge.GlusterVolumeReplaceBrickAbortFailedException(rc=e.rc,
                                                               err=e.err)


@makePublic
def volumeReplaceBrickPause(volumeName, existingBrick, newBrick):
    command = _getGlusterVolCmd() + ["replace-brick", volumeName,
                                     existingBrick, newBrick, "pause"]
    try:
        _execGlusterXml(command)
        return True
    except ge.GlusterCmdFailedException, e:
        raise ge.GlusterVolumeReplaceBrickPauseFailedException(rc=e.rc,
                                                               err=e.err)


@makePublic
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


@makePublic
def volumeReplaceBrickCommit(volumeName, existingBrick, newBrick,
                             force=False):
    command = _getGlusterVolCmd() + ["replace-brick", volumeName,
                                     existingBrick, newBrick, "commit"]
    if force:
        command.append('force')
    try:
        _execGlusterXml(command)
        return True
    except ge.GlusterCmdFailedException, e:
        raise ge.GlusterVolumeReplaceBrickCommitFailedException(rc=e.rc,
                                                                err=e.err)


@makePublic
def volumeRemoveBrickStart(volumeName, brickList, replicaCount=0):
    command = _getGlusterVolCmd() + ["remove-brick", volumeName]
    if replicaCount:
        command += ["replica", "%s" % replicaCount]
    command += brickList + ["start"]
    try:
        xmltree = _execGlusterXml(command)
    except ge.GlusterCmdFailedException, e:
        raise ge.GlusterVolumeRemoveBrickStartFailedException(rc=e.rc,
                                                              err=e.err)
    try:
        return {'taskId': xmltree.find('volRemoveBrick/task-id').text}
    except _etreeExceptions:
        raise ge.GlusterXmlErrorException(err=[etree.tostring(xmltree)])


@makePublic
def volumeRemoveBrickStop(volumeName, brickList, replicaCount=0):
    command = _getGlusterVolCmd() + ["remove-brick", volumeName]
    if replicaCount:
        command += ["replica", "%s" % replicaCount]
    command += brickList + ["stop"]
    try:
        xmltree = _execGlusterXml(command)
    except ge.GlusterCmdFailedException, e:
        raise ge.GlusterVolumeRemoveBrickStopFailedException(rc=e.rc,
                                                             err=e.err)

    try:
        return _parseVolumeRebalanceRemoveBrickStatus(xmltree, 'remove-brick')
    except _etreeExceptions:
        raise ge.GlusterXmlErrorException(err=[etree.tostring(xmltree)])


@makePublic
def volumeRemoveBrickStatus(volumeName, brickList, replicaCount=0):
    command = _getGlusterVolCmd() + ["remove-brick", volumeName]
    if replicaCount:
        command += ["replica", "%s" % replicaCount]
    command += brickList + ["status"]
    try:
        xmltree = _execGlusterXml(command)
    except ge.GlusterCmdFailedException, e:
        raise ge.GlusterVolumeRemoveBrickStatusFailedException(rc=e.rc,
                                                               err=e.err)
    try:
        return _parseVolumeRebalanceRemoveBrickStatus(xmltree, 'remove-brick')
    except _etreeExceptions:
        raise ge.GlusterXmlErrorException(err=[etree.tostring(xmltree)])


@makePublic
def volumeRemoveBrickCommit(volumeName, brickList, replicaCount=0):
    command = _getGlusterVolCmd() + ["remove-brick", volumeName]
    if replicaCount:
        command += ["replica", "%s" % replicaCount]
    command += brickList + ["commit"]
    try:
        _execGlusterXml(command)
        return True
    except ge.GlusterCmdFailedException, e:
        raise ge.GlusterVolumeRemoveBrickCommitFailedException(rc=e.rc,
                                                               err=e.err)


@makePublic
def volumeRemoveBrickForce(volumeName, brickList, replicaCount=0):
    command = _getGlusterVolCmd() + ["remove-brick", volumeName]
    if replicaCount:
        command += ["replica", "%s" % replicaCount]
    command += brickList + ["force"]
    try:
        _execGlusterXml(command)
        return True
    except ge.GlusterCmdFailedException, e:
        raise ge.GlusterVolumeRemoveBrickForceFailedException(rc=e.rc,
                                                              err=e.err)


@makePublic
def peerProbe(hostName):
    command = _getGlusterPeerCmd() + ["probe", hostName]
    try:
        _execGlusterXml(command)
        return True
    except ge.GlusterCmdFailedException as e:
        raise ge.GlusterHostAddFailedException(rc=e.rc, err=e.err)


@makePublic
def peerDetach(hostName, force=False):
    command = _getGlusterPeerCmd() + ["detach", hostName]
    if force:
        command.append('force')
    try:
        _execGlusterXml(command)
        return True
    except ge.GlusterCmdFailedException as e:
        if e.rc == 2:
            raise ge.GlusterHostNotFoundException(rc=e.rc, err=e.err)
        else:
            raise ge.GlusterHostRemoveFailedException(rc=e.rc, err=e.err)


def _parsePeerStatus(tree, gHostName, gUuid, gStatus):
    hostList = [{'hostname': gHostName,
                 'uuid': gUuid,
                 'status': gStatus}]

    for el in tree.findall('peerStatus/peer'):
        if el.find('state').text != '3':
            status = HostStatus.UNKNOWN
        elif el.find('connected').text == '1':
            status = HostStatus.CONNECTED
        else:
            status = HostStatus.DISCONNECTED
        hostList.append({'hostname': el.find('hostname').text,
                         'uuid': el.find('uuid').text,
                         'status': status})

    return hostList


@makePublic
def peerStatus():
    """
    Returns:
        [{'hostname': HOSTNAME, 'uuid': UUID, 'status': STATE}, ...]
    """
    command = _getGlusterPeerCmd() + ["status"]
    try:
        xmltree = _execGlusterXml(command)
    except ge.GlusterCmdFailedException as e:
        raise ge.GlusterHostsListFailedException(rc=e.rc, err=e.err)
    try:
        return _parsePeerStatus(xmltree,
                                _getLocalIpAddress() or _getGlusterHostName(),
                                hostUUIDGet(), HostStatus.CONNECTED)
    except _etreeExceptions:
        raise ge.GlusterXmlErrorException(err=[etree.tostring(xmltree)])


@makePublic
def volumeProfileStart(volumeName):
    command = _getGlusterVolCmd() + ["profile", volumeName, "start"]
    try:
        _execGlusterXml(command)
    except ge.GlusterCmdFailedException as e:
        raise ge.GlusterVolumeProfileStartFailedException(rc=e.rc, err=e.err)
    return True


@makePublic
def volumeProfileStop(volumeName):
    command = _getGlusterVolCmd() + ["profile", volumeName, "stop"]
    try:
        _execGlusterXml(command)
    except ge.GlusterCmdFailedException as e:
        raise ge.GlusterVolumeProfileStopFailedException(rc=e.rc, err=e.err)
    return True


@makePublic
def volumeProfileInfo(volumeName, nfs=False):
    """
    Returns:
    When nfs=True:
    {'volumeName': VOLUME-NAME,
     'nfsServers': [
         {'nfs': SERVER-NAME,
          'cumulativeStats': {'blockStats': [{'size': int,
                                              'read': int,
                                              'write': int}, ...],
                              'fopStats': [{'name': FOP-NAME,
                                            'hits': int,
                                            'latencyAvg': float,
                                            'latencyMin': float,
                                            'latencyMax': float}, ...],
                              'duration': int,
                              'totalRead': int,
                              'totalWrite': int},
          'intervalStats': {'blockStats': [{'size': int,
                                            'read': int,
                                            'write': int}, ...],
                            'fopStats': [{'name': FOP-NAME,
                                          'hits': int,
                                          'latencyAvg': float,
                                          'latencyMin': float,
                                          'latencyMax': float}, ...],
                            'duration': int,
                            'totalRead': int,
                            'totalWrite': int}}, ...]}

    When nfs=False:
    {'volumeName': VOLUME-NAME,
     'bricks': [
         {'brick': BRICK-NAME,
          'cumulativeStats': {'blockStats': [{'size': int,
                                              'read': int,
                                              'write': int}, ...],
                              'fopStats': [{'name': FOP-NAME,
                                            'hits': int,
                                            'latencyAvg': float,
                                            'latencyMin': float,
                                            'latencyMax': float}, ...],
                              'duration': int,
                              'totalRead': int,
                              'totalWrite': int},
          'intervalStats': {'blockStats': [{'size': int,
                                            'read': int,
                                            'write': int}, ...],
                            'fopStats': [{'name': FOP-NAME,
                                          'hits': int,
                                          'latencyAvg': float,
                                          'latencyMin': float,
                                          'latencyMax': float}, ...],
                            'duration': int,
                            'totalRead': int,
                            'totalWrite': int}}, ...]}
    """
    command = _getGlusterVolCmd() + ["profile", volumeName, "info"]
    if nfs:
        command += ["nfs"]
    try:
        xmltree = _execGlusterXml(command)
    except ge.GlusterCmdFailedException as e:
        raise ge.GlusterVolumeProfileInfoFailedException(rc=e.rc, err=e.err)
    try:
        return _parseVolumeProfileInfo(xmltree, nfs)
    except _etreeExceptions:
        raise ge.GlusterXmlErrorException(err=[etree.tostring(xmltree)])


def _parseVolumeTasks(tree):
    """
    returns {TaskId: {'volumeName': VolumeName,
                      'taskType': TaskType,
                      'status': STATUS,
                      'bricks': BrickList}, ...}
    """
    tasks = {}
    for el in tree.findall('volStatus/volumes/volume'):
        volumeName = el.find('volName').text
        for c in el.findall('tasks/task'):
            taskType = c.find('type').text
            taskType = taskType.upper().replace('-', '_').replace(' ', '_')
            taskId = c.find('id').text
            bricks = []
            if taskType == TaskType.REPLACE_BRICK:
                bricks.append(c.find('params/srcBrick').text)
                bricks.append(c.find('params/dstBrick').text)
            elif taskType == TaskType.REMOVE_BRICK:
                for b in c.findall('params/brick'):
                    bricks.append(b.text)
            elif taskType == TaskType.REBALANCE:
                pass

            statusStr = c.find('statusStr').text.upper() \
                                                .replace('-', '_') \
                                                .replace(' ', '_')

            tasks[taskId] = {'volumeName': volumeName,
                             'taskType': taskType,
                             'status': statusStr,
                             'bricks': bricks}
    return tasks


@makePublic
def volumeTasks(volumeName="all"):
    command = _getGlusterVolCmd() + ["status", volumeName, "tasks"]
    try:
        xmltree = _execGlusterXml(command)
    except ge.GlusterCmdFailedException, e:
        raise ge.GlusterVolumeTasksFailedException(rc=e.rc, err=e.err)
    try:
        return _parseVolumeTasks(xmltree)
    except _etreeExceptions:
        raise ge.GlusterXmlErrorException(err=[etree.tostring(xmltree)])
