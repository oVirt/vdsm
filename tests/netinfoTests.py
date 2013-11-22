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
from functools import partial
from shutil import rmtree
import tempfile
from xml.dom import minidom

import ethtool

from vdsm import ipwrapper
from vdsm import netconfpersistence
from vdsm import netinfo
from vdsm.netinfo import getBootProtocol

from ipwrapperTests import _fakeTypeDetection
from monkeypatch import MonkeyPatch, MonkeyPatchScope
from testrunner import VdsmTestCase as TestCaseBase
from testValidation import brokentest

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

    @MonkeyPatch(ipwrapper.Link, '_detectType',
                 partial(_fakeTypeDetection, ipwrapper.Link))
    def testSpeedInvalidNic(self):
        nicName = '0' * 20  # devices can't have so long names
        self.assertEqual(netinfo.nicSpeed(nicName), 0)

    @MonkeyPatch(ipwrapper.Link, '_detectType',
                 partial(_fakeTypeDetection, ipwrapper.Link))
    def testSpeedInRange(self):
        for d in netinfo.nics():
            s = netinfo.nicSpeed(d)
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

    @MonkeyPatch(ipwrapper.Link, '_detectType',
                 partial(_fakeTypeDetection, ipwrapper.Link))
    @MonkeyPatch(netinfo, 'networks', lambda: {'fake': {'bridged': True}})
    def testGetNonExistantBridgeInfo(self):
        # Getting info of non existing bridge should not raise an exception,
        # just log a traceback. If it raises an exception the test will fail as
        # it should.
        netinfo.get()

    def testIPv4toMapped(self):
        self.assertEqual('::ffff:127.0.0.1', netinfo.IPv4toMapped('127.0.0.1'))

    @brokentest('Broken since python-ethtool-0.6-5, which returns devices '
                'with no ip. Those devices are then sent to getIfaceByIP '
                'which returns an empty device')
    def testGetIfaceByIP(self):
        for dev in ethtool.get_interfaces_info(ethtool.get_active_devices()):
            ipaddrs = map(
                lambda etherinfo_ipv6addr: etherinfo_ipv6addr.address,
                dev.get_ipv6_addresses())
            ipaddrs.append(dev.ipv4_address)
            for ip in ipaddrs:
                self.assertEqual(dev.device, netinfo.getIfaceByIP(ip))

    def _testNics(self):
        """Creates a test fixture so that nics() reports:
        physical nics: em, me, me0, me1, hid0 and hideous
        dummies: fake and fake0"""
        lines = ('2: em: <BROADCAST,MULTICAST,UP,LOWER_UP> mtu 1500 qdisc '
                 'pfifo_fast state UP mode DEFAULT group default qlen 1000\\  '
                 '  link/ether f0:de:f1:da:aa:e7 brd ff:ff:ff:ff:ff:ff '
                 'promiscuity 0 \\    nic ',
                 '3: me: <BROADCAST,MULTICAST,UP,LOWER_UP> mtu 1500 qdisc '
                 'pfifo_fast state UP mode DEFAULT group default qlen 1000\\  '
                 '  link/ether ff:de:f1:da:aa:e7 brd ff:ff:ff:ff:ff:ff '
                 'promiscuity 0 \\    nic ',
                 '4: hid0: <BROADCAST,MULTICAST,UP,LOWER_UP> mtu 1500 qdisc '
                 'pfifo_fast state UP mode DEFAULT group default qlen 1000\\  '
                 '  link/ether ff:de:fa:da:aa:e7 brd ff:ff:ff:ff:ff:ff '
                 'promiscuity 0 \\    nic ',
                 '5: hideous: <BROADCAST,MULTICAST,UP,LOWER_UP> mtu 1500 qdisc'
                 ' pfifo_fast state UP mode DEFAULT group default qlen 1000\\ '
                 '   link/ether ff:de:11:da:aa:e7 brd ff:ff:ff:ff:ff:ff '
                 'promiscuity 0 \\    nic ',
                 '6: me0: <BROADCAST,MULTICAST,UP,LOWER_UP> mtu 1500 qdisc '
                 'pfifo_fast state UP mode DEFAULT group default qlen 1000\\  '
                 '  link/ether 66:de:f1:da:aa:e7 brd ff:ff:ff:ff:ff:ff '
                 'promiscuity 0 \\    nic ',
                 '7: me1: <BROADCAST,MULTICAST,UP,LOWER_UP> mtu 1500 qdisc '
                 'pfifo_fast state UP mode DEFAULT group default qlen 1000\\  '
                 '  link/ether 77:de:f1:da:aa:e7 brd ff:ff:ff:ff:ff:ff '
                 'promiscuity 0 \\    nic ',
                 '34: fake0: <BROADCAST,MULTICAST,UP,LOWER_UP> mtu 1500 qdisc '
                 'pfifo_fast state UP mode DEFAULT group default qlen 1000\\  '
                 '  link/ether ff:aa:f1:da:aa:e7 brd ff:ff:ff:ff:ff:ff '
                 'promiscuity 0  \\    dummy ',
                 '35: fake: <BROADCAST,MULTICAST,UP,LOWER_UP> mtu 1500 qdisc '
                 'pfifo_fast state UP mode DEFAULT group default qlen 1000\\  '
                 '  link/ether ff:aa:f1:da:bb:e7 brd ff:ff:ff:ff:ff:ff '
                 'promiscuity 0  \\    dummy ')
        return [ipwrapper.Link.fromText(line) for line in lines]

    def _dev_dirs_setup(self, dir_fixture):
        """
        Creates test fixture so that the nics created by _testNics are reported
        as:
        managed by vdsm: em, me, fake0, fake1
        not managed due to hidden bond (jbond) enslavement: me0, me1
        not managed due to being hidden nics: hid0, hideous

        returns related containing dir.
        """
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
                               (netinfo, 'getLinks',
                                self._testNics),
                               (netinfo, 'NET_PATH',
                                self._dev_dirs_setup(temp_dir)),
                               (ipwrapper.Link, '_detectType',
                                partial(_fakeTypeDetection, ipwrapper.Link)),
                               (netinfo, 'config', self._config_setup()),
                               (ipwrapper.Link, '_fakeNics', ['fake*']),
                               (ipwrapper.Link, '_hiddenNics', ['hid*'])
                               ]):
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

    def testGetBootProtocolIfcfg(self):
        deviceName = "___This_could_never_be_a_device_name___"
        ifcfg = ('DEVICE=%s' % deviceName + '\n' + 'ONBOOT=yes' + '\n' +
                 'MTU=1500' + '\n' + 'HWADDR=5e:64:6d:12:16:84' + '\n')
        tempDir = tempfile.mkdtemp()
        ifcfgPrefix = os.path.join(tempDir, 'ifcfg-')
        filePath = ifcfgPrefix + deviceName

        with MonkeyPatchScope([(netinfo, 'NET_CONF_PREF', ifcfgPrefix)]):
            try:
                with open(filePath, 'w') as ifcfgFile:
                    ifcfgFile.write(ifcfg + 'BOOTPROTO=dhcp\n')
                self.assertEqual(getBootProtocol(deviceName, 'ifcfg'), 'dhcp')

                with open(filePath, 'w') as ifcfgFile:
                    ifcfgFile.write(ifcfg + 'BOOTPROTO=none\n')
                self.assertEqual(getBootProtocol(deviceName, 'ifcfg'), 'none')

                with open(filePath, 'w') as ifcfgFile:
                    ifcfgFile.write(ifcfg)
                self.assertEqual(getBootProtocol(deviceName, 'ifcfg'), None)
            finally:
                rmtree(tempDir)

    def testGetBootProtocolUnified(self):
        tempDir = tempfile.mkdtemp()
        netsDir = os.path.join(tempDir, 'nets')
        os.mkdir(netsDir)
        networks = {
            'nonVMOverNic':
            {"nic": "eth0", "bridged": False, "bootproto": "dhcp"},
            'bridgeOverNic':
            {"nic": "eth1", "bridged": True},
            'nonVMOverBond':
            {"bonding": "bond0", "bridged": False, "bootproto": "dhcp"},
            'bridgeOverBond':
            {"bonding": "bond1", "bridged": True},
            'vlanOverNic':
            {"nic": "eth2", "bridged": False, "vlan": 1,
             "bootproto": "dhcp"},
            'bridgeOverVlan':
            {"nic": "eth3", "bridged": True, "vlan": 1},
            'vlanOverBond':
            {"bonding": "bond2", "bridged": False, "bootproto": "dhcp",
             "vlan": 1},
            'bridgeOverVlanOverBond':
            {"bonding": "bond3", "bridged": True, "vlan": 1}}

        with MonkeyPatchScope([(netconfpersistence, 'CONF_RUN_DIR', tempDir)]):
            try:
                runningConfig = netconfpersistence.RunningConfig()
                for network, attributes in networks.iteritems():
                    runningConfig.setNetwork(network, attributes)
                runningConfig.save()

                for network, attributes in networks.iteritems():
                    if attributes.get('bridged') == 'true':
                        topLevelDevice = network
                    else:
                        topLevelDevice = attributes.get('nic') or \
                            attributes.get('bonding')
                        if attributes.get('vlan'):
                            topLevelDevice += '.%s' % attributes.get('vlan')
                    self.assertEqual(
                        getBootProtocol(topLevelDevice, 'unified'),
                        attributes.get('bootproto'))
            finally:
                rmtree(tempDir)
