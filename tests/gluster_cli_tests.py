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
import xml.etree.cElementTree as etree


class GlusterCliTests(TestCaseBase):
    maxDiff = None

    def setUp(self):
        if not "gcli" in globals().keys():
            raise SkipTest("vdsm-gluster not found")

    def _parseVolumeInfo_empty_test(self):
        out = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<cliOutput>
  <opRet>0</opRet>
  <opErrno>0</opErrno>
  <opErrstr/>
  <volInfo/>
</cliOutput>
"""
        tree = etree.fromstring(out)
        self.assertFalse(gcli._parseVolumeInfo(tree))

    def _parseVolumeInfo_test(self):
        out = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<cliOutput>
  <opRet>0</opRet>
  <opErrno>0</opErrno>
  <opErrstr/>
  <volInfo>
    <volumes>
      <volume>
        <name>music</name>
        <id>b3114c71-741b-4c6f-a39e-80384c4ea3cf</id>
        <status>1</status>
        <statusStr>Started</statusStr>
        <brickCount>2</brickCount>
        <distCount>2</distCount>
        <stripeCount>1</stripeCount>
        <replicaCount>2</replicaCount>
        <type>2</type>
        <typeStr>Replicate</typeStr>
        <transport>0</transport>
        <bricks>
          <brick>192.168.122.2:/tmp/music-b1</brick>
          <brick>192.168.122.2:/tmp/music-b2</brick>
        </bricks>
        <optCount>1</optCount>
        <options>
          <option>
            <name>auth.allow</name>
            <value>*</value>
          </option>
        </options>
      </volume>
      <volume>
        <name>test1</name>
        <id>b444ed94-f346-4cda-bd55-0282f21d22db</id>
        <status>2</status>
        <statusStr>Stopped</statusStr>
        <brickCount>1</brickCount>
        <distCount>1</distCount>
        <stripeCount>1</stripeCount>
        <replicaCount>1</replicaCount>
        <type>0</type>
        <typeStr>Distribute</typeStr>
        <transport>1</transport>
        <bricks>
          <brick>192.168.122.2:/tmp/test1-b1</brick>
        </bricks>
        <optCount>0</optCount>
        <options/>
      </volume>
      <count>2</count>
    </volumes>
  </volInfo>
</cliOutput>
"""
        tree = etree.fromstring(out)
        oVolumeInfo = \
            {'music': {'brickCount': '2',
                       'bricks': ['192.168.122.2:/tmp/music-b1',
                                  '192.168.122.2:/tmp/music-b2'],
                       'distCount': '2',
                       'options': {'auth.allow': '*'},
                       'replicaCount': '2',
                       'stripeCount': '1',
                       'transportType': [gcli.TransportType.TCP],
                       'uuid': 'b3114c71-741b-4c6f-a39e-80384c4ea3cf',
                       'volumeName': 'music',
                       'volumeStatus': gcli.VolumeStatus.ONLINE,
                       'volumeType': 'REPLICATE'},
             'test1': {'brickCount': '1',
                       'bricks': ['192.168.122.2:/tmp/test1-b1'],
                       'distCount': '1',
                       'options': {},
                       'replicaCount': '1',
                       'stripeCount': '1',
                       'transportType': [gcli.TransportType.RDMA],
                       'uuid': 'b444ed94-f346-4cda-bd55-0282f21d22db',
                       'volumeName': 'test1',
                       'volumeStatus': gcli.VolumeStatus.OFFLINE,
                       'volumeType': 'DISTRIBUTE'}}
        volumeInfo = gcli._parseVolumeInfo(tree)
        self.assertEquals(volumeInfo, oVolumeInfo)

    def test_parseVolumeInfo(self):
        self._parseVolumeInfo_empty_test()
        self._parseVolumeInfo_test()

    def _parsePeerStatus_empty_test(self):
        out = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<cliOutput>
  <opRet>0</opRet>
  <opErrno>0</opErrno>
  <opErrstr>No peers present</opErrstr>
  <peerStatus/>
</cliOutput>
"""
        tree = etree.fromstring(out)
        hostList = \
            gcli._parsePeerStatus(tree, 'fedora-16-test',
                                  '711d2887-3222-46d8-801a-7e3f646bdd4d',
                                  gcli.HostStatus.CONNECTED)
        self.assertEquals(hostList,
                          [{'hostname': 'fedora-16-test',
                            'uuid': '711d2887-3222-46d8-801a-7e3f646bdd4d',
                            'status': gcli.HostStatus.CONNECTED}])

    def _parsePeerStatus_test(self):
        out = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<cliOutput>
  <opRet>0</opRet>
  <opErrno>0</opErrno>
  <opErrstr/>
  <peerStatus>
    <peer>
      <uuid>610f466c-781a-4e04-8f67-8eba9a201867</uuid>
      <hostname>192.168.2.21</hostname>
      <connected>1</connected>
      <state>3</state>
      <stateStr>Peer in Cluster</stateStr>
    </peer>
    <peer>
      <uuid>12345678-781a-aaaa-bbbb-8eba9a201867</uuid>
      <hostname>FC16-1</hostname>
      <connected>0</connected>
      <state>3</state>
      <stateStr>Peer in Cluster</stateStr>
    </peer>
    <peer>
      <uuid>12345678-cccc-aaaa-bbbb-8eba9a201867</uuid>
      <hostname>FC16-2</hostname>
      <connected>1</connected>
      <state>2</state>
      <stateStr>Peer rejected</stateStr>
    </peer>
  </peerStatus>
</cliOutput>
"""
        tree = etree.fromstring(out)
        hostList = \
            gcli._parsePeerStatus(tree, 'fedora-16-test',
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
                            'status': gcli.HostStatus.DISCONNECTED},
                           {'hostname': 'FC16-2',
                            'uuid': '12345678-cccc-aaaa-bbbb-8eba9a201867',
                            'status': gcli.HostStatus.UNKNOWN}])

    def test_parsePeerStatus(self):
        self._parsePeerStatus_empty_test()
        self._parsePeerStatus_test()

    def _parseVolumeStatus_test(self):
        out = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<cliOutput>
  <opRet>0</opRet>
  <opErrno>0</opErrno>
  <opErrstr/>
  <volStatus>
    <volName>music</volName>
    <nodeCount>4</nodeCount>
    <node>
      <hostname>192.168.122.2</hostname>
      <path>/tmp/music-b1</path>
      <port>49152</port>
      <status>1</status>
      <pid>1313</pid>
    </node>
    <node>
      <hostname>192.168.122.2</hostname>
      <path>/tmp/music-b2</path>
      <port>49153</port>
      <status>1</status>
      <pid>1335</pid>
    </node>
    <node>
      <hostname>NFS Server</hostname>
      <path>192.168.122.2</path>
      <port>38467</port>
      <status>1</status>
      <pid>1357</pid>
    </node>
    <node>
      <hostname>Self-heal Daemon</hostname>
      <path>192.168.122.2</path>
      <port>0</port>
      <status>1</status>
      <pid>1375</pid>
    </node>
  </volStatus>
</cliOutput>
"""
        tree = etree.fromstring(out)
        status = gcli._parseVolumeStatus(tree)
        self.assertEquals(status,
                          {'bricks': [{'brick': '192.168.122.2:/tmp/music-b1',
                                       'pid': '1313',
                                       'port': '49152',
                                       'status': 'ONLINE'},
                                      {'brick': '192.168.122.2:/tmp/music-b2',
                                       'pid': '1335',
                                       'port': '49153',
                                       'status': 'ONLINE'}],
                           'name': 'music',
                           'nfs': [{'hostname': '192.168.122.2',
                                    'pid': '1357',
                                    'port': '38467',
                                    'status': 'ONLINE'}],
                           'shd': [{'hostname': '192.168.122.2',
                                    'pid': '1375',
                                    'status': 'ONLINE'}]})

    def _parseVolumeStatusDetail_test(self):
        out = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<cliOutput>
  <opRet>0</opRet>
  <opErrno>0</opErrno>
  <opErrstr/>
  <volStatus>
    <volName>music</volName>
    <nodeCount>2</nodeCount>
    <node>
      <hostname>192.168.122.2</hostname>
      <path>/tmp/music-b1</path>
      <port>49152</port>
      <status>1</status>
      <pid>1313</pid>
      <sizeTotal>8370712576</sizeTotal>
      <sizeFree>4478812160</sizeFree>
      <device>/dev/vda1</device>
      <blockSize>4096</blockSize>
      <mntOptions>rw,seclabel,relatime,data=ordered</mntOptions>
      <fsName>ext4</fsName>
    </node>
    <node>
      <hostname>192.168.122.2</hostname>
      <path>/tmp/music-b2</path>
      <port>49153</port>
      <status>1</status>
      <pid>1335</pid>
      <sizeTotal>8370712576</sizeTotal>
      <sizeFree>4478812160</sizeFree>
      <device>/dev/vda1</device>
      <blockSize>4096</blockSize>
      <mntOptions>rw,seclabel,relatime,data=ordered</mntOptions>
      <fsName>ext4</fsName>
    </node>
  </volStatus>
</cliOutput>"""
        tree = etree.fromstring(out)
        oStatus = \
            {'bricks': [{'blockSize': '4096',
                         'brick': '192.168.122.2:/tmp/music-b1',
                         'device': '/dev/vda1',
                         'fsName': 'ext4',
                         'mntOptions': 'rw,seclabel,relatime,data=ordered',
                         'sizeFree': '4271.328',
                         'sizeTotal': '7982.934'},
                        {'blockSize': '4096',
                         'brick': '192.168.122.2:/tmp/music-b2',
                         'device': '/dev/vda1',
                         'fsName': 'ext4',
                         'mntOptions': 'rw,seclabel,relatime,data=ordered',
                         'sizeFree': '4271.328',
                         'sizeTotal': '7982.934'}],
             'name': 'music'}
        status = gcli._parseVolumeStatusDetail(tree)
        self.assertEquals(status, oStatus)

    def _parseVolumeStatusClients_test(self):
        out = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<cliOutput>
  <opRet>0</opRet>
  <opErrno>0</opErrno>
  <opErrstr/>
  <volStatus>
    <volName>music</volName>
    <nodeCount>2</nodeCount>
    <node>
      <hostname>192.168.122.2</hostname>
      <path>/tmp/music-b1</path>
      <port>49152</port>
      <status>1</status>
      <pid>1313</pid>
      <clientsStatus>
        <clientCount>2</clientCount>
        <client>
          <hostname>192.168.122.2:1021</hostname>
          <bytesRead>1172</bytesRead>
          <bytesWrite>792</bytesWrite>
        </client>
        <client>
          <hostname>192.168.122.2:1011</hostname>
          <bytesRead>10076</bytesRead>
          <bytesWrite>12152</bytesWrite>
        </client>
      </clientsStatus>
    </node>
    <node>
      <hostname>192.168.122.2</hostname>
      <path>/tmp/music-b2</path>
      <port>49153</port>
      <status>1</status>
      <pid>1335</pid>
      <clientsStatus>
        <clientCount>2</clientCount>
        <client>
          <hostname>192.168.122.2:1020</hostname>
          <bytesRead>1172</bytesRead>
          <bytesWrite>792</bytesWrite>
        </client>
        <client>
          <hostname>192.168.122.2:1010</hostname>
          <bytesRead>10864</bytesRead>
          <bytesWrite>12816</bytesWrite>
        </client>
      </clientsStatus>
    </node>
  </volStatus>
</cliOutput>
"""
        tree = etree.fromstring(out)
        status = gcli._parseVolumeStatusClients(tree)
        self.assertEquals(status.keys(), ['bricks', 'name'])
        self.assertEquals(status['name'], 'music')
        oBricks = [{'brick': '192.168.122.2:/tmp/music-b1',
                    'clientsStatus': [{'bytesRead': '1172',
                                       'bytesWrite': '792',
                                       'hostname': '192.168.122.2:1021'},
                                      {'bytesRead': '10076',
                                       'bytesWrite': '12152',
                                       'hostname': '192.168.122.2:1011'}]},
                   {'brick': '192.168.122.2:/tmp/music-b2',
                    'clientsStatus': [{'bytesRead': '1172',
                                       'bytesWrite': '792',
                                       'hostname': '192.168.122.2:1020'},
                                      {'bytesRead': '10864',
                                       'bytesWrite': '12816',
                                       'hostname': '192.168.122.2:1010'}]}]
        self.assertEquals(status['bricks'], oBricks)

    def _parseVolumeStatusMem_test(self):
        out = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<cliOutput>
  <opRet>0</opRet>
  <opErrno>0</opErrno>
  <opErrstr/>
  <volStatus>
    <volName>music</volName>
    <nodeCount>2</nodeCount>
    <node>
      <hostname>192.168.122.2</hostname>
      <path>/tmp/music-b1</path>
      <port>49152</port>
      <status>1</status>
      <pid>1452</pid>
      <memStatus>
        <mallinfo>
          <arena>606208</arena>
          <ordblks>6</ordblks>
          <smblks>1</smblks>
          <hblks>12</hblks>
          <hblkhd>15179776</hblkhd>
          <usmblks>0</usmblks>
          <fsmblks>64</fsmblks>
          <uordblks>474208</uordblks>
          <fordblks>132000</fordblks>
          <keepcost>130224</keepcost>
        </mallinfo>
        <mempool>
          <count>15</count>
          <pool>
            <name>music-server:fd_t</name>
            <hotCount>0</hotCount>
            <coldCount>1024</coldCount>
            <padddedSizeOf>100</padddedSizeOf>
            <allocCount>0</allocCount>
            <maxAlloc>0</maxAlloc>
            <poolMisses>0</poolMisses>
            <maxStdAlloc>0</maxStdAlloc>
          </pool>
          <pool>
            <name>music-server:dentry_t</name>
            <hotCount>0</hotCount>
            <coldCount>16384</coldCount>
            <padddedSizeOf>84</padddedSizeOf>
            <allocCount>0</allocCount>
            <maxAlloc>0</maxAlloc>
            <poolMisses>0</poolMisses>
            <maxStdAlloc>0</maxStdAlloc>
          </pool>
          <pool>
            <name>music-server:inode_t</name>
            <hotCount>1</hotCount>
            <coldCount>16383</coldCount>
            <padddedSizeOf>148</padddedSizeOf>
            <allocCount>1</allocCount>
            <maxAlloc>1</maxAlloc>
            <poolMisses>0</poolMisses>
            <maxStdAlloc>0</maxStdAlloc>
          </pool>
          <pool>
            <name>music-locks:pl_local_t</name>
            <hotCount>0</hotCount>
            <coldCount>32</coldCount>
            <padddedSizeOf>140</padddedSizeOf>
            <allocCount>1</allocCount>
            <maxAlloc>1</maxAlloc>
            <poolMisses>0</poolMisses>
            <maxStdAlloc>0</maxStdAlloc>
          </pool>
          <pool>
            <name>music-marker:marker_local_t</name>
            <hotCount>0</hotCount>
            <coldCount>128</coldCount>
            <padddedSizeOf>316</padddedSizeOf>
            <allocCount>0</allocCount>
            <maxAlloc>0</maxAlloc>
            <poolMisses>0</poolMisses>
            <maxStdAlloc>0</maxStdAlloc>
          </pool>
          <pool>
            <name>music-server:rpcsvc_request_t</name>
            <hotCount>0</hotCount>
            <coldCount>512</coldCount>
            <padddedSizeOf>6372</padddedSizeOf>
            <allocCount>10</allocCount>
            <maxAlloc>1</maxAlloc>
            <poolMisses>0</poolMisses>
            <maxStdAlloc>0</maxStdAlloc>
          </pool>
          <pool>
            <name>glusterfs:struct saved_frame</name>
            <hotCount>0</hotCount>
            <coldCount>8</coldCount>
            <padddedSizeOf>124</padddedSizeOf>
            <allocCount>2</allocCount>
            <maxAlloc>2</maxAlloc>
            <poolMisses>0</poolMisses>
            <maxStdAlloc>0</maxStdAlloc>
          </pool>
          <pool>
            <name>glusterfs:struct rpc_req</name>
            <hotCount>0</hotCount>
            <coldCount>8</coldCount>
            <padddedSizeOf>2236</padddedSizeOf>
            <allocCount>2</allocCount>
            <maxAlloc>2</maxAlloc>
            <poolMisses>0</poolMisses>
            <maxStdAlloc>0</maxStdAlloc>
          </pool>
          <pool>
            <name>glusterfs:rpcsvc_request_t</name>
            <hotCount>1</hotCount>
            <coldCount>7</coldCount>
            <padddedSizeOf>6372</padddedSizeOf>
            <allocCount>1</allocCount>
            <maxAlloc>1</maxAlloc>
            <poolMisses>0</poolMisses>
            <maxStdAlloc>0</maxStdAlloc>
          </pool>
          <pool>
            <name>glusterfs:data_t</name>
            <hotCount>117</hotCount>
            <coldCount>16266</coldCount>
            <padddedSizeOf>52</padddedSizeOf>
            <allocCount>179</allocCount>
            <maxAlloc>121</maxAlloc>
            <poolMisses>0</poolMisses>
            <maxStdAlloc>0</maxStdAlloc>
          </pool>
          <pool>
            <name>glusterfs:data_pair_t</name>
            <hotCount>138</hotCount>
            <coldCount>16245</coldCount>
            <padddedSizeOf>68</padddedSizeOf>
            <allocCount>218</allocCount>
            <maxAlloc>142</maxAlloc>
            <poolMisses>0</poolMisses>
            <maxStdAlloc>0</maxStdAlloc>
          </pool>
          <pool>
            <name>glusterfs:dict_t</name>
            <hotCount>13</hotCount>
            <coldCount>4083</coldCount>
            <padddedSizeOf>84</padddedSizeOf>
            <allocCount>24</allocCount>
            <maxAlloc>15</maxAlloc>
            <poolMisses>0</poolMisses>
            <maxStdAlloc>0</maxStdAlloc>
          </pool>
          <pool>
            <name>glusterfs:call_stub_t</name>
            <hotCount>0</hotCount>
            <coldCount>1024</coldCount>
            <padddedSizeOf>1228</padddedSizeOf>
            <allocCount>2</allocCount>
            <maxAlloc>1</maxAlloc>
            <poolMisses>0</poolMisses>
            <maxStdAlloc>0</maxStdAlloc>
          </pool>
          <pool>
            <name>glusterfs:call_stack_t</name>
            <hotCount>0</hotCount>
            <coldCount>1024</coldCount>
            <padddedSizeOf>2084</padddedSizeOf>
            <allocCount>4</allocCount>
            <maxAlloc>2</maxAlloc>
            <poolMisses>0</poolMisses>
            <maxStdAlloc>0</maxStdAlloc>
          </pool>
          <pool>
            <name>glusterfs:call_frame_t</name>
            <hotCount>0</hotCount>
            <coldCount>4096</coldCount>
            <padddedSizeOf>172</padddedSizeOf>
            <allocCount>14</allocCount>
            <maxAlloc>7</maxAlloc>
            <poolMisses>0</poolMisses>
            <maxStdAlloc>0</maxStdAlloc>
          </pool>
        </mempool>
      </memStatus>
    </node>
    <node>
      <hostname>192.168.122.2</hostname>
      <path>/tmp/music-b2</path>
      <port>49153</port>
      <status>1</status>
      <pid>1459</pid>
      <memStatus>
        <mallinfo>
          <arena>606208</arena>
          <ordblks>5</ordblks>
          <smblks>2</smblks>
          <hblks>12</hblks>
          <hblkhd>15179776</hblkhd>
          <usmblks>0</usmblks>
          <fsmblks>128</fsmblks>
          <uordblks>474224</uordblks>
          <fordblks>131984</fordblks>
          <keepcost>130224</keepcost>
        </mallinfo>
        <mempool>
          <count>15</count>
          <pool>
            <name>music-server:fd_t</name>
            <hotCount>0</hotCount>
            <coldCount>1024</coldCount>
            <padddedSizeOf>100</padddedSizeOf>
            <allocCount>0</allocCount>
            <maxAlloc>0</maxAlloc>
            <poolMisses>0</poolMisses>
            <maxStdAlloc>0</maxStdAlloc>
          </pool>
          <pool>
            <name>music-server:dentry_t</name>
            <hotCount>0</hotCount>
            <coldCount>16384</coldCount>
            <padddedSizeOf>84</padddedSizeOf>
            <allocCount>0</allocCount>
            <maxAlloc>0</maxAlloc>
            <poolMisses>0</poolMisses>
            <maxStdAlloc>0</maxStdAlloc>
          </pool>
          <pool>
            <name>music-server:inode_t</name>
            <hotCount>1</hotCount>
            <coldCount>16383</coldCount>
            <padddedSizeOf>148</padddedSizeOf>
            <allocCount>2</allocCount>
            <maxAlloc>2</maxAlloc>
            <poolMisses>0</poolMisses>
            <maxStdAlloc>0</maxStdAlloc>
          </pool>
          <pool>
            <name>music-locks:pl_local_t</name>
            <hotCount>0</hotCount>
            <coldCount>32</coldCount>
            <padddedSizeOf>140</padddedSizeOf>
            <allocCount>1</allocCount>
            <maxAlloc>1</maxAlloc>
            <poolMisses>0</poolMisses>
            <maxStdAlloc>0</maxStdAlloc>
          </pool>
          <pool>
            <name>music-marker:marker_local_t</name>
            <hotCount>0</hotCount>
            <coldCount>128</coldCount>
            <padddedSizeOf>316</padddedSizeOf>
            <allocCount>0</allocCount>
            <maxAlloc>0</maxAlloc>
            <poolMisses>0</poolMisses>
            <maxStdAlloc>0</maxStdAlloc>
          </pool>
          <pool>
            <name>music-server:rpcsvc_request_t</name>
            <hotCount>0</hotCount>
            <coldCount>512</coldCount>
            <padddedSizeOf>6372</padddedSizeOf>
            <allocCount>12</allocCount>
            <maxAlloc>1</maxAlloc>
            <poolMisses>0</poolMisses>
            <maxStdAlloc>0</maxStdAlloc>
          </pool>
          <pool>
            <name>glusterfs:struct saved_frame</name>
            <hotCount>0</hotCount>
            <coldCount>8</coldCount>
            <padddedSizeOf>124</padddedSizeOf>
            <allocCount>2</allocCount>
            <maxAlloc>2</maxAlloc>
            <poolMisses>0</poolMisses>
            <maxStdAlloc>0</maxStdAlloc>
          </pool>
          <pool>
            <name>glusterfs:struct rpc_req</name>
            <hotCount>0</hotCount>
            <coldCount>8</coldCount>
            <padddedSizeOf>2236</padddedSizeOf>
            <allocCount>2</allocCount>
            <maxAlloc>2</maxAlloc>
            <poolMisses>0</poolMisses>
            <maxStdAlloc>0</maxStdAlloc>
          </pool>
          <pool>
            <name>glusterfs:rpcsvc_request_t</name>
            <hotCount>1</hotCount>
            <coldCount>7</coldCount>
            <padddedSizeOf>6372</padddedSizeOf>
            <allocCount>1</allocCount>
            <maxAlloc>1</maxAlloc>
            <poolMisses>0</poolMisses>
            <maxStdAlloc>0</maxStdAlloc>
          </pool>
          <pool>
            <name>glusterfs:data_t</name>
            <hotCount>117</hotCount>
            <coldCount>16266</coldCount>
            <padddedSizeOf>52</padddedSizeOf>
            <allocCount>180</allocCount>
            <maxAlloc>121</maxAlloc>
            <poolMisses>0</poolMisses>
            <maxStdAlloc>0</maxStdAlloc>
          </pool>
          <pool>
            <name>glusterfs:data_pair_t</name>
            <hotCount>138</hotCount>
            <coldCount>16245</coldCount>
            <padddedSizeOf>68</padddedSizeOf>
            <allocCount>220</allocCount>
            <maxAlloc>142</maxAlloc>
            <poolMisses>0</poolMisses>
            <maxStdAlloc>0</maxStdAlloc>
          </pool>
          <pool>
            <name>glusterfs:dict_t</name>
            <hotCount>13</hotCount>
            <coldCount>4083</coldCount>
            <padddedSizeOf>84</padddedSizeOf>
            <allocCount>25</allocCount>
            <maxAlloc>15</maxAlloc>
            <poolMisses>0</poolMisses>
            <maxStdAlloc>0</maxStdAlloc>
          </pool>
          <pool>
            <name>glusterfs:call_stub_t</name>
            <hotCount>0</hotCount>
            <coldCount>1024</coldCount>
            <padddedSizeOf>1228</padddedSizeOf>
            <allocCount>4</allocCount>
            <maxAlloc>1</maxAlloc>
            <poolMisses>0</poolMisses>
            <maxStdAlloc>0</maxStdAlloc>
          </pool>
          <pool>
            <name>glusterfs:call_stack_t</name>
            <hotCount>0</hotCount>
            <coldCount>1024</coldCount>
            <padddedSizeOf>2084</padddedSizeOf>
            <allocCount>6</allocCount>
            <maxAlloc>2</maxAlloc>
            <poolMisses>0</poolMisses>
            <maxStdAlloc>0</maxStdAlloc>
          </pool>
          <pool>
            <name>glusterfs:call_frame_t</name>
            <hotCount>0</hotCount>
            <coldCount>4096</coldCount>
            <padddedSizeOf>172</padddedSizeOf>
            <allocCount>20</allocCount>
            <maxAlloc>7</maxAlloc>
            <poolMisses>0</poolMisses>
            <maxStdAlloc>0</maxStdAlloc>
          </pool>
        </mempool>
      </memStatus>
    </node>
  </volStatus>
</cliOutput>
"""
        ostatus = \
            {'bricks': [{'brick': '192.168.122.2:/tmp/music-b1',
                         'mallinfo': {'arena': '606208',
                                      'fordblks': '132000',
                                      'fsmblks': '64',
                                      'hblkhd': '15179776',
                                      'hblks': '12',
                                      'keepcost': '130224',
                                      'ordblks': '6',
                                      'smblks': '1',
                                      'uordblks': '474208',
                                      'usmblks': '0'},
                         'mempool': [{'allocCount': '0',
                                      'coldCount': '1024',
                                      'hotCount': '0',
                                      'maxAlloc': '0',
                                      'maxStdAlloc': '0',
                                      'name': 'music-server:fd_t',
                                      'padddedSizeOf': '100',
                                      'poolMisses': '0'},
                                     {'allocCount': '0',
                                      'coldCount': '16384',
                                      'hotCount': '0',
                                      'maxAlloc': '0',
                                      'maxStdAlloc': '0',
                                      'name': 'music-server:dentry_t',
                                      'padddedSizeOf': '84',
                                      'poolMisses': '0'},
                                     {'allocCount': '1',
                                      'coldCount': '16383',
                                      'hotCount': '1',
                                      'maxAlloc': '1',
                                      'maxStdAlloc': '0',
                                      'name': 'music-server:inode_t',
                                      'padddedSizeOf': '148',
                                      'poolMisses': '0'},
                                     {'allocCount': '1',
                                      'coldCount': '32',
                                      'hotCount': '0',
                                      'maxAlloc': '1',
                                      'maxStdAlloc': '0',
                                      'name': 'music-locks:pl_local_t',
                                      'padddedSizeOf': '140',
                                      'poolMisses': '0'},
                                     {'allocCount': '0',
                                      'coldCount': '128',
                                      'hotCount': '0',
                                      'maxAlloc': '0',
                                      'maxStdAlloc': '0',
                                      'name': 'music-marker:marker_local_t',
                                      'padddedSizeOf': '316',
                                      'poolMisses': '0'},
                                     {'allocCount': '10',
                                      'coldCount': '512',
                                      'hotCount': '0',
                                      'maxAlloc': '1',
                                      'maxStdAlloc': '0',
                                      'name': 'music-server:rpcsvc_request_t',
                                      'padddedSizeOf': '6372',
                                      'poolMisses': '0'},
                                     {'allocCount': '2',
                                      'coldCount': '8',
                                      'hotCount': '0',
                                      'maxAlloc': '2',
                                      'maxStdAlloc': '0',
                                      'name': 'glusterfs:struct saved_frame',
                                      'padddedSizeOf': '124',
                                      'poolMisses': '0'},
                                     {'allocCount': '2',
                                      'coldCount': '8',
                                      'hotCount': '0',
                                      'maxAlloc': '2',
                                      'maxStdAlloc': '0',
                                      'name': 'glusterfs:struct rpc_req',
                                      'padddedSizeOf': '2236',
                                      'poolMisses': '0'},
                                     {'allocCount': '1',
                                      'coldCount': '7',
                                      'hotCount': '1',
                                      'maxAlloc': '1',
                                      'maxStdAlloc': '0',
                                      'name': 'glusterfs:rpcsvc_request_t',
                                      'padddedSizeOf': '6372',
                                      'poolMisses': '0'},
                                     {'allocCount': '179',
                                      'coldCount': '16266',
                                      'hotCount': '117',
                                      'maxAlloc': '121',
                                      'maxStdAlloc': '0',
                                      'name': 'glusterfs:data_t',
                                      'padddedSizeOf': '52',
                                      'poolMisses': '0'},
                                     {'allocCount': '218',
                                      'coldCount': '16245',
                                      'hotCount': '138',
                                      'maxAlloc': '142',
                                      'maxStdAlloc': '0',
                                      'name': 'glusterfs:data_pair_t',
                                      'padddedSizeOf': '68',
                                      'poolMisses': '0'},
                                     {'allocCount': '24',
                                      'coldCount': '4083',
                                      'hotCount': '13',
                                      'maxAlloc': '15',
                                      'maxStdAlloc': '0',
                                      'name': 'glusterfs:dict_t',
                                      'padddedSizeOf': '84',
                                      'poolMisses': '0'},
                                     {'allocCount': '2',
                                      'coldCount': '1024',
                                      'hotCount': '0',
                                      'maxAlloc': '1',
                                      'maxStdAlloc': '0',
                                      'name': 'glusterfs:call_stub_t',
                                      'padddedSizeOf': '1228',
                                      'poolMisses': '0'},
                                     {'allocCount': '4',
                                      'coldCount': '1024',
                                      'hotCount': '0',
                                      'maxAlloc': '2',
                                      'maxStdAlloc': '0',
                                      'name': 'glusterfs:call_stack_t',
                                      'padddedSizeOf': '2084',
                                      'poolMisses': '0'},
                                     {'allocCount': '14',
                                      'coldCount': '4096',
                                      'hotCount': '0',
                                      'maxAlloc': '7',
                                      'maxStdAlloc': '0',
                                      'name': 'glusterfs:call_frame_t',
                                      'padddedSizeOf': '172',
                                      'poolMisses': '0'}]},
                        {'brick': '192.168.122.2:/tmp/music-b2',
                         'mallinfo': {'arena': '606208',
                                      'fordblks': '131984',
                                      'fsmblks': '128',
                                      'hblkhd': '15179776',
                                      'hblks': '12',
                                      'keepcost': '130224',
                                      'ordblks': '5',
                                      'smblks': '2',
                                      'uordblks': '474224',
                                      'usmblks': '0'},
                         'mempool': [{'allocCount': '0',
                                      'coldCount': '1024',
                                      'hotCount': '0',
                                      'maxAlloc': '0',
                                      'maxStdAlloc': '0',
                                      'name': 'music-server:fd_t',
                                      'padddedSizeOf': '100',
                                      'poolMisses': '0'},
                                     {'allocCount': '0',
                                      'coldCount': '16384',
                                      'hotCount': '0',
                                      'maxAlloc': '0',
                                      'maxStdAlloc': '0',
                                      'name': 'music-server:dentry_t',
                                      'padddedSizeOf': '84',
                                      'poolMisses': '0'},
                                     {'allocCount': '2',
                                      'coldCount': '16383',
                                      'hotCount': '1',
                                      'maxAlloc': '2',
                                      'maxStdAlloc': '0',
                                      'name': 'music-server:inode_t',
                                      'padddedSizeOf': '148',
                                      'poolMisses': '0'},
                                     {'allocCount': '1',
                                      'coldCount': '32',
                                      'hotCount': '0',
                                      'maxAlloc': '1',
                                      'maxStdAlloc': '0',
                                      'name': 'music-locks:pl_local_t',
                                      'padddedSizeOf': '140',
                                      'poolMisses': '0'},
                                     {'allocCount': '0',
                                      'coldCount': '128',
                                      'hotCount': '0',
                                      'maxAlloc': '0',
                                      'maxStdAlloc': '0',
                                      'name': 'music-marker:marker_local_t',
                                      'padddedSizeOf': '316',
                                      'poolMisses': '0'},
                                     {'allocCount': '12',
                                      'coldCount': '512',
                                      'hotCount': '0',
                                      'maxAlloc': '1',
                                      'maxStdAlloc': '0',
                                      'name': 'music-server:rpcsvc_request_t',
                                      'padddedSizeOf': '6372',
                                      'poolMisses': '0'},
                                     {'allocCount': '2',
                                      'coldCount': '8',
                                      'hotCount': '0',
                                      'maxAlloc': '2',
                                      'maxStdAlloc': '0',
                                      'name': 'glusterfs:struct saved_frame',
                                      'padddedSizeOf': '124',
                                      'poolMisses': '0'},
                                     {'allocCount': '2',
                                      'coldCount': '8',
                                      'hotCount': '0',
                                      'maxAlloc': '2',
                                      'maxStdAlloc': '0',
                                      'name': 'glusterfs:struct rpc_req',
                                      'padddedSizeOf': '2236',
                                      'poolMisses': '0'},
                                     {'allocCount': '1',
                                      'coldCount': '7',
                                      'hotCount': '1',
                                      'maxAlloc': '1',
                                      'maxStdAlloc': '0',
                                      'name': 'glusterfs:rpcsvc_request_t',
                                      'padddedSizeOf': '6372',
                                      'poolMisses': '0'},
                                     {'allocCount': '180',
                                      'coldCount': '16266',
                                      'hotCount': '117',
                                      'maxAlloc': '121',
                                      'maxStdAlloc': '0',
                                      'name': 'glusterfs:data_t',
                                      'padddedSizeOf': '52',
                                      'poolMisses': '0'},
                                     {'allocCount': '220',
                                      'coldCount': '16245',
                                      'hotCount': '138',
                                      'maxAlloc': '142',
                                      'maxStdAlloc': '0',
                                      'name': 'glusterfs:data_pair_t',
                                      'padddedSizeOf': '68',
                                      'poolMisses': '0'},
                                     {'allocCount': '25',
                                      'coldCount': '4083',
                                      'hotCount': '13',
                                      'maxAlloc': '15',
                                      'maxStdAlloc': '0',
                                      'name': 'glusterfs:dict_t',
                                      'padddedSizeOf': '84',
                                      'poolMisses': '0'},
                                     {'allocCount': '4',
                                      'coldCount': '1024',
                                      'hotCount': '0',
                                      'maxAlloc': '1',
                                      'maxStdAlloc': '0',
                                      'name': 'glusterfs:call_stub_t',
                                      'padddedSizeOf': '1228',
                                      'poolMisses': '0'},
                                     {'allocCount': '6',
                                      'coldCount': '1024',
                                      'hotCount': '0',
                                      'maxAlloc': '2',
                                      'maxStdAlloc': '0',
                                      'name': 'glusterfs:call_stack_t',
                                      'padddedSizeOf': '2084',
                                      'poolMisses': '0'},
                                     {'allocCount': '20',
                                      'coldCount': '4096',
                                      'hotCount': '0',
                                      'maxAlloc': '7',
                                      'maxStdAlloc': '0',
                                      'name': 'glusterfs:call_frame_t',
                                      'padddedSizeOf': '172',
                                      'poolMisses': '0'}]}],
             'name': 'music'}
        tree = etree.fromstring(out)
        status = gcli._parseVolumeStatusMem(tree)
        self.assertEquals(status, ostatus)

    def test_parseVolumeStatus(self):
        self._parseVolumeStatus_test()
        self._parseVolumeStatusDetail_test()
        self._parseVolumeStatusClients_test()
        self._parseVolumeStatusMem_test()
