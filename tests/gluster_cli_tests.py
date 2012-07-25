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

from nose.plugins.skip import SkipTest
from testrunner import VdsmTestCase as TestCaseBase

# If the test is run under the installed vdsm, vdsm-cluster may not be always
# installed, so just ignore the exception.
# In the individual test, setUp() will skip test if  gluster is not imported
try:
    from gluster import cli as gcli
except ImportError:
    pass


class GlusterCliTests(TestCaseBase):
    def setUp(self):
        if not "gcli" in globals().keys():
            raise SkipTest("vdsm-gluster not found")

    def _parseVolumeInfo_empty_test(self):
        out = ['No volumes present']
        self.assertFalse(gcli._parseVolumeInfo(out))

    def _parseVolumeInfo_test(self):
        out = [' ',
               'Volume Name: music',
               'Type: Distribute',
               'Volume ID: 1e7d2638-fb77-4325-94fd-3242650a013c',
               'Status: Stopped',
               'Number of Bricks: 2',
               'Transport-type: tcp',
               'Bricks:',
               'Brick1: 192.168.122.167:/tmp/music-b1',
               'Brick2: 192.168.122.167:/tmp/music-b2',
               'Options Reconfigured:',
               'auth.allow: *']
        volumeInfo = gcli._parseVolumeInfo(out)
        for volumeName  in volumeInfo:
            if volumeName == 'music':
                self.assertEquals(volumeInfo['music']['volumeName'],
                                  volumeName)
                self.assertEquals(volumeInfo['music']['uuid'],
                                  '1e7d2638-fb77-4325-94fd-3242650a013c')
                self.assertEquals(volumeInfo['music']['volumeType'],
                                  'DISTRIBUTE')
                self.assertEquals(volumeInfo['music']['volumeStatus'],
                                  'STOPPED')
                self.assertEquals(volumeInfo['music']['transportType'],
                                  ['TCP'])
                self.assertEquals(volumeInfo['music']['bricks'],
                                  ['192.168.122.167:/tmp/music-b1',
                                   '192.168.122.167:/tmp/music-b2'])
                self.assertEquals(volumeInfo['music']['brickCount'], '2')
                self.assertEquals(volumeInfo['music']['options'],
                                  {'auth.allow': '*'})

    def test_parseVolumeInfo(self):
        self._parseVolumeInfo_empty_test()
        self._parseVolumeInfo_test()

    def _parsePeerStatus_empty_test(self):
        out = ['No peers present']
        hostList = \
            gcli._parsePeerStatus(out, 'fedora-16-test',
                                  '711d2887-3222-46d8-801a-7e3f646bdd4d',
                                  gcli.HostStatus.CONNECTED)
        self.assertEquals(hostList,
                          [{'hostname': 'fedora-16-test',
                            'uuid': '711d2887-3222-46d8-801a-7e3f646bdd4d',
                            'status': gcli.HostStatus.CONNECTED}])

    def _parsePeerStatus_test(self):
        out = ['Number of Peers: 1',
               '',
               'Hostname: 192.168.2.21',
               'Uuid: 610f466c-781a-4e04-8f67-8eba9a201867',
               'State: Peer in Cluster (Connected)',
               '',
               'Hostname: FC16-1',
               'Uuid: 12345678-781a-aaaa-bbbb-8eba9a201867',
               'State: Peer in Cluster (Disconnected)']
        hostList = \
            gcli._parsePeerStatus(out, 'fedora-16-test',
                                  '711d2887-3222-46d8-801a-7e3f646bdd4d',
                                  gcli.HostStatus.CONNECTED)
        self.assertEquals(hostList,
                          [{'hostname': 'fedora-16-test',
                            'uuid': '711d2887-3222-46d8-801a-7e3f646bdd4d',
                            'status': gcli.HostStatus.CONNECTED},
                           {'hostname': '192.168.2.21',
                            'uuid': '610f466c-781a-4e04-8f67-8eba9a201867',
                            'status': gcli.HostStatus.CONNECTED},
                           {'hostname': 'FC16-1',
                            'uuid': '12345678-781a-aaaa-bbbb-8eba9a201867',
                            'status': gcli.HostStatus.DISCONNECTED}])

    def test_parsePeerStatus(self):
        self._parsePeerStatus_empty_test()
        self._parsePeerStatus_test()
