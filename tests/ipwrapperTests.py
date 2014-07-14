# Copyright 2013 Red Hat, Inc.
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
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA
# 02110-1301  USA
#
# Refer to the README and COPYING files for full details of the license
#
from functools import partial

from testValidation import ValidateRunningAsRoot
from vdsm import ipwrapper
from vdsm.ipwrapper import Link
from vdsm.ipwrapper import LinkType
from vdsm.ipwrapper import Monitor
from vdsm.ipwrapper import MonitorEvent
from vdsm.ipwrapper import MonitorError
from vdsm.ipwrapper import Route
from vdsm.ipwrapper import Rule
from monkeypatch import MonkeyPatch
import tcTests

from testlib import VdsmTestCase as TestCaseBase


def _fakeTypeDetection(cls, devname):
    pass


class TestIpwrapper(TestCaseBase):
    @MonkeyPatch(Link, '_detectType', partial(_fakeTypeDetection, Link))
    def testLinkFromF20Text(self):
        lines = ('1: lo: <LOOPBACK,UP,LOWER_UP> mtu 65536 qdisc noqueue state '
                 'UNKNOWN mode DEFAULT group default \\    link/loopback '
                 '00:00:00:00:00:00 brd 00:00:00:00:00:00 promiscuity 0 ',
                 '2: em1: <BROADCAST,MULTICAST,UP,LOWER_UP> mtu 1500 qdisc '
                 'pfifo_fast state UP mode DEFAULT group default qlen 1000\\  '
                 '  link/ether f0:de:f1:da:aa:e7 brd ff:ff:ff:ff:ff:ff '
                 'promiscuity 0 ',
                 '3: wlp3s0: <BROADCAST,MULTICAST,UP,LOWER_UP> mtu 1500 qdisc '
                 'mq state UP mode DORMANT group default qlen 1000\\    '
                 'link/ether 24:77:03:5b:a7:70 brd ff:ff:ff:ff:ff:ff '
                 'promiscuity 0 ',
                 '9: ;vdsmdummy;: <BROADCAST,MULTICAST> mtu 1500 qdisc noop '
                 'state DOWN mode DEFAULT group default \\    link/ether '
                 '92:91:9b:9a:41:b9 brd ff:ff:ff:ff:ff:ff promiscuity 0 \\    '
                 'bridge ',
                 '128: bond0: <NO-CARRIER,BROADCAST,MULTICAST,MASTER,UP> mtu '
                 '1500 qdisc noqueue state DOWN mode DEFAULT group default \\ '
                 '   link/ether 56:62:c2:71:75:c0 brd ff:ff:ff:ff:ff:ff '
                 'promiscuity 0 \\    bond ',
                 '148: dummy_999: <BROADCAST,NOARP,SLAVE,UP,LOWER_UP> mtu 1500'
                 ' qdisc noqueue master bond777 state UNKNOWN mode DEFAULT '
                 'group default \\    link/ether 1a:38:3e:28:c0:32 brd '
                 'ff:ff:ff:ff:ff:ff promiscuity 1 \\    dummy ',
                 '149: dummy_888: <BROADCAST,NOARP,SLAVE,UP,LOWER_UP> mtu 1500'
                 ' qdisc noqueue master bond777 state UNKNOWN mode DEFAULT '
                 'group default \\    link/ether 1a:38:3e:28:c0:32 brd '
                 'ff:ff:ff:ff:ff:ff promiscuity 1 \\    dummy ',
                 '150: bond777: <BROADCAST,MULTICAST,MASTER,UP,LOWER_UP> mtu '
                 '1500 qdisc noqueue state UP mode DEFAULT group default \\   '
                 ' link/ether 1a:38:3e:28:c0:32 brd ff:ff:ff:ff:ff:ff '
                 'promiscuity 1 \\    bond ',
                 '151: bond777.555@bond777: <BROADCAST,MULTICAST,UP,LOWER_UP> '
                 'mtu 1500 qdisc noqueue master foo state UP mode DEFAULT '
                 'group default \\    link/ether 1a:38:3e:28:c0:32 brd '
                 'ff:ff:ff:ff:ff:ff promiscuity 1 \\    vlan protocol 802.1Q '
                 'id 555 <REORDER_HDR> ',
                 '152: foo: <BROADCAST,MULTICAST,UP,LOWER_UP> mtu 1500 qdisc '
                 'noqueue state UP mode DEFAULT group default \\    link/ether'
                 ' 1a:38:3e:28:c0:32 brd ff:ff:ff:ff:ff:ff promiscuity 0 \\   '
                 ' bridge ',
                 '153: assaf@em1: <BROADCAST,MULTICAST> mtu 1500 qdisc noop '
                 'state DOWN mode DEFAULT group default \\    link/ether '
                 '00:43:af:3e:e0:a1 brd ff:ff:ff:ff:ff:ff promiscuity 0 \\    '
                 'macvlan  mode vepa ')
        devices = [Link.fromText(line) for line in lines]
        self.assertEqual(len(devices), len(lines))
        self.assertEqual(devices[-3].vlanid, 555)
        self.assertEqual(devices[1].address, 'f0:de:f1:da:aa:e7')
        self.assertEqual(devices[-5].type, LinkType.DUMMY)
        self.assertEqual(devices[-3].master, devices[-2].name)

    @MonkeyPatch(Link, '_detectType', partial(_fakeTypeDetection, Link))
    def testLinkFromRHEL64Text(self):
        lines = (
            '1: lo: <LOOPBACK,UP,LOWER_UP> mtu 16436 qdisc noqueue state '
            'UNKNOWN \\    link/loopback 00:00:00:00:00:00 brd '
            '00:00:00:00:00:00',
            '2: p1p1: <BROADCAST,MULTICAST> mtu 1500 qdisc noop state DOWN '
            'qlen 1000\\    link/ether 00:10:18:e1:6c:f0 brd '
            'ff:ff:ff:ff:ff:ff',
            '3: p1p2: <BROADCAST,MULTICAST> mtu 1500 qdisc noop state DOWN '
            'qlen 1000\\    link/ether 00:10:18:e1:6c:f2 brd '
            'ff:ff:ff:ff:ff:ff',
            '4: p1p3: <NO-CARRIER,BROADCAST,MULTICAST,UP> mtu 1500 qdisc mq '
            'state DOWN qlen 1000\\    link/ether 00:10:18:e1:6c:f4 brd '
            'ff:ff:ff:ff:ff:ff',
            '5: p1p4: <BROADCAST,MULTICAST> mtu 1500 qdisc mq state DOWN qlen '
            '1000\\    link/ether 00:10:18:e1:6c:f6 brd ff:ff:ff:ff:ff:ff',
            '6: em1: <BROADCAST,MULTICAST,UP,LOWER_UP> mtu 1500 qdisc mq '
            'state UP qlen 1000\\    link/ether d0:67:e5:f0:76:59 brd '
            'ff:ff:ff:ff:ff:ff',
            '7: em2: <BROADCAST,MULTICAST> mtu 1500 qdisc mq state DOWN qlen '
            '1000\\    link/ether d0:67:e5:f0:76:5a brd ff:ff:ff:ff:ff:ff',
            '9: ;vdsmdummy;: <BROADCAST,MULTICAST> mtu 1500 qdisc noop state '
            'DOWN \\    link/ether 52:ca:1c:18:36:98 brd ff:ff:ff:ff:ff:ff',
            '10: bond0: <BROADCAST,MULTICAST,MASTER> mtu 1500 qdisc noqueue '
            'state DOWN \\    link/ether 00:00:00:00:00:00 brd '
            'ff:ff:ff:ff:ff:ff',
            '11: bond4: <BROADCAST,MULTICAST,MASTER> mtu 1500 qdisc noqueue '
            'state DOWN \\    link/ether 00:00:00:00:00:00 brd '
            'ff:ff:ff:ff:ff:ff',
            '12: bond1: <BROADCAST,MULTICAST,MASTER> mtu 1500 qdisc noop '
            'state DOWN \\    link/ether 00:00:00:00:00:00 brd '
            'ff:ff:ff:ff:ff:ff',
            '13: bond2: <BROADCAST,MULTICAST,MASTER> mtu 1500 qdisc noop '
            'state DOWN \\    link/ether 00:00:00:00:00:00 brd '
            'ff:ff:ff:ff:ff:ff',
            '14: bond3: <BROADCAST,MULTICAST,MASTER> mtu 1500 qdisc noop '
            'state DOWN \\    link/ether 00:00:00:00:00:00 brd '
            'ff:ff:ff:ff:ff:ff',
            '15: dummy0: <BROADCAST,NOARP> mtu 1500 qdisc noop state DOWN '
            '\\    link/ether 1e:dd:89:60:6f:1a brd ff:ff:ff:ff:ff:ff\\    '
            'dummy ',
            '417: p1p3.13@p1p3: <NO-CARRIER,BROADCAST,MULTICAST,UP> mtu 1500 '
            'qdisc noqueue state LOWERLAYERDOWN \\    link/ether '
            '00:10:18:e1:6c:f4 brd ff:ff:ff:ff:ff:ff\\    vlan id 13 '
            '<REORDER_HDR> ',
            '418: bar: <BROADCAST,MULTICAST,UP,LOWER_UP> mtu 1500 qdisc '
            'noqueue state UNKNOWN \\    link/ether 00:10:18:e1:6c:f4 brd '
            'ff:ff:ff:ff:ff:ff')
        devices = [Link.fromText(line) for line in lines]
        self.assertEqual(len(devices), len(lines))
        self.assertEqual(devices[-2].vlanid, 13)
        self.assertEqual(devices[1].address, '00:10:18:e1:6c:f0')
        self.assertEqual(devices[-3].type, LinkType.DUMMY)
        self.assertEqual(devices[-2].name, 'p1p3.13')

    def testRouteFromText(self):
        _getRouteAttrs = lambda x: (x.network, x.via, x.device, x.table)
        good_routes = {
            'default via 192.168.99.254 dev eth0':
            ('0.0.0.0/0', '192.168.99.254', 'eth0', None),
            'default via 192.168.99.254 dev eth0 table foo':
            ('0.0.0.0/0', '192.168.99.254', 'eth0', 'foo'),
            '200.100.50.0/16 via 11.11.11.11 dev eth2 table foo':
            ('200.100.50.0/16', '11.11.11.11', 'eth2', 'foo'),
            'local 127.0.0.1 dev lo  src 127.0.0.1':
            ('local', None, 'lo', None)}

        for text, attributes in good_routes.iteritems():
            route = Route.fromText(text)
            self.assertEqual(_getRouteAttrs(route), attributes)

        bad_routes = \
            ['default via 192.168.99.257 dev eth0 table foo',  # Misformed via
             '200.100.50.0/16 dev eth2 table foo extra',  # Key without value
             '288.1.2.9/43 via 1.1.9.4 dev em1 table foo',  # Misformed network
             '200.100.50.0/16 via 192.168.99.254 table foo',  # No device
             'local dev eth0 table bar']  # local with no address
        for text in bad_routes:
            self.assertRaises(ValueError, Route.fromText, text)

    def testRuleFromText(self):
        _getRuleAttrs = lambda x: (x.table, x.source, x.destination,
                                   x.srcDevice, x.detached)
        good_rules = {
            '1:    from all lookup main':
            ('main', None, None, None, False),
            '2:    from 10.0.0.0/8 to 20.0.0.0/8 lookup table_100':
            ('table_100', '10.0.0.0/8', '20.0.0.0/8', None, False),
            '3:    from all to 8.8.8.8 lookup table_200':
            ('table_200', None, '8.8.8.8', None, False),
            '4:    from all to 5.0.0.0/8 iif dummy0 [detached] lookup 500':
            ('500', None, '5.0.0.0/8', 'dummy0', True),
            '5:    from all to 5.0.0.0/8 dev dummy0 lookup 500':
            ('500', None, '5.0.0.0/8', 'dummy0', False)}
        for text, attributes in good_rules.iteritems():
            rule = Rule.fromText(text)
            self.assertEqual(_getRuleAttrs(rule), attributes)

        bad_rules = ['32766:    from all lookup main foo',
                     '2766:    lookup main',
                     '276:    from 8.8.8.8'
                     '32:    from 10.0.0.0/8 to 264.0.0.0/8 lookup table_100']
        for text in bad_rules:
            self.assertRaises(ValueError, Rule.fromText, text)


class TestMonitor(TestCaseBase):
    def testWrongMonitorUsage(self):
        mon = Monitor()
        with self.assertRaises(MonitorError):
            for event in mon:
                pass

    def testMonitorEvents(self):
        devs = ({'index': '273',
                 'reportedName': 'bond0', 'name': 'bond0',
                 'flags': frozenset(['BROADCAST', 'MULTICAST', 'MASTER']),
                 'attrs': 'mtu 1500 qdisc noqueue',
                 'state': 'DOWN',
                 'address': '33:44:55:66:77:88', 'brd': 'ff:ff:ff:ff:ff:ff'},
                {'index': '4',
                 'reportedName': 'wlp3s0', 'name': 'wlp3s0',
                 'flags': frozenset(['BROADCAST', 'MULTICAST', 'UP',
                                     'LOWER_UP']),
                 'address': ''},
                {'index': '417',
                 'reportedName': 'p1p3.13@p1p3', 'name': 'p1p3.13',
                 'flags': frozenset(['NO-CARRIER', 'BROADCAST', 'MULTICAST',
                                     'UP']),
                 'attrs': 'mtu 1500 qdisc noqueue',
                 'state': 'LOWERLAYERDOWN',
                 'address': '00:10:18:e1:6c:f4',
                 'brd': 'ff:ff:ff:ff:ff:ff'},
                {'index': '418',
                 'reportedName': 'foo', 'name': 'foo',
                 'flags': frozenset(['BROADCAST', 'MULTICAST']),
                 'attrs': 'mtu 1500 qdisc noop',
                 'state': 'DOWN',
                 'extraAttrs': 'group default',
                 'address': 'ba:2c:7b:68:b8:77',
                 'brd': 'ff:ff:ff:ff:ff:ff',
                 'deleted': True})

        def entry(index, reportedName, flags, address, attrs=None,
                  state=None, extraAttrs=None, brd=None, deleted=False,
                  **kwargs):
            elements = []
            if deleted:
                elements.append(Monitor._DELETED_TEXT)
            elements += [index + ':', reportedName + ':',
                         '<' + ','.join(flags) + '>']
            if attrs is not None:
                elements.append(attrs)
            if state is not None:
                elements.append('state ' + state)
            if extraAttrs is not None:
                elements.append(extraAttrs)
            elements.append('\\   ')
            elements.append('link/ether ' + address)
            if brd is not None:
                elements.append('brd ' + brd)
            return ' '.join(elements)

        data = [entry(**dev) for dev in devs]
        events = [MonitorEvent(
            dev['index'], dev['name'], dev['flags'],
            Monitor.LINK_STATE_DELETED if dev.get('deleted') else
            dev.get('state', None)) for dev in devs]
        self.assertEqual(Monitor._parse('\n'.join(data)), events)

    @ValidateRunningAsRoot
    def testMonitorIteration(self):
        bridge = tcTests._Bridge()
        tcTests._checkDependencies()
        mon = Monitor()
        mon.start()
        iterator = iter(mon)

        bridge.addDevice()  # Generate an event to avoid blocking
        iterator.next()

        bridge.delDevice()
        iterator.next()  # Generate an event to avoid blocking

        # Stop the monitor and check that eventually StopIteration is raised.
        # There might be other system link events so we loop to exhaust them.
        mon.stop()
        with self.assertRaises(StopIteration):
            while True:
                iterator.next()


class TestLinks(TestCaseBase):
    _bridge = tcTests._Bridge()

    @ValidateRunningAsRoot
    def setUp(self):
        tcTests._checkDependencies()
        self._bridge.addDevice()

    def tearDown(self):
        self._bridge.delDevice()

    def testGetLink(self):
        link = ipwrapper.getLink(self._bridge.devName)
        self.assertTrue(link.isBRIDGE)
        self.assertEqual(link.master, None)
        self.assertEqual(link.name, self._bridge.devName)


class TestDrvinfo(TestCaseBase):
    _bridge = tcTests._Bridge()

    @ValidateRunningAsRoot
    def setUp(self):
        tcTests._checkDependencies()
        self._bridge.addDevice()

    def tearDown(self):
        self._bridge.delDevice()

    def testBridgeEthtoolDrvinfo(self):
        self.assertEqual(ipwrapper.drv_name(self._bridge.devName),
                         ipwrapper.LinkType.BRIDGE)
