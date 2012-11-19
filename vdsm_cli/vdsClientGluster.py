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

import pprint as pp

from vdsClient import service


class GlusterService(service):
    def __init__(self):
        service.__init__(self)

    def do_glusterVolumeCreate(self, args):
        params = self._eqSplit(args)
        try:
            brickList = params['bricks'].split(',')
        except:
            raise ValueError
        volumeName = params.get('volumeName', '')
        replicaCount = params.get('replica', '')
        stripeCount = params.get('stripe', '')
        transport = params.get('transport', '')
        transportList = transport.strip().split(',') if transport else []

        status = self.s.glusterVolumeCreate(volumeName, brickList,
                                            replicaCount, stripeCount,
                                            transportList)
        return status['status']['code'], status['status']['message']

    def do_glusterVolumesList(self, args):
        params = self._eqSplit(args)
        volumeName = params.get('volumeName', '')

        status = self.s.glusterVolumesList(volumeName)
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
        try:
            brickList = params['bricks'].split(',')
        except:
            raise ValueError
        volumeName = params.get('volumeName', '')
        replicaCount = params.get('replica', '')
        stripeCount = params.get('stripe', '')

        status = self.s.glusterVolumeBrickAdd(volumeName, brickList,
                                              replicaCount, stripeCount)
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
        params = self._eqSplit(args[1:])
        rebalanceType = params.get('type', 'fix-layout')
        force = params.get('force', False)
        status = self.s.glusterVolumeRebalanceStart(args[0],
                                                    rebalanceType, force)
        pp.pprint(status)
        return status['status']['code'], status['status']['message']

    def do_glusterVolumeRebalanceStop(self, args):
        params = self._eqSplit(args[1:])
        force = params.get('force', False)
        status = self.s.glusterVolumeRebalanceStop(args[0], force)
        pp.pprint(status)
        return status['status']['code'], status['status']['message']

    def do_glusterVolumeRebalanceStatus(self, args):
        status = self.s.glusterVolumeRebalanceStatus(args[0])
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

    def do_glusterVolumeReplaceBrickStart(self, args):
        status = self.s.glusterVolumeReplaceBrickStart(args[0], args[1],
                                                       args[2])
        return status['status']['code'], status['status']['message']

    def do_glusterVolumeReplaceBrickAbort(self, args):
        status = self.s.glusterVolumeReplaceBrickAbort(args[0], args[1],
                                                       args[2])
        return status['status']['code'], status['status']['message']

    def do_glusterVolumeReplaceBrickPause(self, args):
        status = self.s.glusterVolumeReplaceBrickPause(args[0], args[1],
                                                       args[2])
        return status['status']['code'], status['status']['message']

    def do_glusterVolumeReplaceBrickStatus(self, args):
        status = self.s.glusterVolumeReplaceBrickStatus(args[0], args[1],
                                                        args[2])
        return status['status']['code'], status['status']['message']

    def do_glusterVolumeReplaceBrickCommit(self, args):
        status = self.s.glusterVolumeReplaceBrickCommit(args[0], args[1],
                                                        args[2])
        return status['status']['code'], status['status']['message']

    def do_glusterVolumeRemoveBrickStart(self, args):
        params = self._eqSplit(args[1:])
        try:
            brickList = params['bricks'].split(',')
        except:
            raise ValueError
        replicaCount = params.get('replica', '')
        status = self.s.glusterVolumeRemoveBrickStart(args[0], brickList,
                                                      replicaCount)
        pp.pprint(status)
        return status['status']['code'], status['status']['message']

    def do_glusterVolumeRemoveBrickStop(self, args):
        params = self._eqSplit(args[1:])
        try:
            brickList = params['bricks'].split(',')
        except:
            raise ValueError
        replicaCount = params.get('replica', '')
        status = self.s.glusterVolumeRemoveBrickStop(args[0], brickList,
                                                     replicaCount)
        pp.pprint(status)
        return status['status']['code'], status['status']['message']

    def do_glusterVolumeRemoveBrickStatus(self, args):
        params = self._eqSplit(args[1:])
        try:
            brickList = params['bricks'].split(',')
        except:
            raise ValueError
        replicaCount = params.get('replica', '')
        status = self.s.glusterVolumeRemoveBrickStatus(args[0], brickList,
                                                       replicaCount)
        pp.pprint(status)
        return status['status']['code'], status['status']['message']

    def do_glusterVolumeRemoveBrickCommit(self, args):
        params = self._eqSplit(args[1:])
        try:
            brickList = params['bricks'].split(',')
        except:
            raise ValueError
        replicaCount = params.get('replica', '')
        status = self.s.glusterVolumeRemoveBrickCommit(args[0], brickList,
                                                       replicaCount)
        pp.pprint(status)
        return status['status']['code'], status['status']['message']

    def do_glusterVolumeRemoveBrickForce(self, args):
        params = self._eqSplit(args)
        volumeName = params.get('volumeName', '')
        try:
            brickList = params['bricks'].split(',')
        except:
            raise ValueError
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
        status = self.s.glusterVolumeProfileStart(args[0])
        return status['status']['code'], status['status']['message']

    def do_glusterVolumeProfileStop(self, args):
        status = self.s.glusterVolumeProfileStop(args[0])
        return status['status']['code'], status['status']['message']


def getGlusterCmdDict(serv):
    return {
        'glusterVolumeCreate':
            (serv.do_glusterVolumeCreate,
             ('volumeName=<volume_name> bricks=<brick[,brick, ...]> '
              '[replica=<count>] [stripe=<count>] [transport={tcp|rdma}]\n\t'
              '<volume_name> is name of new volume',
              '<brick[,brick, ...]> is brick(s) which will be used to '
              'create volume',
              'create gluster volume'
              )),
        'glusterVolumesList':
            (serv.do_glusterVolumesList,
             ('[volumeName=<volume_name>]\n\t'
              '<volume_name> is existing volume name',
              'list all or given gluster volume details'
              )),
        'glusterVolumeStart':
            (serv.do_glusterVolumeStart,
             ('volumeName=<volume_name> [force={yes|no}]\n\t'
              '<volume_name> is existing volume name',
              'start gluster volume'
              )),
        'glusterVolumeStop':
            (serv.do_glusterVolumeStop,
             ('volumeName=<volume_name> [force={yes|no}]\n\t'
              '<volume_name> is existing volume name',
              'stop gluster volume'
              )),
        'glusterVolumeBrickAdd':
            (serv.do_glusterVolumeBrickAdd,
             ('volumeName=<volume_name> bricks=<brick[,brick, ...]> '
              '[replica=<count>] [stripe=<count>]\n\t'
              '<volume_name> is existing volume name\n\t'
              '<brick[,brick, ...]> is new brick(s) which will be added to '
              'the volume',
              'add bricks to gluster volume'
              )),
        'glusterVolumeSet':
            (serv.do_glusterVolumeSet,
             ('volumeName=<volume_name> option=<option> value=<value>\n\t'
              '<volume_name> is existing volume name\n\t'
              '<option> is volume option\n\t'
              '<value> is value to volume option',
              'set gluster volume option'
              )),
        'glusterVolumeSetOptionsList':
            (serv.do_glusterVolumeSetOptionsList,
             ('',
              'list gluster volume set options'
              )),
        'glusterVolumeReset':
            (serv.do_glusterVolumeReset,
             ('volumeName=<volume_name> [option=<option>] [force={yes|no}]\n\t'
              '<volume_name> is existing volume name',
              'reset gluster volume or volume option'
              )),
        'glusterHostAdd':
            (serv.do_glusterHostAdd,
             ('hostName=<host>\n\t'
              '<host> is hostname or ip address of new server',
              'add new server to gluster cluster'
              )),
        'glusterVolumeRebalanceStart':
            (serv.do_glusterVolumeRebalanceStart,
             ('<volume_name>\n\t<volume_name> is existing volume name',
              'start volume rebalance'
              )),
        'glusterVolumeRebalanceStop':
            (serv.do_glusterVolumeRebalanceStop,
             ('<volume_name>\n\t<volume_name> is existing volume name',
              'stop volume rebalance'
              )),
        'glusterVolumeRebalanceStatus':
            (serv.do_glusterVolumeRebalanceStatus,
             ('<volume_name>\n\t<volume_name> is existing volume name',
              'get volume rebalance status'
              )),
        'glusterVolumeDelete':
            (serv.do_glusterVolumeDelete,
             ('volumeName=<volume_name> \n\t<volume_name> is existing '
              'volume name',
              'delete gluster volume'
              )),
        'glusterHostRemove':
            (serv.do_glusterHostRemove,
             ('hostName=<host> [force={yes|no}]\n\t'
              '<host> is hostname or ip address of a server in '
              'gluster cluster',
              'remove server from gluster cluster'
              )),
        'glusterVolumeReplaceBrickStart':
            (serv.do_glusterVolumeReplaceBrickStart,
             ('<volume_name> <existing_brick> <new_brick> \n\t<volume_name> '
              'is existing volume name\n\t<brick> is existing brick\n\t'
              '<new_brick> is new brick',
              'start volume replace brick'
              )),
        'glusterVolumeReplaceBrickAbort':
            (serv.do_glusterVolumeReplaceBrickAbort,
             ('<volume_name> <existing_brick> <new_brick> \n\t<volume_name> '
              'is existing volume name\n\t<brick> is existing brick\n\t'
              '<new_brick> is new brick',
              'abort volume replace brick'
              )),
        'glusterVolumeReplaceBrickPause':
            (serv.do_glusterVolumeReplaceBrickPause,
             ('<volume_name> <existing_brick> <new_brick> \n\t<volume_name> '
              'is existing volume name\n\t<brick> is existing brick\n\t'
              '<new_brick> is new brick',
              'pause volume replace brick'
              )),
        'glusterVolumeReplaceBrickStatus':
            (serv.do_glusterVolumeReplaceBrickStatus,
             ('<volume_name> <existing_brick> <new_brick> \n\t<volume_name> '
              'is existing volume name\n\t<brick> is existing brick\n\t'
              '<new_brick> is new brick',
              'get volume replace brick status'
              )),
        'glusterVolumeReplaceBrickCommit':
            (serv.do_glusterVolumeReplaceBrickCommit,
             ('<volume_name> <existing_brick> <new_brick> \n\t<volume_name> '
              'is existing volume name\n\t<brick> is existing brick\n\t'
              '<new_brick> is new brick',
              'commit volume replace brick'
              )),
        'glusterVolumeRemoveBrickStart':
            (serv.do_glusterVolumeRemoveBrickStart,
             ('<volume_name> [replica=<count>] bricks=brick[,brick] ... \n\t'
              '<volume_name> is existing volume name\n\t<brick> is '
              'existing brick',
              'start volume remove bricks'
              )),
        'glusterVolumeRemoveBrickStop':
            (serv.do_glusterVolumeRemoveBrickStop,
             ('<volume_name> [replica=<count>] bricks=brick[,brick] ... \n\t'
              '<volume_name> is existing volume name\n\t<brick> is '
              'existing brick',
              'stop volume remove bricks'
              )),
        'glusterVolumeRemoveBrickStatus':
            (serv.do_glusterVolumeRemoveBrickStatus,
             ('<volume_name> [replica=<count>] bricks=brick[,brick] ... \n\t'
              '<volume_name> is existing volume name\n\t<brick> is '
              'existing brick',
              'get volume remove bricks status'
              )),
        'glusterVolumeRemoveBrickCommit':
            (serv.do_glusterVolumeRemoveBrickCommit,
             ('<volume_name> [replica=<count>] bricks=brick[,brick] ... \n\t'
              '<volume_name> is existing volume name\n\t<brick> is '
              'existing brick',
              'commit volume remove bricks'
              )),
        'glusterVolumeRemoveBrickForce':
            (serv.do_glusterVolumeRemoveBrickForce,
             ('volumeName=<volume_name> bricks=<brick[,brick, ...]> '
              '[replica=<count>]\n\t'
              '<volume_name> is existing volume name\n\t'
              '<brick[,brick, ...]> is existing brick(s)',
              'force volume remove bricks'
              )),
        'glusterVolumeStatus':
            (serv.do_glusterVolumeStatus,
             ('volumeName=<volume_name> [brick=<existing_brick>] '
              '[option={detail | clients | mem}]\n\t'
              '<volume_name> is existing volume name\n\t'
              'option=detail gives brick detailed status\n\t'
              'option=clients gives clients status\n\t'
              'option=mem gives memory status\n\t',
              'get volume status of given volume with its all brick or '
              'specified brick'
              )),
        'glusterHostsList':
            (serv.do_glusterHostsList,
             ('',
              'list host info'
              )),
        'glusterVolumeProfileStart':
            (serv.do_glusterVolumeProfileStart,
             ('<volume_name>\n\t<volume_name> is existing volume name',
              'start gluster volume profile'
              )),
        'glusterVolumeProfileStop':
            (serv.do_glusterVolumeProfileStop,
             ('<volume_name>\n\t<volume_name> is existing volume name',
              'stop gluster volume profile'
              )),
        }
