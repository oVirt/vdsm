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

import random
import time
import string
import os
import signal

from multiprocessing import Process
from binascii import unhexlify
from itertools import izip_longest
from subprocess import Popen, check_call, PIPE
import fcntl
import struct
import ethtool

from testlib import VdsmTestCase as TestCaseBase
from testValidation import ValidateRunningAsRoot

from vdsm.constants import EXT_BRCTL, EXT_TC
from nose.plugins.skip import SkipTest

from network import tc
import platform

EXT_IP = "/sbin/ip"


class _Interface():
    def __init__(self, prefix='vdsmtest-'):
        self.devName = self._generateRandomName(prefix)

    def _generateRandomName(self, prefix):
        char_set = string.ascii_letters + string.digits
        return prefix + ''.join(random.sample(char_set, 5))

    def _ifUp(self):
        check_call([EXT_IP, "link", "set", self.devName, "up"])

    def _ifDown(self):
        check_call([EXT_IP, "link", "set", self.devName, "down"])


class _Bridge(_Interface):

    def addDevice(self):
        check_call([EXT_BRCTL, 'addbr', self.devName])
        # learning interval is different on different kernels, so set it
        # explicit for 2.x kernels
        if os.uname()[2].startswith("2"):
            check_call([EXT_BRCTL, 'setfd', self.devName, '0'])
            check_call([EXT_BRCTL, 'setageing', self.devName, '0'])
        self._ifUp()

    def delDevice(self):
        self._ifDown()
        check_call([EXT_BRCTL, 'delbr', self.devName])

    def addIf(self, dev):
        check_call([EXT_BRCTL, 'addif', self.devName, dev])


def _listenOnDevice(fd, icmp):
    while True:
        packet = os.read(fd, 2048)
        # check if it is an IP packet
        if (packet[12:14] == chr(0x08) + chr(0x00)):
            if packet == icmp:
                return


class _Tap(_Interface):

    _IFF_TAP = 0x0002
    _IFF_NO_PI = 0x1000
    arch = platform.machine()
    if arch == 'x86_64':
        _TUNSETIFF = 0x400454ca
    elif arch == 'ppc64':
        _TUNSETIFF = 0x800454ca
    else:
        raise SkipTest("Unsupported Architecture %s" % arch)

    _deviceListener = None

    def addDevice(self):
        self._cloneDevice = open('/dev/net/tun', 'r+b')
        ifr = struct.pack('16sH', self.devName, self._IFF_TAP |
                          self._IFF_NO_PI)
        fcntl.ioctl(self._cloneDevice, self._TUNSETIFF, ifr)
        self._ifUp()

    def delDevice(self):
        self._ifDown()
        self._cloneDevice.close()

    def startListener(self, icmp):
        self._deviceListener = Process(target=_listenOnDevice,
                                       args=(self._cloneDevice.fileno(), icmp))
        self._deviceListener.start()

    def isListenerAlive(self):
        if self._deviceListener:
            return self._deviceListener.is_alive()
        else:
            return False

    def stopListener(self):
        if self._deviceListener:
            os.kill(self._deviceListener.pid, signal.SIGKILL)
            self._deviceListener.join()

    def writeToDevice(self, icmp):
        os.write(self._cloneDevice.fileno(), icmp)


def _checkDependencies():

    dev = _Bridge()
    try:
        dev.addDevice()
    except:
        raise SkipTest("'brctl' has failed. Do you have bridge-utils "
                       "installed?")

    null = open("/dev/null", "a")
    try:
        check_call([EXT_TC, 'qdisc', 'add', 'dev', dev.devName, 'ingress'],
                   stderr=null)
    except:
        raise SkipTest("'tc' has failed. Do you have Traffic Control kernel "
                       "modules installed?")
    finally:
        null.close()
        dev.delDevice()


class TestQdisc(TestCaseBase):
    _bridge = _Bridge()

    @ValidateRunningAsRoot
    def setUp(self):
        _checkDependencies()
        self._bridge.addDevice()

    def tearDown(self):
        self._bridge.delDevice()

    def _showQdisc(self):
        popen = Popen([EXT_TC, "qdisc", "show", "dev", self._bridge.devName],
                      stdout=PIPE)
        return popen.stdout.read()

    def _addIngress(self):
        tc.qdisc_replace_ingress(self._bridge.devName)
        self.assertIn("qdisc ingress", self._showQdisc())

    def testToggleIngress(self):
        self._addIngress()
        tc.qdisc_del(self._bridge.devName, 'ingress')
        self.assertNotIn("qdisc ingress", self._showQdisc())

    def testQdiscsOfDevice(self):
        self._addIngress()
        self.assertEquals(("ffff:", ),
                          tuple(tc._qdiscs_of_device(self._bridge.devName)))

    def testReplacePrio(self):
        self._addIngress()
        tc.qdisc_replace_prio(self._bridge.devName)
        self.assertIn("root", self._showQdisc())

    def testTogglePromisc(self):
        tc.set_promisc(self._bridge.devName, True)
        self.assertTrue(ethtool.get_flags(self._bridge.devName) &
                        ethtool.IFF_PROMISC,
                        "Could not enable promiscuous mode.")

        tc.set_promisc(self._bridge.devName, False)
        self.assertFalse(ethtool.get_flags(self._bridge.devName) &
                         ethtool.IFF_PROMISC,
                         "Could not disable promiscuous mode.")

    def testException(self):
        self.assertRaises(tc.TrafficControlException, tc.qdisc_del,
                          self._bridge.devName + "A", 'ingress')


class TestFilters(TestCaseBase):
    def test_filter_objs(self):
        dirName = os.path.dirname(os.path.realpath(__file__))
        path = os.path.join(dirName, "tc_filter_show.out")
        out = file(path).read()
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
            {'protocol': 'ip', 'pref': 49149, 'kind': 'u32', 'u32': {}},
            {'protocol': 'ip', 'pref': 49149, 'kind': 'u32', 'u32': {
                'fh': '803:', 'ht_divisor': 1}},
            {'protocol': 'ip', 'pref': 49149, 'kind': 'u32', 'u32': {
                'fh': '803::800', 'order': 2048, 'key_ht': 0x803,
                'key_bkt': 0x0, 'terminal': True, 'match': {
                    'value': 0x0, 'mask': 0x0, 'offset': 0x0},
                'actions': [
                    {'order': 1, 'kind': 'mirred', 'action': 'egress_mirror',
                     'target': 'tap1', 'op': 'pipe', 'index': 18, 'ref': 1,
                     'bind': 1}]}},

            {'protocol': 'ip', 'pref': 49150, 'kind': 'u32', 'u32': {}},
            {'protocol': 'ip', 'pref': 49150, 'kind': 'u32', 'u32': {
                'fh': '802:', 'ht_divisor': 1}},
            {'protocol': 'ip', 'pref': 49150, 'kind': 'u32', 'u32': {
                'fh': '802::800', 'order': 2048, 'key_ht': 0x802,
                'key_bkt': 0x0, 'terminal': True, 'match': {
                    'value': 0x0, 'mask': 0x0, 'offset': 0x0},
                'actions': [
                    {'order': 33, 'kind': 'mirred', 'action': 'egress_mirror',
                     'target': 'tap2', 'op': 'pipe', 'index': 17, 'ref': 1,
                     'bind': 1}]}},

            {'protocol': 'ip', 'pref': 49152, 'kind': 'u32', 'u32': {}},
            {'protocol': 'ip', 'pref': 49152, 'kind': 'u32', 'u32': {
                'fh': '800:', 'ht_divisor': 1}},
            {'protocol': 'ip', 'pref': 49152, 'kind': 'u32', 'u32': {
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

    # just an echo request from 192.168.0.52 to 192.168.0.3
    _ICMP = unhexlify("001cc0d044dc00215c4d4275080045000054000040004001b921c0a"
                      "80034c0a800030800dd200c1400016b52085000000000d754050000"
                      "000000101112131415161718191a1b1c1d1e1f20212223242526272"
                      "8292a2b2c2d2e2f3031323334353637")

    _tap0 = _Tap()
    _tap1 = _Tap()
    _tap2 = _Tap()
    _bridge0 = _Bridge('src-')
    _bridge1 = _Bridge('target-')
    _bridge2 = _Bridge('target2-')

    @ValidateRunningAsRoot
    def setUp(self):
        _checkDependencies()
        self._tap0.addDevice()
        self._tap1.addDevice()
        self._tap2.addDevice()
        self._bridge0.addDevice()
        self._bridge1.addDevice()
        self._bridge2.addDevice()
        self._bridge0.addIf(self._tap0.devName)
        self._bridge1.addIf(self._tap1.devName)
        self._bridge2.addIf(self._tap2.devName)

    def tearDown(self):
        self._tap0.delDevice()
        self._tap1.delDevice()
        self._tap2.delDevice()
        self._bridge0.delDevice()
        self._bridge1.delDevice()
        self._bridge2.delDevice()

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
