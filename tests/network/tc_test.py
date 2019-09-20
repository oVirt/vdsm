#
# Copyright 2012 Roman Fenkhuber.
# Copyright 2012-2016 Red Hat, Inc.
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
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA
#
# Refer to the README and COPYING files for full details of the license
#

from __future__ import absolute_import
from __future__ import division
from collections import namedtuple
import time
import os
import sys
from binascii import unhexlify

from nose.plugins.attrib import attr

import six
from six.moves import zip_longest

from testlib import (
    VdsmTestCase as TestCaseBase,
    permutations,
    expandPermutations,
)
from testlib import mock
from testValidation import ValidateRunningAsRoot, stresstest, skipif
from .nettestlib import (
    Bridge,
    Dummy,
    IperfClient,
    IperfServer,
    Tap,
    bridge_device,
    network_namespace,
    requires_iperf3,
    requires_tc,
    requires_tun,
    veth_pair,
    vlan_device,
)
from .nettestlib import running
from .nettestlib import EXT_TC

from vdsm.network import cmd
from vdsm.network import tc
from vdsm.network.configurators import qos
from vdsm.network.ipwrapper import addrAdd, linkSet, netns_exec, link_set_netns
from vdsm.network.netinfo.qos import DEFAULT_CLASSID


class TestQdisc(TestCaseBase):
    @ValidateRunningAsRoot
    @requires_tc
    def setUp(self):
        self._bridge = Bridge()
        self._bridge.addDevice()

    def tearDown(self):
        self._bridge.delDevice()

    def _showQdisc(self):
        _, out, _ = cmd.exec_sync(
            [EXT_TC, "qdisc", "show", "dev", self._bridge.devName]
        )
        return out

    def _addIngress(self):
        tc._qdisc_replace_ingress(self._bridge.devName)
        self.assertIn("qdisc ingress", self._showQdisc())

    def testToggleIngress(self):
        self._addIngress()
        tc._qdisc_del(self._bridge.devName, 'ingress')
        self.assertNotIn("qdisc ingress", self._showQdisc())

    def testQdiscsOfDevice(self):
        self._addIngress()
        self.assertEqual(
            ("ffff:",), tuple(tc._qdiscs_of_device(self._bridge.devName))
        )

    def testReplacePrio(self):
        self._addIngress()
        tc.qdisc.replace(self._bridge.devName, 'prio', parent=None)
        self.assertIn("root", self._showQdisc())

    def testException(self):
        self.assertRaises(
            tc.TrafficControlException,
            tc._qdisc_del,
            "__nosuchiface__",
            'ingress',
        )


@attr(type='unit')
class TestFilters(TestCaseBase):
    def test_filter_objs(self):
        dirName = os.path.dirname(os.path.realpath(__file__))
        path = os.path.join(dirName, "tc_filter_show.out")
        with open(path) as f:
            out = f.read()
        PARSED_FILTERS = (
            tc.Filter(
                prio=49149,
                handle='803::800',
                actions=[tc.MirredAction(target='tap1')],
            ),
            tc.Filter(
                prio=49150,
                handle='802::800',
                actions=[tc.MirredAction(target='tap2')],
            ),
            tc.Filter(
                prio=49152,
                handle='800::800',
                actions=[
                    tc.MirredAction(target='target'),
                    tc.MirredAction(target='target2'),
                ],
            ),
        )
        self.assertEqual(
            tuple(tc.filters('bridge', 'parent', out=out)), PARSED_FILTERS
        )

    def test_filters(self):
        filters = (
            {
                'protocol': 'all',
                'pref': 168,
                'kind': 'basic',
                'parent': '1389:',
                'basic': {},
            },
            {
                'protocol': 'all',
                'pref': 168,
                'kind': 'basic',
                'parent': '1389:',
                'basic': {
                    'flowid': '1389:a8',
                    'handle': '0x1',
                    'mask': 0,
                    'module': 'meta',
                    'object': 'vlan',
                    'relation': 'eq',
                    'value': 168,
                },
            },
            {
                'protocol': 'all',
                'pref': 168,
                'kind': 'basic',
                'parent': '1389:',
                'basic': {
                    'flowid': '1389:a8',
                    'handle': '0x1',
                    'mask': 0,
                    'module': 'meta',
                    'object': 'vlan',
                },
            },
            {
                'protocol': 'all',
                'pref': 168,
                'kind': 'basic',
                'parent': '1389:',
                'basic': {
                    'module': 'meta',
                    'flowid': '1389:a8',
                    'handle': '0x1',
                },
            },
            {'protocol': 'all', 'pref': 49149, 'kind': 'u32', 'u32': {}},
            {
                'protocol': 'all',
                'pref': 49149,
                'kind': 'u32',
                'u32': {'fh': '803:', 'ht_divisor': 1},
            },
            {
                'protocol': 'all',
                'pref': 49149,
                'kind': 'u32',
                'u32': {
                    'fh': '803::800',
                    'order': 2048,
                    'key_ht': 0x803,
                    'key_bkt': 0x0,
                    'terminal': True,
                    'match': {'value': 0x0, 'mask': 0x0, 'offset': 0x0},
                    'actions': [
                        {
                            'order': 1,
                            'kind': 'mirred',
                            'action': 'egress_mirror',
                            'target': 'tap1',
                            'op': 'pipe',
                            'index': 18,
                            'ref': 1,
                            'bind': 1,
                        }
                    ],
                },
            },
            {'protocol': 'all', 'pref': 49150, 'kind': 'u32', 'u32': {}},
            {
                'protocol': 'all',
                'pref': 49150,
                'kind': 'u32',
                'u32': {'fh': '802:', 'ht_divisor': 1},
            },
            {
                'protocol': 'all',
                'pref': 49150,
                'kind': 'u32',
                'u32': {
                    'fh': '802::800',
                    'order': 2048,
                    'key_ht': 0x802,
                    'key_bkt': 0x0,
                    'terminal': True,
                    'match': {'value': 0x0, 'mask': 0x0, 'offset': 0x0},
                    'actions': [
                        {
                            'order': 33,
                            'kind': 'mirred',
                            'action': 'egress_mirror',
                            'target': 'tap2',
                            'op': 'pipe',
                            'index': 17,
                            'ref': 1,
                            'bind': 1,
                        }
                    ],
                },
            },
            {'protocol': 'all', 'pref': 49152, 'kind': 'u32', 'u32': {}},
            {
                'protocol': 'all',
                'pref': 49152,
                'kind': 'u32',
                'u32': {'fh': '800:', 'ht_divisor': 1},
            },
            {
                'protocol': 'all',
                'pref': 49152,
                'kind': 'u32',
                'u32': {
                    'fh': '800::800',
                    'order': 2048,
                    'key_ht': 0x800,
                    'key_bkt': 0x0,
                    'terminal': True,
                    'match': {'value': 0x0, 'mask': 0x0, 'offset': 0x0},
                    'actions': [
                        {
                            'order': 1,
                            'kind': 'mirred',
                            'action': 'egress_mirror',
                            'target': 'target',
                            'op': 'pipe',
                            'index': 60,
                            'ref': 1,
                            'bind': 1,
                        },
                        {
                            'order': 2,
                            'kind': 'mirred',
                            'action': 'egress_mirror',
                            'target': 'target2',
                            'op': 'pipe',
                            'index': 61,
                            'ref': 1,
                            'bind': 1,
                        },
                    ],
                },
            },
        )
        dirName = os.path.dirname(os.path.realpath(__file__))
        path = os.path.join(dirName, "tc_filter_show.out")
        with open(path) as tc_filter_show:
            data = tc_filter_show.read()

        for parsed, correct in zip_longest(
            tc._filters(None, out=data), filters
        ):
            self.assertEqual(parsed, correct)

    def test_qdiscs(self):
        data_lines = (
            'qdisc hfsc 1: root refcnt 2 default 5000',
            'qdisc sfq 10: parent 1:10 limit 127p quantum 1514b',
            'qdisc sfq 20: parent 1:20 limit 127p quantum 1514b',
            'qdisc sfq 30: parent 1:30 limit 127p quantum 30Kb perturb 3sec',
            'qdisc sfq 40: parent 1:40 limit 127p quantum 20Mb perturb 5sec',
            'qdisc ingress ffff: parent ffff:fff1 ----------------',
            'qdisc mq 0: dev wlp3s0 root',
            'qdisc ingress ffff: dev vdsmtest-Z2TMO parent ffff:fff1 '
            '----------------',  # end of previous line
            'qdisc pfifo_fast 0: dev em1 root refcnt 2 bands 3 priomap  '
            '1 2 2 2 1 2 0 0 1 1 1 1 1 1 1 1',  # end of previous line
            'qdisc pfifo_fast 0: dev wlp3s0 parent :1 bands 3 priomap  '
            '1 2 2 2 1 2 0 0 1 1 1 1 1 1 1 1',  # end of previous line
            'qdisc fq_codel 801e: root refcnt 2 limit 132p flows 15 quantum '
            '400 target 5.0ms interval 150.0ms ecn',  # end of previous line
        )
        data = '\n'.join(data_lines)
        qdiscs = (
            {
                'kind': 'hfsc',
                'root': True,
                'handle': '1:',
                'refcnt': 2,
                'hfsc': {'default': 0x5000},
            },
            {
                'kind': 'sfq',
                'handle': '10:',
                'parent': '1:10',
                'sfq': {'limit': 127, 'quantum': 1514},
            },
            {
                'kind': 'sfq',
                'handle': '20:',
                'parent': '1:20',
                'sfq': {'limit': 127, 'quantum': 1514},
            },
            {
                'kind': 'sfq',
                'handle': '30:',
                'parent': '1:30',
                'sfq': {'limit': 127, 'quantum': 30 * 1024, 'perturb': 3},
            },
            {
                'kind': 'sfq',
                'handle': '40:',
                'parent': '1:40',
                'sfq': {'limit': 127, 'quantum': 20 * 1024 ** 2, 'perturb': 5},
            },
            {'kind': 'ingress', 'handle': 'ffff:', 'parent': 'ffff:fff1'},
            {'kind': 'mq', 'handle': '0:', 'dev': 'wlp3s0', 'root': True},
            {
                'kind': 'ingress',
                'handle': 'ffff:',
                'dev': 'vdsmtest-Z2TMO',
                'parent': 'ffff:fff1',
            },
            {
                'kind': 'pfifo_fast',
                'handle': '0:',
                'dev': 'em1',
                'root': True,
                'refcnt': 2,
                'pfifo_fast': {
                    'bands': 3,
                    'priomap': [1, 2, 2, 2, 1, 2, 0, 0, 1, 1, 1, 1, 1, 1, 1],
                },
            },
            {
                'kind': 'pfifo_fast',
                'handle': '0:',
                'dev': 'wlp3s0',
                'parent': ':1',
                'pfifo_fast': {
                    'bands': 3,
                    'priomap': [1, 2, 2, 2, 1, 2, 0, 0, 1, 1, 1, 1, 1, 1, 1],
                },
            },
            {
                'kind': 'fq_codel',
                'handle': '801e:',
                'root': True,
                'refcnt': 2,
                'fq_codel': {
                    'limit': 132,
                    'flows': 15,
                    'quantum': 400,
                    'target': 5000.0,
                    'interval': 150000.0,
                    'ecn': True,
                },
            },
        )
        for parsed, correct in zip_longest(tc.qdiscs(None, out=data), qdiscs):
            self.assertEqual(parsed, correct)

    def test_classes(self):
        cmd_line_ls_10 = 3200
        cmd_line_ls_m1_20 = 6400
        cmd_line_ls_d_20 = 152
        cmd_line_ls_m2_20 = 3200
        cmd_line_ls_30 = 3500
        cmd_line_ls_m2_5000 = 40000
        data = '\n'.join(
            (
                'class hfsc 1: root',
                'class hfsc 1:10 parent 1: leaf 10: sc m1 0bit d 0us '
                'm2 {0}Kbit'.format(cmd_line_ls_10),  # end of previous line
                'class hfsc 1:20 parent 1: leaf 20: ls m1 {0}Kibit d {1}us '
                'm2 {2}Kbit ul m1 0bit d 0us m2 30000Kbit'.format(
                    cmd_line_ls_m1_20, cmd_line_ls_d_20, cmd_line_ls_m2_20
                ),
                'class hfsc 1:30 parent 1: leaf 40: sc m1 0bit d 0us '
                'm2 {0}bit'.format(cmd_line_ls_30),  # end of previous line
                'class hfsc 1:5000 parent 1: leaf 5000: ls m1 0bit d 0us '
                'm2 {0}Kbit'.format(
                    cmd_line_ls_m2_5000
                ),  # end of previous line
            )
        )
        reported_ls_10 = cmd_line_ls_10 * 1000 // 8
        reported_ls_m1_20 = cmd_line_ls_m1_20 * 1024 // 8
        reported_ls_d_20 = cmd_line_ls_d_20 // 8
        reported_ls_m2_20 = cmd_line_ls_m2_20 * 1000 // 8
        reported_ls_30 = cmd_line_ls_30 // 8
        reported_ls_5000 = cmd_line_ls_m2_5000 * 1000 // 8
        classes = (
            {'kind': 'hfsc', 'root': True, 'handle': '1:'},
            {
                'kind': 'hfsc',
                'handle': '1:10',
                'parent': '1:',
                'leaf': '10:',
                'hfsc': {
                    'ls': {'m1': 0, 'd': 0, 'm2': reported_ls_10},
                    'rt': {'m1': 0, 'd': 0, 'm2': reported_ls_10},
                },
            },
            {
                'kind': 'hfsc',
                'handle': '1:20',
                'parent': '1:',
                'leaf': '20:',
                'hfsc': {
                    'ls': {
                        'm1': reported_ls_m1_20,
                        'd': reported_ls_d_20,
                        'm2': reported_ls_m2_20,
                    },
                    'ul': {'m1': 0, 'd': 0, 'm2': 30000 * 1000},
                },
            },
            {
                'kind': 'hfsc',
                'handle': '1:30',
                'parent': '1:',
                'leaf': '40:',
                'hfsc': {
                    'ls': {'m1': 0, 'd': 0, 'm2': reported_ls_30},
                    'rt': {'m1': 0, 'd': 0, 'm2': reported_ls_30},
                },
            },
            {
                'kind': 'hfsc',
                'handle': '1:5000',
                'parent': '1:',
                'leaf': '5000:',
                'hfsc': {'ls': {'m1': 0, 'd': 0, 'm2': reported_ls_5000}},
            },
        )
        for parsed, correct in zip_longest(
            tc.classes(None, out=data), classes
        ):
            self.assertEqual(parsed, correct)


class TestPortMirror(TestCaseBase):

    """
    Tests port mirroring of IP traffic between two bridges.

    This test brings up two tap devices and attaches every device to a
    separate bridge. Then mirroring of IP packets between the two bridges is
    enabled. If sent through _tap0 the packet _ICMP arrives on _tap1 the test
    succeeds. The tap devices are needed because the tc filter rules only
    become active when the bridge is ready, and the bridge only becomes ready
    when it is attached to an active device.
    """

    #  [ Ethernet ]
    #  dst       = 00:1c:c0:d0:44:dc
    #  src       = 00:21:5c:4d:42:75
    #  type      = IPv4
    #     [ IP ]
    #     version   = 4L
    #     ihl       = 5L
    #     tos       = 0x0
    #     len       = 33
    #     id        = 1
    #     flags     =
    #     frag      = 0L
    #     ttl       = 64
    #     proto     = icmp
    #     chksum    = 0xf953
    #     src       = 192.168.0.52
    #     dst       = 192.168.0.3
    #     \options   \
    #        [ ICMP ]
    #        type      = echo-request
    #        code      = 0
    #        chksum    = 0x2875
    #        id        = 0x0
    #        seq       = 0x0
    #           [ Raw ]
    #           load      = '\x01#Eg\x89'
    _ICMP = unhexlify(
        '001cc0d044dc'
        '00215c4d4275'
        '0800'  # Ethernet
        '45000021000100004001f953'
        'c0a80034'
        'c0a80003'  # IP
        '080028750000000'  # ICMP
        '00123456789'
    )  # Payload

    @ValidateRunningAsRoot
    @requires_tc
    @requires_tun
    def setUp(self):
        self._tap0 = Tap()
        self._tap1 = Tap()
        self._tap2 = Tap()
        self._bridge0 = Bridge('src-')
        self._bridge1 = Bridge('target-')
        self._bridge2 = Bridge('target2-')
        self._devices = [
            self._tap0,
            self._tap1,
            self._tap2,
            self._bridge0,
            self._bridge1,
            self._bridge2,
        ]
        # If setUp raise, teardown is not called, so we should either succeed,
        # or fail without leaving junk around.
        cleanup = []
        try:
            for iface in self._devices:
                iface.addDevice()
                cleanup.append(iface)
            self._bridge0.addIf(self._tap0.devName)
            self._bridge1.addIf(self._tap1.devName)
            self._bridge2.addIf(self._tap2.devName)
        except:
            t, v, tb = sys.exc_info()
            for iface in cleanup:
                try:
                    iface.delDevice()
                except Exception:
                    self.log.exception("Error removing device %s" % iface)
            six.reraise(t, v, tb)

    def tearDown(self):
        failed = False
        for iface in self._devices:
            try:
                iface.delDevice()
            except Exception:
                self.log.exception("Error removing device %s" % iface)
                failed = True
        if failed:
            raise RuntimeError("Error tearing down interfaces")

    def _sendPing(self):
        self._tap1.startListener(self._ICMP)
        self._tap0.writeToDevice(self._ICMP)
        # Attention: sleep is bad programming practice! Never use it for
        # synchronization in productive code!
        time.sleep(0.1)
        if self._tap1.isListenerAlive():
            self._tap1.stopListener()
            return False
        else:
            return True

    @skipif(six.PY3, "needs porting to python 3")
    def testMirroring(self):
        tc.setPortMirroring(self._bridge0.devName, self._bridge1.devName)
        self.assertTrue(
            self._sendPing(), "Bridge received no mirrored ping " "requests."
        )

        tc.unsetPortMirroring(self._bridge0.devName, self._bridge1.devName)
        self.assertFalse(
            self._sendPing(),
            "Bridge received mirrored ping "
            "requests, but mirroring is unset.",
        )

    @skipif(six.PY3, "needs porting to python 3")
    def testMirroringWithDistraction(self):
        "setting another mirror action should not obstract the first one"
        tc.setPortMirroring(self._bridge0.devName, self._bridge2.devName)
        self.testMirroring()
        tc.unsetPortMirroring(self._bridge0.devName, self._bridge2.devName)


HOST_QOS_OUTBOUND = {
    'ls': {
        'm1': 4 * 1000 ** 2,  # 4Mbit/s
        'd': 100 * 1000,  # 100 microseconds
        'm2': 3 * 1000 ** 2,
    },  # 3Mbit/s
    'ul': {'m2': 8 * 1000 ** 2},
}  # 8Mbit/s

TcClasses = namedtuple('TcClasses', 'classes, default_class, root_class')
TcQdiscs = namedtuple('TcQdiscs', 'leaf_qdiscs, ingress_qdisc, root_qdisc')
TcFilters = namedtuple('TcFilters', 'untagged_filters, tagged_filters')


@expandPermutations
class TestConfigureOutbound(TestCaseBase):
    def setUp(self):
        self.device = Dummy()
        self.device.create()
        self.device.up()
        self.device_name = self.device.devName

    # TODO:
    # test remove_outbound

    def tearDown(self):
        self.device.remove()

    def test_single_non_vlan(self):
        qos.configure_outbound(HOST_QOS_OUTBOUND, self.device_name, None)
        tc_classes, tc_filters, tc_qdiscs = (
            self._analyse_qos_and_general_assertions()
        )
        self.assertEqual(tc_classes.classes, [])

        self.assertEqual(len(tc_qdiscs.leaf_qdiscs), 1)
        self.assertIsNotNone(self._non_vlan_qdisc(tc_qdiscs.leaf_qdiscs))
        self._assert_parent(tc_qdiscs.leaf_qdiscs, tc_classes.default_class)

        self.assertEqual(len(tc_filters.tagged_filters), 0)

    @permutations([[1], [2]])
    @mock.patch('vdsm.network.netinfo.bonding.permanent_address', lambda: {})
    def test_single_vlan(self, repeating_calls):
        with vlan_device(self.device_name) as vlan:
            for _ in range(repeating_calls):
                qos.configure_outbound(
                    HOST_QOS_OUTBOUND, self.device_name, vlan.tag
                )
            tc_classes, tc_filters, tc_qdiscs = (
                self._analyse_qos_and_general_assertions()
            )
            self.assertEqual(len(tc_classes.classes), 1)

            self.assertEqual(len(tc_qdiscs.leaf_qdiscs), 2)
            vlan_qdisc = self._vlan_qdisc(tc_qdiscs.leaf_qdiscs, vlan.tag)
            vlan_class = self._vlan_class(tc_classes.classes, vlan.tag)
            self._assert_parent([vlan_qdisc], vlan_class)

            self.assertEqual(len(tc_filters.tagged_filters), 1)
            self.assertEqual(
                int(tc_filters.tagged_filters[0]['basic']['value']), vlan.tag
            )

    @mock.patch('vdsm.network.netinfo.bonding.permanent_address', lambda: {})
    def test_multiple_vlans(self):
        with vlan_device(self.device_name, tag=16) as vlan1:
            with vlan_device(self.device_name, tag=17) as vlan2:
                for v in (vlan1, vlan2):
                    qos.configure_outbound(
                        HOST_QOS_OUTBOUND, self.device_name, v.tag
                    )

                tc_classes, tc_filters, tc_qdiscs = (
                    self._analyse_qos_and_general_assertions()
                )
                self.assertEqual(len(tc_classes.classes), 2)

                self.assertEqual(len(tc_qdiscs.leaf_qdiscs), 3)
                v1_qdisc = self._vlan_qdisc(tc_qdiscs.leaf_qdiscs, vlan1.tag)
                v2_qdisc = self._vlan_qdisc(tc_qdiscs.leaf_qdiscs, vlan2.tag)
                v1_class = self._vlan_class(tc_classes.classes, vlan1.tag)
                v2_class = self._vlan_class(tc_classes.classes, vlan2.tag)
                self._assert_parent([v1_qdisc], v1_class)
                self._assert_parent([v2_qdisc], v2_class)

                self.assertEqual(len(tc_filters.tagged_filters), 2)
                current_tagged_filters_flow_id = set(
                    f['basic']['flowid'] for f in tc_filters.tagged_filters
                )
                expected_flow_ids = set(
                    '%s%x' % (qos._ROOT_QDISC_HANDLE, v.tag)
                    for v in (vlan1, vlan2)
                )
                self.assertEqual(
                    current_tagged_filters_flow_id, expected_flow_ids
                )

    @stresstest
    @requires_iperf3
    @requires_tc
    def test_iperf_upper_limit(self):
        # Upper limit is not an accurate measure. This is because it converges
        # over time and depends on current machine hardware (CPU).
        # Hence, it is hard to make hard assertions on it. The test should run
        # at least 60 seconds (the longer the better) and the user should
        # inspect the computed average rate and optionally the additional
        # traffic data that was collected in client.out in order to be
        # convinced QOS is working properly.
        limit_kbps = 1000  # 1 Mbps (in kbps)
        server_ip = '192.0.2.1'
        client_ip = '192.0.2.10'
        qos_out = {'ul': {'m2': limit_kbps}, 'ls': {'m2': limit_kbps}}
        # using a network namespace is essential since otherwise the kernel
        # short-circuits the traffic and bypasses the veth devices and the
        # classfull qdisc.
        with network_namespace(
            'server_ns'
        ) as ns, bridge_device() as bridge, veth_pair() as (
            server_peer,
            server_dev,
        ), veth_pair() as (
            client_dev,
            client_peer,
        ):
            linkSet(server_peer, ['up'])
            linkSet(client_peer, ['up'])
            # iperf server and its veth peer lie in a separate network
            # namespace
            link_set_netns(server_dev, ns)
            bridge.addIf(server_peer)
            bridge.addIf(client_peer)
            linkSet(client_dev, ['up'])
            netns_exec(ns, ['ip', 'link', 'set', 'dev', server_dev, 'up'])
            addrAdd(client_dev, client_ip, 24)
            netns_exec(
                ns,
                [
                    'ip',
                    '-4',
                    'addr',
                    'add',
                    'dev',
                    server_dev,
                    '%s/24' % server_ip,
                ],
            )
            qos.configure_outbound(qos_out, client_peer, None)
            with running(IperfServer(server_ip, network_ns=ns)):
                client = IperfClient(server_ip, client_ip, test_time=60)
                client.start()
                max_rate = max(
                    [
                        float(interval['streams'][0]['bits_per_second'])
                        // (2 ** 10)
                        for interval in client.out['intervals']
                    ]
                )
                self.assertTrue(0 < max_rate < limit_kbps * 1.5)

    def _analyse_qos_and_general_assertions(self):
        tc_classes = self._analyse_classes()
        tc_qdiscs = self._analyse_qdiscs()
        tc_filters = self._analyse_filters()
        self._assertions_on_classes(
            tc_classes.classes, tc_classes.default_class, tc_classes.root_class
        )
        self._assertions_on_qdiscs(
            tc_qdiscs.ingress_qdisc, tc_qdiscs.root_qdisc
        )
        self._assertions_on_filters(
            tc_filters.untagged_filters, tc_filters.tagged_filters
        )
        return tc_classes, tc_filters, tc_qdiscs

    def _analyse_classes(self):
        all_classes = list(tc.classes(self.device_name))
        root_class = self._root_class(all_classes)
        default_class = self._default_class(all_classes)
        all_classes.remove(root_class)
        all_classes.remove(default_class)
        return TcClasses(all_classes, default_class, root_class)

    def _analyse_qdiscs(self):
        all_qdiscs = list(tc.qdiscs(self.device_name))
        ingress_qdisc = self._ingress_qdisc(all_qdiscs)
        root_qdisc = self._root_qdisc(all_qdiscs)
        leaf_qdiscs = self._leaf_qdiscs(all_qdiscs)
        self.assertEqual(len(leaf_qdiscs) + 2, len(all_qdiscs))
        return TcQdiscs(leaf_qdiscs, ingress_qdisc, root_qdisc)

    def _analyse_filters(self):
        filters = list(tc._filters(self.device_name))
        untagged_filters = self._untagged_filters(filters)
        tagged_filters = self._tagged_filters(filters)
        return TcFilters(untagged_filters, tagged_filters)

    def _assertions_on_classes(self, all_classes, default_class, root_class):
        self.assertTrue(
            all(
                cls.get('kind') == qos._SHAPING_QDISC_KIND
                for cls in all_classes
            ),
            str(all_classes),
        )

        self._assertions_on_root_class(root_class)

        self._assertions_on_default_class(default_class)

        if not all_classes:  # only a default class
            self._assert_upper_limit(default_class)
        else:
            for cls in all_classes:
                self._assert_upper_limit(cls)

    def _assertions_on_qdiscs(self, ingress_qdisc, root_qdisc):
        self.assertEqual(root_qdisc['kind'], qos._SHAPING_QDISC_KIND)
        self._assert_root_handle(root_qdisc)
        self.assertEqual(ingress_qdisc['handle'], tc.QDISC_INGRESS)

    def _assertions_on_filters(self, untagged_filters, tagged_filters):
        self.assertTrue(all(f['protocol'] == 'all' for f in tagged_filters))
        self._assert_parent_handle(
            tagged_filters + untagged_filters, qos._ROOT_QDISC_HANDLE
        )
        self.assertEqual(len(untagged_filters), 1, msg=untagged_filters)
        self.assertEqual(untagged_filters[0]['protocol'], 'all')

    def _assert_upper_limit(self, default_class):
        self.assertEqual(
            default_class[qos._SHAPING_QDISC_KIND]['ul']['m2'],
            HOST_QOS_OUTBOUND['ul']['m2'],
        )

    def _assertions_on_default_class(self, default_class):
        self._assert_parent_handle([default_class], qos._ROOT_QDISC_HANDLE)
        self.assertEqual(default_class['leaf'], DEFAULT_CLASSID + ':')
        self.assertEqual(
            default_class[qos._SHAPING_QDISC_KIND]['ls'],
            HOST_QOS_OUTBOUND['ls'],
        )

    def _assertions_on_root_class(self, root_class):
        self.assertIsNotNone(root_class)
        self._assert_root_handle(root_class)

    def _assert_root_handle(self, entity):
        self.assertEqual(entity['handle'], qos._ROOT_QDISC_HANDLE)

    def _assert_parent(self, entities, parent):
        self.assertTrue(all(e['parent'] == parent['handle'] for e in entities))

    def _assert_parent_handle(self, entities, parent_handle):
        self.assertTrue(all(e['parent'] == parent_handle for e in entities))

    def _root_class(self, classes):
        return _find_entity(lambda c: c.get('root'), classes)

    def _default_class(self, classes):
        default_cls_handle = qos._ROOT_QDISC_HANDLE + DEFAULT_CLASSID
        return _find_entity(
            lambda c: c['handle'] == default_cls_handle, classes
        )

    def _ingress_qdisc(self, qdiscs):
        return _find_entity(lambda q: q['kind'] == 'ingress', qdiscs)

    def _root_qdisc(self, qdiscs):
        return _find_entity(lambda q: q.get('root'), qdiscs)

    def _leaf_qdiscs(self, qdiscs):
        return [
            qdisc for qdisc in qdiscs if qdisc['kind'] == qos._FAIR_QDISC_KIND
        ]

    def _untagged_filters(self, filters):
        predicate = lambda f: f.get('u32', {}).get('match', {}) == {
            'mask': 0,
            'value': 0,
            'offset': 0,
        }
        return list(f for f in filters if predicate(f))

    def _tagged_filters(self, filters):
        def tagged(f):
            return f.get('basic', {}).get('object') == 'vlan'

        return list(f for f in filters if tagged(f))

    def _vlan_qdisc(self, qdiscs, vlan_tag):
        handle = '%x:' % vlan_tag
        return _find_entity(lambda q: q['handle'] == handle, qdiscs)

    def _vlan_class(self, classes, vlan_tag):
        handle = qos._ROOT_QDISC_HANDLE + '%x' % vlan_tag
        return _find_entity(lambda c: c['handle'] == handle, classes)

    def _non_vlan_qdisc(self, qdiscs):
        handle = DEFAULT_CLASSID + ':'
        return _find_entity(lambda q: q['handle'] == handle, qdiscs)


def _find_entity(predicate, entities):
    for ent in entities:
        if predicate(ent):
            return ent
