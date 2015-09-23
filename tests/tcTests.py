#
# Copyright 2012 Roman Fenkhuber.
# Copyright 2012-2014 Red Hat, Inc.
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

import time
import os
import six
import sys

from binascii import unhexlify
from itertools import izip_longest
from subprocess import Popen, PIPE

from testlib import VdsmTestCase as TestCaseBase
from testValidation import ValidateRunningAsRoot
from nettestlib import Bridge, Tap, requires_tc, requires_tun

from vdsm.constants import EXT_TC
from network import tc


class TestQdisc(TestCaseBase):

    @ValidateRunningAsRoot
    @requires_tc
    def setUp(self):
        self._bridge = Bridge()
        self._bridge.addDevice()

    def tearDown(self):
        self._bridge.delDevice()

    def _showQdisc(self):
        popen = Popen([EXT_TC, "qdisc", "show", "dev", self._bridge.devName],
                      stdout=PIPE)
        return popen.stdout.read()

    def _addIngress(self):
        tc._qdisc_replace_ingress(self._bridge.devName)
        self.assertIn("qdisc ingress", self._showQdisc())

    def testToggleIngress(self):
        self._addIngress()
        tc._qdisc_del(self._bridge.devName, 'ingress')
        self.assertNotIn("qdisc ingress", self._showQdisc())

    def testQdiscsOfDevice(self):
        self._addIngress()
        self.assertEquals(("ffff:", ),
                          tuple(tc._qdiscs_of_device(self._bridge.devName)))

    def testReplacePrio(self):
        self._addIngress()
        tc.qdisc.replace(self._bridge.devName, 'prio', parent=None)
        self.assertIn("root", self._showQdisc())

    def testException(self):
        self.assertRaises(tc.TrafficControlException, tc._qdisc_del,
                          "__nosuchiface__", 'ingress')


class TestFilters(TestCaseBase):
    def test_filter_objs(self):
        dirName = os.path.dirname(os.path.realpath(__file__))
        path = os.path.join(dirName, "tc_filter_show.out")
        with open(path) as f:
            out = f.read()
        PARSED_FILTERS = (
            tc.Filter(prio=49149, handle='803::800',
                      actions=[tc.MirredAction(target='tap1')]),
            tc.Filter(prio=49150, handle='802::800',
                      actions=[tc.MirredAction(target='tap2')]),
            tc.Filter(prio=49152, handle='800::800',
                      actions=[tc.MirredAction(target='target'),
                               tc.MirredAction(target='target2')]))
        self.assertEqual(tuple(tc.filters('bridge', 'parent', out=out)),
                         PARSED_FILTERS)

    def test_filters(self):
        filters = (
            {'protocol': 'all', 'pref': 49149, 'kind': 'u32', 'u32': {}},
            {'protocol': 'all', 'pref': 49149, 'kind': 'u32', 'u32': {
                'fh': '803:', 'ht_divisor': 1}},
            {'protocol': 'all', 'pref': 49149, 'kind': 'u32', 'u32': {
                'fh': '803::800', 'order': 2048, 'key_ht': 0x803,
                'key_bkt': 0x0, 'terminal': True, 'match': {
                    'value': 0x0, 'mask': 0x0, 'offset': 0x0},
                'actions': [
                    {'order': 1, 'kind': 'mirred', 'action': 'egress_mirror',
                     'target': 'tap1', 'op': 'pipe', 'index': 18, 'ref': 1,
                     'bind': 1}]}},

            {'protocol': 'all', 'pref': 49150, 'kind': 'u32', 'u32': {}},
            {'protocol': 'all', 'pref': 49150, 'kind': 'u32', 'u32': {
                'fh': '802:', 'ht_divisor': 1}},
            {'protocol': 'all', 'pref': 49150, 'kind': 'u32', 'u32': {
                'fh': '802::800', 'order': 2048, 'key_ht': 0x802,
                'key_bkt': 0x0, 'terminal': True, 'match': {
                    'value': 0x0, 'mask': 0x0, 'offset': 0x0},
                'actions': [
                    {'order': 33, 'kind': 'mirred', 'action': 'egress_mirror',
                     'target': 'tap2', 'op': 'pipe', 'index': 17, 'ref': 1,
                     'bind': 1}]}},

            {'protocol': 'all', 'pref': 49152, 'kind': 'u32', 'u32': {}},
            {'protocol': 'all', 'pref': 49152, 'kind': 'u32', 'u32': {
                'fh': '800:', 'ht_divisor': 1}},
            {'protocol': 'all', 'pref': 49152, 'kind': 'u32', 'u32': {
                'fh': '800::800', 'order': 2048, 'key_ht': 0x800,
                'key_bkt': 0x0, 'terminal': True, 'match': {
                    'value': 0x0, 'mask': 0x0, 'offset': 0x0},
                'actions': [
                    {'order': 1, 'kind': 'mirred', 'action': 'egress_mirror',
                     'target': 'target', 'op': 'pipe', 'index': 60, 'ref': 1,
                     'bind': 1},
                    {'order': 2, 'kind': 'mirred', 'action': 'egress_mirror',
                     'target': 'target2', 'op': 'pipe', 'index': 61, 'ref': 1,
                     'bind': 1},
                ]}},
        )
        dirName = os.path.dirname(os.path.realpath(__file__))
        path = os.path.join(dirName, "tc_filter_show.out")
        with open(path) as tc_filter_show:
            data = tc_filter_show.read()

        for parsed, correct in izip_longest(tc._filters(None, out=data),
                                            filters):
            self.assertEqual(parsed, correct)

    def test_qdiscs(self):
        data = '\n'.join((
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
        ))
        qdiscs = (
            {'kind': 'hfsc', 'root': True, 'handle': '1:', 'refcnt': 2,
             'hfsc': {'default': 0x5000}},
            {'kind': 'sfq', 'handle': '10:', 'parent': '1:10',
             'sfq': {'limit': 127, 'quantum': 1514}},
            {'kind': 'sfq', 'handle': '20:', 'parent': '1:20',
             'sfq': {'limit': 127, 'quantum': 1514}},
            {'kind': 'sfq', 'handle': '30:', 'parent': '1:30',
             'sfq': {'limit': 127, 'quantum': 30 * 1024, 'perturb': 3}},
            {'kind': 'sfq', 'handle': '40:', 'parent': '1:40',
             'sfq': {'limit': 127, 'quantum': 20 * 1024 ** 2, 'perturb': 5}},
            {'kind': 'ingress', 'handle': 'ffff:', 'parent': 'ffff:fff1'},
            {'kind': 'mq', 'handle': '0:', 'dev': 'wlp3s0', 'root': True},
            {'kind': 'ingress', 'handle': 'ffff:', 'dev': 'vdsmtest-Z2TMO',
             'parent': 'ffff:fff1'},
            {'kind': 'pfifo_fast', 'handle': '0:', 'dev': 'em1',
             'root': True, 'refcnt': 2, 'pfifo_fast': {
                 'bands': 3,
                 'priomap': [1, 2, 2, 2, 1, 2, 0, 0, 1, 1, 1, 1, 1, 1, 1]}},
            {'kind': 'pfifo_fast', 'handle': '0:', 'dev': 'wlp3s0',
             'parent': ':1', 'pfifo_fast': {
                 'bands': 3,
                 'priomap': [1, 2, 2, 2, 1, 2, 0, 0, 1, 1, 1, 1, 1, 1, 1]}},
            {'kind': 'fq_codel', 'handle': '801e:', 'root': True, 'refcnt': 2,
             'fq_codel': {'limit': 132, 'flows': 15, 'quantum': 400,
                          'target': 5000.0, 'interval': 150000.0,
                          'ecn': True}},
        )
        for parsed, correct in izip_longest(tc._qdiscs(None, out=data),
                                            qdiscs):
            self.assertEqual(parsed, correct)

    def test_classes(self):
        cmd_line_ls_10 = 3200
        cmd_line_ls_m1_20 = 6400
        cmd_line_ls_d_20 = 152
        cmd_line_ls_m2_20 = 3200
        cmd_line_ls_30 = 3500
        cmd_line_ls_m2_5000 = 40000
        data = '\n'.join((
            'class hfsc 1: root',
            'class hfsc 1:10 parent 1: leaf 10: sc m1 0bit d 0us '
            'm2 {0}Kbit'.format(cmd_line_ls_10),  # end of previous line
            'class hfsc 1:20 parent 1: leaf 20: ls m1 {0}Kibit d {1}us '
            'm2 {2}Kbit ul m1 0bit d 0us m2 30000Kbit'.format(
                cmd_line_ls_m1_20, cmd_line_ls_d_20, cmd_line_ls_m2_20),
            'class hfsc 1:30 parent 1: leaf 40: sc m1 0bit d 0us '
            'm2 {0}bit'.format(cmd_line_ls_30),  # end of previous line
            'class hfsc 1:5000 parent 1: leaf 5000: ls m1 0bit d 0us '
            'm2 {0}Kbit'.format(cmd_line_ls_m2_5000),  # end of previous line
        ))
        reported_ls_10 = cmd_line_ls_10 * 1000 / 8
        reported_ls_m1_20 = cmd_line_ls_m1_20 * 1024 / 8
        reported_ls_d_20 = cmd_line_ls_d_20 / 8
        reported_ls_m2_20 = cmd_line_ls_m2_20 * 1000 / 8
        reported_ls_30 = cmd_line_ls_30 / 8
        reported_ls_5000 = cmd_line_ls_m2_5000 * 1000 / 8
        classes = (
            {'kind': 'hfsc', 'root': True, 'handle': '1:'},
            {'kind': 'hfsc', 'handle': '1:10', 'parent': '1:', 'leaf': '10:',
             'hfsc': {'ls': {'m1': 0, 'd': 0, 'm2': reported_ls_10},
                      'rt': {'m1': 0, 'd': 0, 'm2': reported_ls_10}}},
            {'kind': 'hfsc', 'handle': '1:20', 'parent': '1:', 'leaf': '20:',
             'hfsc': {'ls': {'m1': reported_ls_m1_20, 'd': reported_ls_d_20,
                             'm2': reported_ls_m2_20},
                      'ul': {'m1': 0, 'd': 0, 'm2': 30000 * 1000}}},
            {'kind': 'hfsc', 'handle': '1:30', 'parent': '1:', 'leaf': '40:',
             'hfsc': {'ls': {'m1': 0, 'd': 0, 'm2': reported_ls_30},
                      'rt': {'m1': 0, 'd': 0, 'm2': reported_ls_30}}},
            {'kind': 'hfsc', 'handle': '1:5000', 'parent': '1:',
             'leaf': '5000:', 'hfsc': {'ls': {'m1': 0, 'd': 0,
                                              'm2': reported_ls_5000}}},
        )
        for parsed, correct in izip_longest(tc.classes(None, out=data),
                                            classes):
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
        '001cc0d044dc''00215c4d4275''0800'  # Ethernet
        '45000021000100004001f953''c0a80034''c0a80003'  # IP
        '080028750000000'  # ICMP
        '00123456789')  # Payload

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
        self._devices = [self._tap0, self._tap1, self._tap2,
                         self._bridge0, self._bridge1, self._bridge2]
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

    def testMirroring(self):
        tc.setPortMirroring(self._bridge0.devName, self._bridge1.devName)
        self.assertTrue(self._sendPing(), "Bridge received no mirrored ping "
                        "requests.")

        tc.unsetPortMirroring(self._bridge0.devName, self._bridge1.devName)
        self.assertFalse(self._sendPing(), "Bridge received mirrored ping "
                         "requests, but mirroring is unset.")

    def testMirroringWithDistraction(self):
        "setting another mirror action should not obstract the first one"
        tc.setPortMirroring(self._bridge0.devName, self._bridge2.devName)
        self.testMirroring()
        tc.unsetPortMirroring(self._bridge0.devName, self._bridge2.devName)
