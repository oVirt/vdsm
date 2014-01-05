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
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA
# 02110-1301  USA
#
# Refer to the README and COPYING files for full details of the license
#
import os
from shutil import rmtree
import tempfile
from xml.dom import minidom

import ethtool

from vdsm import netinfo

from monkeypatch import MonkeyPatch, MonkeyPatchScope
from testrunner import VdsmTestCase as TestCaseBase

# speeds defined in ethtool
ETHTOOL_SPEEDS = set([10, 100, 1000, 2500, 10000])


class TestNetinfo(TestCaseBase):

    def testNetmaskConversions(self):
        path = os.path.join(os.path.dirname(__file__), "netmaskconversions")
        with open(path) as netmaskFile:
            for line in netmaskFile:
                if line.startswith('#'):
                    continue
                bitmask, address = [value.strip() for value in line.split()]
                self.assertEqual(netinfo.prefix2netmask(int(bitmask)),
                                 address)
        self.assertRaises(ValueError, netinfo.prefix2netmask, -1)
        self.assertRaises(ValueError, netinfo.prefix2netmask, 33)

    def testSpeedInvalidNic(self):
        nicName = 'DUMMYNICDEVNAME'
        self.assertTrue(nicName not in netinfo.nics())
        s = netinfo.speed(nicName)
        self.assertEqual(s, 0)

    def testSpeedInRange(self):
        for d in netinfo.nics():
            s = netinfo.speed(d)
            self.assertFalse(s < 0)
            self.assertTrue(s in ETHTOOL_SPEEDS or s == 0)

    def testIntToAddress(self):
        num = [0, 1, 16777344, 16777408, 4294967295]
        ip = ["0.0.0.0", "1.0.0.0", "128.0.0.1",
              "192.0.0.1", "255.255.255.255"]
        for n, addr in zip(num, ip):
            self.assertEqual(addr, netinfo.intToAddress(n))

    def testIPv6StrToAddress(self):
        inputs = [
            '00000000000000000000000000000000',
            '00000000000000000000000000000001',
            '20010db8000000000001000000000002',
            '20010db8aaaabbbbccccddddeeeeffff',
            'fe80000000000000be305bbffec58446']
        ip = [
            '::',
            '::1',
            '2001:db8::1:0:0:2',
            '2001:db8:aaaa:bbbb:cccc:dddd:eeee:ffff',
            'fe80::be30:5bbf:fec5:8446']
        for s, addr in zip(inputs, ip):
            self.assertEqual(addr, netinfo.ipv6StrToAddress(s))

    @MonkeyPatch(netinfo, 'networks', lambda: {'fake': {'bridged': True}})
    def testGetNonExistantBridgeInfo(self):
        # Getting info of non existing bridge should not raise an exception,
        # just log a traceback. If it raises an exception the test will fail as
        # it should.
        netinfo.get()

    def testMatchNicName(self):
        self.assertTrue(netinfo._match_name('test1', ['test0', 'test1']))

    def testIPv4toMapped(self):
        self.assertEqual('::ffff:127.0.0.1', netinfo.IPv4toMapped('127.0.0.1'))

    def testGetIfaceByIP(self):
        for dev in ethtool.get_interfaces_info(ethtool.get_active_devices()):
            # Link-local IPv6 addresses are generated from the MAC address,
            # which is shared between a nic and its bridge. Since We don't
            # support having the same IP address on two different NICs, and
            # link-local IPv6 addresses aren't interesting for 'getDeviceByIP'
            # then ignore them in the test
            ipaddrs = [ipv6.address for ipv6 in dev.get_ipv6_addresses()
                       if ipv6.scope != 'link']
            if dev.ipv4_address is not None:
                ipaddrs.append(dev.ipv4_address)
            for ip in ipaddrs:
                self.assertEqual(dev.device, netinfo.getIfaceByIP(ip))

    def _dev_dirs_setup(self, dir_fixture):
        """
        Creates test fixture which is a dir structure:
        em, me, fake0, fake1 devices that should managed by vdsm.
        hid0, hideons not managed by being hidden nics.
        jbond not managed by being hidden bond.
        me0, me1 not managed by being nics enslaved to jbond hidden bond.
        /tmp/.../em/device
        /tmp/.../me/device
        /tmp/.../fake0
        /tmp/.../fake
        /tmp/.../hid0/device
        /tmp/.../hideous/device
        /tmp/.../me0/device
        /tmp/.../me1/device
        returns related containing dir.
        """

        dev_dirs = [os.path.join(dir_fixture, dev) for dev in
                    ('em/device', 'me/device', 'fake', 'fake0',
                     'hid/device', 'hideous/device',
                     'me0/device', 'me1/device')]

        for dev_dir in dev_dirs:
            os.makedirs(dev_dir)

        bonding_path = os.path.join(dir_fixture, 'jbond/bonding')
        os.makedirs(bonding_path)
        with open(os.path.join(bonding_path, 'slaves'), 'w') as f:
            f.write('me0 me1')

        return dir_fixture

    def _config_setup(self):
        """
        Returns an instance of a config stub.
        With patterns:
            * hid* for hidden nics.
            * fake* for fake nics.
            * jb* for hidden bonds.
        """
        class Config(object):
            def get(self, unused_vars, key):
                if key == 'hidden_nics':
                    return 'hid*'
                elif key == 'fake_nics':
                    return 'fake*'
                else:
                    return 'jb*'

        return Config()

    def testNics(self):
        temp_dir = tempfile.mkdtemp()
        with MonkeyPatchScope([(netinfo, 'BONDING_SLAVES',
                                temp_dir + '/%s/bonding/slaves'),
                               (netinfo, 'NET_PATH',
                                self._dev_dirs_setup(temp_dir)),
                               (netinfo, 'config', self._config_setup())]):
            try:
                self.assertEqual(set(netinfo.nics()),
                                 set(['em', 'me', 'fake', 'fake0']))
            finally:
                rmtree(temp_dir)

    def testGetBandwidthQos(self):
        notEmptyDoc = minidom.parseString("""<bandwidth>
                            <inbound average='4500' burst='5400' />
                            <outbound average='4500' burst='5400' peak='101' />
                          </bandwidth>""")
        expectedQosNotEmpty = netinfo._Qos(inbound={'average': '4500',
                                                    'burst': '5400',
                                                    'peak': ''},
                                           outbound={'average': '4500',
                                                     'burst': '5400',
                                                     'peak': '101'})
        emptyDoc = minidom.parseString("<whatever></whatever>")

        self.assertEqual(expectedQosNotEmpty,
                         netinfo._parseBandwidthQos(notEmptyDoc))
        self.assertEqual(netinfo._Qos('', ''),
                         netinfo._parseBandwidthQos(emptyDoc))
