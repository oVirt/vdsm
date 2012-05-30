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
        if args:
            status = self.s.glusterVolumesList(args[0])
        else:
            status = self.s.glusterVolumesList()
        pp.pprint(status)
        return status['status']['code'], status['status']['message']

    def do_glusterVolumeStart(self, args):
        status = self.s.glusterVolumeStart(args[0])
        return status['status']['code'], status['status']['message']

    def do_glusterVolumeStop(self, args):
        status = self.s.glusterVolumeStop(args[0])
        return status['status']['code'], status['status']['message']

    def do_glusterVolumeBrickAdd(self, args):
        params = self._eqSplit(args[1:])
        try:
            brickList = params['bricks'].split(',')
        except:
            raise ValueError
        replicaCount = params.get('replica', '')
        stripeCount = params.get('stripe', '')
        status = self.s.glusterVolumeBrickAdd(args[0], brickList,
                                              replicaCount, stripeCount)
        return status['status']['code'], status['status']['message']

    def do_glusterVolumeSet(self, args):
        status = self.s.glusterVolumeSet(args[0], args[1], args[2])
        return status['status']['code'], status['status']['message']

    def do_glusterVolumeSetOptionsList(self, args):
        status = self.s.glusterVolumeSetOptionsList()
        pp.pprint(status)
        return status['status']['code'], status['status']['message']

    def do_glusterVolumeReset(self, args):
        status = self.s.glusterVolumeReset(args[0])
        return status['status']['code'], status['status']['message']

    def do_glusterHostAdd(self, args):
        status = self.s.glusterHostAdd(args[0])
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
        status = self.s.glusterVolumeDelete(args[0])
        return status['status']['code'], status['status']['message']

    def do_glusterHostRemove(self, args):
        status = self.s.glusterHostRemove(args[0])
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
        status = self.s.glusterVolumeRemoveBrickForce(args[0], args[1:])
        return status['status']['code'], status['status']['message']

    def do_glusterHostsList(self, args):
        status = self.s.glusterHostsList()
        pp.pprint(status)
        return status['status']['code'], status['status']['message']


def getGlusterCmdDict(serv):
    return {
        'glusterVolumeCreate':
            (serv.do_glusterVolumeCreate,
             ('volumeName=<volume_name> [replica=<count>] [stripe=<count>] '
              '[transport=<ethernet|infiniband>] bricks=brick[,brick] '
              '... \n\t<volume_name> is name of new volume',
              'create gluster volume'
              )),
        'glusterVolumesList':
            (serv.do_glusterVolumesList,
             ('[volume_name]',
              'if volume_name is given, list only given volume, else all'
              )),
        'glusterVolumeStart':
            (serv.do_glusterVolumeStart,
             ('<volume_name>\n\t<volume_name> is existing volume name',
              'start gluster volume'
              )),
        'glusterVolumeStop':
            (serv.do_glusterVolumeStop,
             ('<volume_name>\n\t<volume_name> is existing volume name',
              'stop gluster volume'
              )),
        'glusterVolumeBrickAdd':
            (serv.do_glusterVolumeBrickAdd,
             ('<volume_name> [replica=<count>] [stripe=<count>] '
              'bricks=brick[,brick] ... \n\t<volume_name> is '
              'existing volume name\n\t<new-brick> is brick which '
              'will be added to the volume',
              'add bricks to gluster volume'
              )),
        'glusterVolumeSet':
            (serv.do_glusterVolumeSet,
             ('<volume_name> <key> <value>\n\t<volume_name> is existing '
              'volume name\n\t<key> is volume option\n\t<value> is '
              'option value',
              'set value to key of gluster volume'
              )),
        'glusterVolumeSetOptionsList':
            (serv.do_glusterVolumeSetOptionsList,
             ('',
              'list gluster volume set options'
              )),
        'glusterVolumeReset':
            (serv.do_glusterVolumeReset,
             ('<volume_name>\n\t<volume_name> is existing volume name',
              'reset gluster volume'
              )),
        'glusterHostAdd':
            (serv.do_glusterHostAdd,
             ('<host>\n\t<host> is hostname or ip address of new server',
              'add new server to gluster storage cluster'
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
             ('<host>\n\t<host> is hostname or ip address of existing server',
              'remove existing server form gluster storage cluster'
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
             ('<volume_name> [replica=<count>] bricks=brick[,brick] ... \n\t'
              '<volume_name> is existing volume name\n\t<brick> is '
              'existing brick',
              'force volume remove bricks'
              )),
        'glusterHostsList':
            (serv.do_glusterHostsList,
             ('',
              'list host info'
              )),
        }
