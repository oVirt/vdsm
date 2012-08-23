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

from functools import wraps

from vdsm.define import doneCode
import supervdsm as svdsm

_SUCCESS = {'status': doneCode}


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


class VolumeStatus():
    ONLINE = 'ONLINE'
    OFFLINE = 'OFFLINE'


class GlusterApi(object):
    """
    The gluster interface of vdsm.

    """

    def __init__(self, cif, log):
        self.cif = cif
        self.log = log
        self.svdsmProxy = svdsm.getProxy()

    @exportAsVerb
    def volumesList(self, volumeName=None, options=None):
        """
        Returns:
            {'status' : {'code': CODE, 'message': MESSAGE},
             'volumes': {VOLUMENAME: {'brickCount': BRICKCOUNT,
                                      'bricks': [BRICK1, BRICK2, ...],
                                      'options': {OPTION: VALUE, ...},
                                      'transportType': [TCP, RDMA, ...],
                                      'uuid': UUID,
                                      'volumeName': NAME,
                                      'volumeStatus': STATUS,
                                      'volumeType': TYPE}, ...}}
        """
        volumeInfoDict = self.svdsmProxy.glusterVolumeInfo(volumeName)
        for name, info in volumeInfoDict.iteritems():
            info["volumeType"] = info["volumeType"].replace("-", "_")
            if info["volumeStatus"] == "STARTED":
                info["volumeStatus"] = VolumeStatus.ONLINE
            else:
                info["volumeStatus"] = VolumeStatus.OFFLINE
        return {'volumes': volumeInfoDict}

    @exportAsVerb
    def volumeCreate(self, volumeName, brickList, replicaCount=0,
                     stripeCount=0, transportList=[], options=None):
        self.svdsmProxy.glusterVolumeCreate(volumeName, brickList,
                                            replicaCount, stripeCount,
                                            transportList)
        volumeList = self.svdsmProxy.glusterVolumeInfo()
        return {'uuid': volumeList[volumeName]['uuid']}

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
    def volumeBrickAdd(self, volumeName, brickList,
                       replicaCount=0, stripeCount=0, options=None):
        self.svdsmProxy.glusterVolumeAddBrick(volumeName, brickList,
                                              replicaCount, stripeCount)

    @exportAsVerb
    def volumeRebalanceStart(self, volumeName, rebalanceType="",
                             force=False, options=None):
        self.svdsmProxy.glusterVolumeRebalanceStart(volumeName,
                                                    rebalanceType,
                                                    force)

    @exportAsVerb
    def volumeRebalanceStop(self, volumeName, force=False, options=None):
        self.svdsmProxy.glusterVolumeRebalanceStop(volumeName, force)

    @exportAsVerb
    def volumeRebalanceStatus(self, volumeName, options=None):
        st, msg = self.svdsmProxy.glusterVolumeRebalanceStatus(volumeName)
        return {'rebalance': st, 'message': msg}

    @exportAsVerb
    def volumeReplaceBrickStart(self, volumeName, existingBrick, newBrick,
                                options=None):
        self.svdsmProxy.glusterVolumeReplaceBrickStart(volumeName,
                                                       existingBrick,
                                                       newBrick)

    @exportAsVerb
    def volumeReplaceBrickAbort(self, volumeName, existingBrick, newBrick,
                                options=None):
        self.svdsmProxy.glusterVolumeReplaceBrickAbort(volumeName,
                                                       existingBrick,
                                                       newBrick)

    @exportAsVerb
    def volumeReplaceBrickPause(self, volumeName, existingBrick, newBrick,
                                options=None):
        self.svdsmProxy.glusterVolumeReplaceBrickPause(volumeName,
                                                       existingBrick,
                                                       newBrick)

    @exportAsVerb
    def volumeReplaceBrickStatus(self, volumeName, oldBrick, newBrick,
                                 options=None):
        st, msg = self.svdsmProxy.glusterVolumeReplaceBrickStatus(volumeName,
                                                                  oldBrick,
                                                                  newBrick)
        return {'replaceBrick': st, 'message': msg}

    @exportAsVerb
    def volumeReplaceBrickCommit(self, volumeName, existingBrick, newBrick,
                                 force=False, options=None):
        self.svdsmProxy.glusterVolumeReplaceBrickCommit(volumeName,
                                                        existingBrick,
                                                        newBrick,
                                                        force)

    @exportAsVerb
    def volumeRemoveBrickStart(self, volumeName, brickList,
                               replicaCount=0, options=None):
        self.svdsmProxy.glusterVolumeRemoveBrickStart(volumeName, brickList,
                                                      replicaCount)

    @exportAsVerb
    def volumeRemoveBrickStop(self, volumeName, brickList,
                              replicaCount=0, options=None):
        self.svdsmProxy.glusterVolumeRemoveBrickStop(volumeName, brickList,
                                                     replicaCount)

    @exportAsVerb
    def volumeRemoveBrickStatus(self, volumeName, brickList,
                                replicaCount=0, options=None):
        message = self.svdsmProxy.glusterVolumeRemoveBrickStatus(volumeName,
                                               brickList, replicaCount)
        return {'message': message}

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

    @exportAsVerb
    def volumeStatus(self, volumeName, brick=None, statusOption=None,
                     options=None):
        return {'volumeStatus':
                    self.svdsmProxy.glusterVolumeStatus(volumeName, brick,
                                                        statusOption)}

    @exportAsVerb
    def hostAdd(self, hostName, options=None):
        self.svdsmProxy.glusterPeerProbe(hostName)

    @exportAsVerb
    def hostRemove(self, hostName, force=False, options=None):
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


def getGlusterMethods(gluster):
    l = []
    for name in dir(gluster):
        func = getattr(gluster, name)
        if getattr(func, 'exportAsVerb', False) == True:
            l.append((func, 'gluster%s%s' % (name[0].upper(), name[1:])))
    return tuple(l)
