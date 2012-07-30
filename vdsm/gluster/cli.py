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
from functools import wraps

from vdsm import utils
from vdsm import netinfo
import exception as ge
from hostname import getHostNameFqdn, HostNameException

_glusterCommandPath = utils.CommandPath("gluster",
                                        "/usr/sbin/gluster",
                                        )


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


class VolumeStatus:
    ONLINE = 'ONLINE'
    OFFLINE = 'OFFLINE'


class TransportType:
    TCP = 'TCP'
    RDMA = 'RDMA'


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
    except (etree.ParseError, AttributeError, ValueError):
        raise ge.GlusterXmlErrorException(err=out)
    if rv == 0:
        return tree
    else:
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


def _getGlusterUuid():
    try:
        with open('/var/lib/glusterd/glusterd.info') as f:
            return dict(map(lambda x: x.strip().split('=', 1),
                            f)).get('UUID', '')
    except IOError:
        return ''


def _parseVolumeStatus(tree):
    status = {'name': tree.find('volStatus/volName').text,
              'bricks': [],
              'nfs': [],
              'shd': []}
    hostname = _getLocalIpAddress() or _getGlusterHostName()
    for el in tree.findall('volStatus/node'):
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
    status = {'name': tree.find('volStatus/volName').text,
              'bricks': []}
    for el in tree.findall('volStatus/node'):
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
    status = {'name': tree.find('volStatus/volName').text,
              'bricks': []}
    for el in tree.findall('volStatus/node'):
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
    status = {'name': tree.find('volStatus/volName').text,
              'bricks': []}
    for el in tree.findall('volStatus/node'):
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


@exportToSuperVdsm
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
    except ge.GlusterCmdFailedException, e:
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
    except (etree.ParseError, AttributeError, ValueError):
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
        for b in el.findall('bricks/brick'):
            value['bricks'].append(b.text)
        for o in el.findall('options/option'):
            value['options'][o.find('name').text] = o.find('value').text
        volumes[value['volumeName']] = value
    return volumes


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
    try:
        xmltree = _execGlusterXml(command)
    except ge.GlusterCmdFailedException, e:
        raise ge.GlusterVolumesListFailedException(rc=e.rc, err=e.err)
    try:
        return _parseVolumeInfo(xmltree)
    except (etree.ParseError, AttributeError, ValueError):
        raise ge.GlusterXmlErrorException(err=[etree.tostring(xmltree)])


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
    try:
        xmltree = _execGlusterXml(command)
    except ge.GlusterCmdFailedException, e:
        raise ge.GlusterVolumeCreateFailedException(rc=e.rc, err=e.err)
    try:
        return {'uuid': xmltree.find('volCreate/volume/id').text}
    except (etree.ParseError, AttributeError, ValueError):
        raise ge.GlusterXmlErrorException(err=[etree.tostring(xmltree)])


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
    try:
        _execGlusterXml(command)
        return True
    except ge.GlusterCmdFailedException, e:
        raise ge.GlusterVolumeStopFailedException(rc=e.rc, err=e.err)


@exportToSuperVdsm
def volumeDelete(volumeName):
    command = _getGlusterVolCmd() + ["delete", volumeName]
    try:
        _execGlusterXml(command)
        return True
    except ge.GlusterCmdFailedException, e:
        raise ge.GlusterVolumeDeleteFailedException(rc=e.rc, err=e.err)


@exportToSuperVdsm
def volumeSet(volumeName, option, value):
    command = _getGlusterVolCmd() + ["set", volumeName, option, value]
    try:
        _execGlusterXml(command)
        return True
    except ge.GlusterCmdFailedException, e:
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
    try:
        _execGlusterXml(command)
        return True
    except ge.GlusterCmdFailedException, e:
        raise ge.GlusterVolumeResetFailedException(rc=e.rc, err=e.err)


@exportToSuperVdsm
def volumeAddBrick(volumeName, brickList,
                   replicaCount=0, stripeCount=0):
    command = _getGlusterVolCmd() + ["add-brick", volumeName]
    if stripeCount:
        command += ["stripe", "%s" % stripeCount]
    if replicaCount:
        command += ["replica", "%s" % replicaCount]
    command += brickList
    try:
        _execGlusterXml(command)
        return True
    except ge.GlusterCmdFailedException, e:
        raise ge.GlusterVolumeBrickAddFailedException(rc=e.rc, err=e.err)


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
    command = _getGlusterPeerCmd() + ["probe", hostName]
    try:
        _execGlusterXml(command)
        return True
    except ge.GlusterCmdFailedException, e:
        raise ge.GlusterHostAddFailedException(rc=e.rc, err=e.err)


@exportToSuperVdsm
def peerDetach(hostName, force=False):
    command = _getGlusterPeerCmd() + ["detach", hostName]
    if force:
        command.append('force')
    try:
        _execGlusterXml(command)
        return True
    except ge.GlusterCmdFailedException, e:
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


@exportToSuperVdsm
def peerStatus():
    """
    Returns:
        [{'hostname': HOSTNAME, 'uuid': UUID, 'status': STATE}, ...]
    """
    command = _getGlusterPeerCmd() + ["status"]
    try:
        xmltree = _execGlusterXml(command)
    except ge.GlusterCmdFailedException, e:
        raise ge.GlusterHostsListFailedException(rc=e.rc, err=e.err)
    try:
        return _parsePeerStatus(xmltree,
                                _getLocalIpAddress() or _getGlusterHostName(),
                                _getGlusterUuid(), HostStatus.CONNECTED)
    except (etree.ParseError, AttributeError, ValueError):
        raise ge.GlusterXmlErrorException(err=[etree.tostring(xmltree)])


@exportToSuperVdsm
def volumeProfileStart(volumeName):
    command = _getGlusterVolCmd() + ["profile", volumeName, "start"]
    try:
        _execGlusterXml(command)
    except ge.GlusterCmdFailedException, e:
        raise ge.GlusterVolumeProfileStartFailedException(rc=e.rc, err=e.err)
    return True


@exportToSuperVdsm
def volumeProfileStop(volumeName):
    command = _getGlusterVolCmd() + ["profile", volumeName, "stop"]
    try:
        _execGlusterXml(command)
    except ge.GlusterCmdFailedException, e:
        raise ge.GlusterVolumeProfileStopFailedException(rc=e.rc, err=e.err)
    return True
