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
from datetime import datetime
from functools import partial
import time

from vdsm import ipwrapper
from vdsm import netconfpersistence
from vdsm import netinfo
from vdsm.netinfo import (getBootProtocol, getDhclientIfaces, BONDING_MASTERS,
                          BONDING_OPT, _getBondingOptions)
from vdsm.netlink import addr as nl_addr
from vdsm.tool.dump_bonding_defaults import _random_iface_name

from functional import dummy, veth
from ipwrapperTests import _fakeTypeDetection
from monkeypatch import MonkeyPatch, MonkeyPatchScope
from testlib import VdsmTestCase as TestCaseBase, namedTemporaryDir
from testValidation import ValidateRunningAsRoot, RequireBondingMod

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

    @MonkeyPatch(ipwrapper.Link, '_detectType',
                 partial(_fakeTypeDetection, ipwrapper.Link))
    @MonkeyPatch(netinfo, 'networks', lambda: {'fake': {'bridged': True}})
    @MonkeyPatch(netinfo, '_getBondingOptions', lambda x: {})
    def testGetNonExistantBridgeInfo(self):
        # Getting info of non existing bridge should not raise an exception,
        # just log a traceback. If it raises an exception the test will fail as
        # it should.
        netinfo.get()

    @MonkeyPatch(netinfo, 'getLinks', lambda: [])
    @MonkeyPatch(netinfo, 'networks', lambda: {})
    def testGetEmpty(self):
        result = {}
        result.update(netinfo.get())
        self.assertEqual(result['networks'], {})
        self.assertEqual(result['bridges'], {})
        self.assertEqual(result['nics'], {})
        self.assertEqual(result['bondings'], {})
        self.assertEqual(result['vlans'], {})

    def testIPv4toMapped(self):
        self.assertEqual('::ffff:127.0.0.1', netinfo.IPv4toMapped('127.0.0.1'))

    def testGetDeviceByIP(self):
        for addr in nl_addr.iter_addrs():
            # Link-local IPv6 addresses are generated from the MAC address,
            # which is shared between a nic and its bridge. Since We don't
            # support having the same IP address on two different NICs, and
            # link-local IPv6 addresses aren't interesting for 'getDeviceByIP'
            # then ignore them in the test
            if addr['scope'] != 'link':
                self.assertEqual(
                    addr['label'],
                    netinfo.getDeviceByIP(addr['address'].split('/')[0]))

    def _testNics(self):
        """Creates a test fixture so that nics() reports:
        physical nics: em, me, me0, me1, hid0 and hideous
        dummies: fake and fake0
        bonds: jbond (over me0 and me1)"""
        return [ipwrapper.Link(address='f0:de:f1:da:aa:e7', index=2,
                               linkType=ipwrapper.LinkType.NIC, mtu=1500,
                               name='em', qdisc='pfifo_fast', state='up'),
                ipwrapper.Link(address='ff:de:f1:da:aa:e7', index=3,
                               linkType=ipwrapper.LinkType.NIC, mtu=1500,
                               name='me', qdisc='pfifo_fast', state='up'),
                ipwrapper.Link(address='ff:de:fa:da:aa:e7', index=4,
                               linkType=ipwrapper.LinkType.NIC, mtu=1500,
                               name='hid0', qdisc='pfifo_fast', state='up'),
                ipwrapper.Link(address='ff:de:11:da:aa:e7', index=5,
                               linkType=ipwrapper.LinkType.NIC, mtu=1500,
                               name='hideous', qdisc='pfifo_fast', state='up'),
                ipwrapper.Link(address='66:de:f1:da:aa:e7', index=6,
                               linkType=ipwrapper.LinkType.NIC, mtu=1500,
                               name='me0', qdisc='pfifo_fast', state='up',
                               master='jbond'),
                ipwrapper.Link(address='66:de:f1:da:aa:e7', index=7,
                               linkType=ipwrapper.LinkType.NIC, mtu=1500,
                               name='me1', qdisc='pfifo_fast', state='up',
                               master='jbond'),
                ipwrapper.Link(address='ff:aa:f1:da:aa:e7', index=34,
                               linkType=ipwrapper.LinkType.DUMMY, mtu=1500,
                               name='fake0', qdisc='pfifo_fast', state='up'),
                ipwrapper.Link(address='ff:aa:f1:da:bb:e7', index=35,
                               linkType=ipwrapper.LinkType.DUMMY, mtu=1500,
                               name='fake', qdisc='pfifo_fast', state='up'),
                ipwrapper.Link(address='66:de:f1:da:aa:e7', index=419,
                               linkType=ipwrapper.LinkType.BOND, mtu=1500,
                               name='jbond', qdisc='pfifo_fast', state='up')]

    def testNics(self):
        """
        managed by vdsm: em, me, fake0, fake1
        not managed due to hidden bond (jbond) enslavement: me0, me1
        not managed due to being hidden nics: hid0, hideous
        """
        with MonkeyPatchScope([(netinfo, 'getLinks',
                                self._testNics),
                               (ipwrapper, '_bondExists',
                                lambda x: x == 'jbond'),
                               (ipwrapper.Link, '_detectType',
                                partial(_fakeTypeDetection, ipwrapper.Link)),
                               (ipwrapper.Link, '_fakeNics', ['fake*']),
                               (ipwrapper.Link, '_hiddenBonds', ['jb*']),
                               (ipwrapper.Link, '_hiddenNics', ['hid*'])
                               ]):
            self.assertEqual(set(netinfo.nics()),
                             set(['em', 'me', 'fake', 'fake0']))

    @ValidateRunningAsRoot
    def testFakeNics(self):
        with MonkeyPatchScope([(ipwrapper.Link, '_fakeNics', ['veth_*',
                                                              'dummy_*'])]):
            d1 = dummy.create()
            d2 = dummy.create(prefix='mehd_')
            v1a, v1b = veth.create()
            v2a, v2b = veth.create(prefix='mehv_')

            fakes = set([d1, v1a, v1b])
            hiddens = set([d2, v2a, v2b])
            nics = netinfo.nics()
            dummy.remove(d1)
            dummy.remove(d2)
            veth.remove(v1a)
            veth.remove(v2a)

            self.assertTrue(fakes.issubset(nics), 'Fake devices %s are not '
                            'listed in nics %s' % (fakes, nics))
            self.assertFalse(hiddens.intersection(nics), 'Some of hidden '
                             'devices %s is shown in nics %s' % (hiddens,
                                                                 nics))

    def testGetBootProtocolIfcfg(self):
        deviceName = "___This_could_never_be_a_device_name___"
        ifcfg = ('DEVICE=%s' % deviceName + '\n' + 'ONBOOT=yes' + '\n' +
                 'MTU=1500' + '\n' + 'HWADDR=5e:64:6d:12:16:84' + '\n')
        with namedTemporaryDir() as tempDir:
            ifcfgPrefix = os.path.join(tempDir, 'ifcfg-')
            filePath = ifcfgPrefix + deviceName

            with MonkeyPatchScope([(netinfo, 'NET_CONF_PREF', ifcfgPrefix)]):
                with open(filePath, 'w') as ifcfgFile:
                    ifcfgFile.write(ifcfg + 'BOOTPROTO=dhcp\n')
                self.assertEqual(getBootProtocol(deviceName, 'ifcfg'), 'dhcp')

                with open(filePath, 'w') as ifcfgFile:
                    ifcfgFile.write(ifcfg + 'BOOTPROTO=none\n')
                self.assertEqual(getBootProtocol(deviceName, 'ifcfg'), 'none')

                with open(filePath, 'w') as ifcfgFile:
                    ifcfgFile.write(ifcfg)
                self.assertEqual(getBootProtocol(deviceName, 'ifcfg'), None)

    def testGetIfaceCfg(self):
        deviceName = "___This_could_never_be_a_device_name___"
        ifcfg = ('GATEWAY0=1.1.1.1\n' 'NETMASK=255.255.0.0\n')
        with namedTemporaryDir() as tempDir:
            ifcfgPrefix = os.path.join(tempDir, 'ifcfg-')
            filePath = ifcfgPrefix + deviceName

            with MonkeyPatchScope([(netinfo, 'NET_CONF_PREF', ifcfgPrefix)]):
                with open(filePath, 'w') as ifcfgFile:
                    ifcfgFile.write(ifcfg)
                self.assertEqual(
                    netinfo.getIfaceCfg(deviceName)['GATEWAY'], '1.1.1.1')
                self.assertEqual(
                    netinfo.getIfaceCfg(deviceName)['NETMASK'], '255.255.0.0')

    def testGetBootProtocolUnified(self):
        with namedTemporaryDir() as tempDir:
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

            with MonkeyPatchScope([(netconfpersistence, 'CONF_RUN_DIR',
                                   tempDir)]):
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

    def testGetDhclientIfaces(self):
        LEASES = (
            'lease {{\n'
            '  interface "valid";\n'
            '  expire {0:%w %Y/%m/%d %H:%M:%S};\n'
            '}}\n'
            'lease {{\n'
            '  interface "valid2";\n'
            '  expire epoch {1:.0f}; # Sat Jan 31 20:04:20 2037\n'
            '}}\n'                   # human-readable date is just a comment
            'lease {{\n'
            '  interface "expired";\n'
            '  expire {2:%w %Y/%m/%d %H:%M:%S};\n'
            '}}\n'
            'lease {{\n'
            '  interface "expired2";\n'
            '  expire epoch {3:.0f}; # Fri Jan 31 20:04:20 2014\n'
            '}}\n'
        )

        with namedTemporaryDir() as tmpDir:
            leaseFile = os.path.join(tmpDir, 'test.lease')
            with open(leaseFile, 'w') as f:
                lastMinute = time.time() - 60
                nextMinute = time.time() + 60

                f.write(LEASES.format(
                    datetime.utcfromtimestamp(nextMinute),
                    nextMinute,
                    datetime.utcfromtimestamp(lastMinute),
                    lastMinute
                ))

            dhcp4 = getDhclientIfaces([leaseFile])

        self.assertIn('valid', dhcp4)
        self.assertIn('valid2', dhcp4)
        self.assertNotIn('expired', dhcp4)
        self.assertNotIn('expired2', dhcp4)

    @ValidateRunningAsRoot
    @RequireBondingMod
    def testGetBondingOptions(self):
        INTERVAL = '12345'
        bondName = _random_iface_name()

        with open(BONDING_MASTERS, 'w') as bonds:
            bonds.write('+' + bondName)
            bonds.flush()

            try:
                with self.assertNotRaises():
                    self.assertEqual(_getBondingOptions(bondName), {})

                    with open(BONDING_OPT % (bondName, 'miimon'), 'w') as opt:
                        opt.write(INTERVAL)

                self.assertEqual(_getBondingOptions(bondName),
                                 {'miimon': INTERVAL})

            finally:
                bonds.write('-' + bondName)
