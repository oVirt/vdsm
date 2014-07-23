#
# Copyright 2013-2014 Red Hat, Inc.
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
from contextlib import contextmanager
from functools import wraps
import os.path
import json

from hookValidation import ValidatesHook
from testrunner import (VdsmTestCase as TestCaseBase, namedTemporaryDir,
                        expandPermutations, permutations)
from testValidation import (brokentest, RequireDummyMod, RequireVethMod,
                            ValidateRunningAsRoot)

import dhcp
import dummy
import firewall
import veth
from nose.plugins.skip import SkipTest
from utils import SUCCESS, VdsProxy, cleanupRules

from vdsm.ipwrapper import (ruleAdd, ruleDel, routeAdd, routeDel, routeExists,
                            ruleExists, Route, Rule, addrFlush, LinkType,
                            getLinks)

from vdsm.constants import EXT_BRCTL
from vdsm.utils import RollbackContext, execCmd
from vdsm.netinfo import (bridges, operstate, prefix2netmask, getRouteDeviceTo,
                          getDhclientIfaces)
from vdsm import ipwrapper
from vdsm.utils import pgrep

import caps
from network import errors


NETWORK_NAME = 'test-network'
VLAN_ID = '27'
BONDING_NAME = 'bond0'
IP_ADDRESS = '240.0.0.1'
IP_NETWORK = '240.0.0.0'
IP_ADDRESS_IN_NETWORK = '240.0.0.50'
IP_CIDR = '24'
IP_NETWORK_AND_CIDR = IP_NETWORK + '/' + IP_CIDR
IP_GATEWAY = '240.0.0.254'
# Current implementation converts ip to its 32 bit int representation
IP_TABLE = '4026531841'
DHCP_RANGE_FROM = '240.0.0.10'
DHCP_RANGE_TO = '240.0.0.100'
CUSTOM_PROPS = {'linux': 'rules', 'vdsm': 'as well'}

IPv6_ADDRESS = 'fdb3:84e5:4ff4:55e3::1/64'
IPv6_GATEWAY = 'fdb3:84e5:4ff4:55e3::ff'

dummyPool = set()
DUMMY_POOL_SIZE = 5

NOCHK = {'connectivityCheck': False}


def setupModule():
    """Persists network configuration."""
    vdsm = VdsProxy()
    vdsm.save_config()
    for _ in range(DUMMY_POOL_SIZE):
        dummyPool.add(dummy.create())


def tearDownModule():
    """Restores the network configuration previous to running tests."""
    vdsm = VdsProxy()
    vdsm.restoreNetConfig()
    for nic in dummyPool:
        dummy.remove(nic)


@contextmanager
def dnsmasqDhcp(interface):
    """Manages the life cycle of dnsmasq as a DHCP server."""
    dhcpServer = dhcp.Dnsmasq()
    try:
        dhcpServer.start(interface, DHCP_RANGE_FROM, DHCP_RANGE_TO)
    except dhcp.DhcpError as e:
        raise SkipTest(e)

    with firewallDhcp(interface):
        try:
            yield
        finally:
            dhcpServer.stop()


@contextmanager
def avoidAnotherDhclient(interface):
    """Makes sure no other dhclient is run automatically on the interface."""
    has_nm = pgrep('NetworkManager')

    if has_nm:
        connectionName = 'placeholder-' + interface
        dhcp.addNMplaceholderConnection(interface, connectionName)

    try:
        yield
    finally:
        if has_nm:
            dhcp.removeNMplaceholderConnection(connectionName)


@contextmanager
def firewallDhcp(interface):
    """ Adds and removes firewall rules for DHCP"""
    firewall.allowDhcp(interface)
    try:
        yield
    finally:
        firewall.stopAllowingDhcp(interface)


@contextmanager
def dummyIf(num):
    """Manages a list of num dummy interfaces. Assumes root privileges."""
    dummies = []
    try:
        for _ in range(num):
            dummies.append(dummyPool.pop())
        yield dummies
    finally:
        for nic in dummies:
            dummyPool.add(nic)


@contextmanager
def vethIf():
    """ Yields a tuple containing pair of veth devices."""
    (left, right) = veth.create()
    try:
        yield (left, right)
    finally:
        veth.remove(left)


class OperStateChangedError(ValueError):
    pass


@contextmanager
def nonChangingOperstate(device):
    """Raises an exception if it detects that the device link state changes."""
    originalState = operstate(device).upper()
    monitor = ipwrapper.Monitor()
    monitor.start()
    try:
        yield
    finally:
        monitor.stop()
        changes = [(event.device, event.state) for event in monitor
                   if event.device == device]
        for _, state in changes:
            if state != originalState:
                raise OperStateChangedError('%s operstate changed: %s -> %r' %
                                            (device, originalState, changes))


@expandPermutations
class NetworkTest(TestCaseBase):

    def setUp(self):
        self.vdsm_net = VdsProxy()

    def cleanupNet(func):
        """
        Instance method decorator. Restores a previously persisted network
        config in case of a test failure, traceback is kept. Assumes root
        privileges.
        """

        @wraps(func)
        def wrapper(*args, **kwargs):
            with RollbackContext() as rollback:
                rollback.prependDefer(args[0].vdsm_net.restoreNetConfig)
                func(*args, **kwargs)
        return wrapper

    def assertNetworkExists(self, networkName, bridged=None, bridgeOpts=None):
        netinfo = self.vdsm_net.netinfo
        config = self.vdsm_net.config
        self.assertIn(networkName, netinfo.networks)
        if bridged is not None:
            self.assertEqual(bridged, netinfo.networks[networkName]['bridged'])
        if bridgeOpts is not None and netinfo.networks[networkName]['bridged']:
            appliedOpts = netinfo.bridges[networkName]['opts']
            for opt, value in bridgeOpts.iteritems():
                self.assertEqual(value, appliedOpts[opt])
        if config is not None:
            self.assertIn(networkName, config.networks)
            if bridged is not None:
                self.assertEqual(config.networks[networkName].get('bridged'),
                                 bridged)

    def assertNetworkDoesntExist(self, networkName):
        self.assertNotIn(networkName, self.vdsm_net.netinfo.networks)
        if self.vdsm_net.config is not None:
            self.assertNotIn(networkName, self.vdsm_net.config.networks)

    def assertBondExists(self, bondName, nics=None, options=None):
        netinfo = self.vdsm_net.netinfo
        config = self.vdsm_net.config
        self.assertIn(bondName, netinfo.bondings)
        if nics is not None:
            self.assertEqual(set(nics),
                             set(netinfo.bondings[bondName]['slaves']))
        if config is not None:
            self.assertIn(bondName, config.bonds)
            self.assertEqual(set(nics),
                             set(config.bonds[bondName].get('nics')))
        if options is not None:
            active = (opt + '=' + val for (opt, val)
                      in netinfo.bondings[bondName]['opts'].iteritems())
            self.assertTrue(set(options.split()) <= set(active))

    def assertBondDoesntExist(self, bondName, nics=None):
        netinfo = self.vdsm_net.netinfo
        config = self.vdsm_net.config
        self.assertTrue(bondName not in netinfo.bondings or (set(nics) !=
                        set(netinfo.bondings[bondName]['slaves'])),
                        '%s found unexpectedly' % bondName)
        if config is not None:
            self.assertTrue(bondName not in config.bonds or (set(nics) !=
                            set(config.bonds[bondName].get('nics'))),
                            '%s found unexpectedly in running config' %
                            bondName)

    def assertVlanExists(self, vlanName):
        netinfo = self.vdsm_net.netinfo
        devName, vlanId = vlanName.split('.')
        self.assertIn(vlanName, netinfo.vlans)
        if devName:
            self.assertEqual(devName, netinfo.vlans[vlanName]['iface'])
            if self.vdsm_net.config is not None:
                self.assertTrue(
                    self.vdsm_net._vlanInRunningConfig(devName, vlanId),
                    '%s not in running config' % vlanName)

    def assertVlanDoesntExist(self, vlanName):
        devName, vlanId = vlanName.split('.')
        self.assertNotIn(vlanName, self.vdsm_net.netinfo.vlans)
        if devName and self.vdsm_net.config is not None:
            self.assertFalse(
                self.vdsm_net._vlanInRunningConfig(devName, vlanId),
                '%s found unexpectedly in running config' % vlanName)

    def assertMtu(self, mtu, *elems):
        for elem in elems:
            self.assertEquals(int(mtu), int(self.vdsm_net.getMtu(elem)))

    def testLegacyBonds(self):
        if not (caps.getos() in (caps.OSName.RHEVH, caps.OSName.RHEL)
                and caps.osversion()['version'].startswith('6')):
            raise SkipTest('legacy bonds are expected only on el6')

        for b in caps._REQUIRED_BONDINGS:
            # assertBondExists is not used here since we do not care about
            # whether the bond exists in the running config; we only need it to
            # be reported to legacy Engines.
            self.assertIn(b, self.vdsm_net.netinfo.bondings)

    @cleanupNet
    @permutations([[True], [False]])
    @RequireDummyMod
    @ValidateRunningAsRoot
    def testSetupNetworksAddBondWithManyVlans(self, bridged):
        VLAN_COUNT = 5
        network_names = [NETWORK_NAME + str(tag) for tag in range(VLAN_COUNT)]
        with dummyIf(2) as nics:
            networks = dict((vlan_net,
                             {'vlan': str(tag), 'bonding': BONDING_NAME,
                              'bridged': bridged})
                            for tag, vlan_net in enumerate(network_names))
            bondings = {BONDING_NAME: {'nics': nics}}

            status, msg = self.vdsm_net.setupNetworks(networks, bondings,
                                                      NOCHK)
            self.assertEqual(status, SUCCESS, msg)
            for vlan_net in network_names:
                self.assertNetworkExists(vlan_net, bridged)
                self.assertBondExists(BONDING_NAME, nics)
                self.assertVlanExists(BONDING_NAME + '.' +
                                      networks[vlan_net]['vlan'])

            for vlan_net in network_names:
                status, msg = self.vdsm_net.setupNetworks(
                    {vlan_net: {'remove': True}},
                    {BONDING_NAME: {'remove': True}}, NOCHK)
                self.assertEqual(status, SUCCESS, msg)
                self.assertNetworkDoesntExist(vlan_net)
                self.assertVlanDoesntExist(BONDING_NAME + '.' +
                                           networks[vlan_net]['vlan'])

    @cleanupNet
    @permutations([[True], [False]])
    @RequireDummyMod
    @ValidateRunningAsRoot
    def testSetupNetworksAddDelBondedNetwork(self, bridged):
        with dummyIf(2) as nics:
            status, msg = self.vdsm_net.setupNetworks(
                {NETWORK_NAME:
                    {'bonding': BONDING_NAME, 'bridged': bridged}},
                {BONDING_NAME: {'nics': nics, 'options': 'mode=2'}}, NOCHK)
            self.assertEqual(status, SUCCESS, msg)
            self.assertNetworkExists(NETWORK_NAME, bridged)
            self.assertBondExists(BONDING_NAME, nics, 'mode=2')

            status, msg = self.vdsm_net.setupNetworks(
                {NETWORK_NAME: {'remove': True}},
                {BONDING_NAME: {'remove': True}}, NOCHK)
            self.assertEqual(status, SUCCESS, msg)
            self.assertNetworkDoesntExist(NETWORK_NAME)

    @cleanupNet
    @permutations([[True], [False]])
    @RequireDummyMod
    @ValidateRunningAsRoot
    def testSetupNetworksAddOverExistingBond(self, bridged=True):
        with dummyIf(2) as nics:
            status, msg = self.vdsm_net.setupNetworks(
                {NETWORK_NAME + '0': {'bonding': BONDING_NAME,
                                      'bridged': False}},
                {BONDING_NAME: {'nics': nics}},
                NOCHK)
            self.assertEqual(status, SUCCESS, msg)
            self.assertBondExists(BONDING_NAME, nics)

            with nonChangingOperstate(BONDING_NAME):
                status, msg = self.vdsm_net.setupNetworks(
                    {NETWORK_NAME:
                        {'bonding': BONDING_NAME, 'bridged': bridged,
                         'vlan': VLAN_ID}},
                    {}, NOCHK)
            self.assertEqual(status, SUCCESS, msg)
            self.assertNetworkExists(NETWORK_NAME, bridged)

            status, msg = self.vdsm_net.setupNetworks(
                {NETWORK_NAME: {'remove': True},
                 NETWORK_NAME + '0': {'remove': True}},
                {}, NOCHK)
            self.assertEqual(status, SUCCESS, msg)
            self.assertBondExists(BONDING_NAME, nics)

            status, msg = self.vdsm_net.setupNetworks(
                {},
                {BONDING_NAME: {'remove': True}}, NOCHK)
            self.assertEqual(status, SUCCESS, msg)

    @cleanupNet
    @RequireDummyMod
    @ValidateRunningAsRoot
    def testSetupNetworksDelOneOfBondNets(self):
        NETA_NAME = NETWORK_NAME + 'A'
        NETB_NAME = NETWORK_NAME + 'B'
        NETA_DICT = {'bonding': BONDING_NAME, 'bridged': False, 'mtu': '1600',
                     'vlan': '4090'}
        NETB_DICT = {'bonding': BONDING_NAME, 'bridged': False, 'mtu': '2000',
                     'vlan': '4091'}
        with dummyIf(2) as nics:
            status, msg = self.vdsm_net.setupNetworks(
                {NETA_NAME: NETA_DICT,
                 NETB_NAME: NETB_DICT},
                {BONDING_NAME: {'nics': nics}}, NOCHK)
            self.assertEqual(status, SUCCESS, msg)
            self.assertNetworkExists(NETA_NAME)
            self.assertNetworkExists(NETB_NAME)
            self.assertBondExists(BONDING_NAME, nics)
            self.assertMtu(NETB_DICT['mtu'], BONDING_NAME)

            with nonChangingOperstate(BONDING_NAME):
                status, msg = self.vdsm_net.setupNetworks(
                    {NETB_NAME: {'remove': True}}, {}, NOCHK)

            self.assertEqual(status, SUCCESS, msg)
            self.assertNetworkExists(NETA_NAME)
            self.assertNetworkDoesntExist(NETB_NAME)
            # Check that the mtu of the bond has been adjusted to the smaller
            # NETA value
            self.assertMtu(NETA_DICT['mtu'], BONDING_NAME)

            status, msg = self.vdsm_net.setupNetworks(
                {NETA_NAME: {'remove': True}},
                {BONDING_NAME: {'remove': True}}, NOCHK)
            self.assertEqual(status, SUCCESS, msg)

    @cleanupNet
    @permutations([[True], [False]])
    @RequireDummyMod
    @ValidateRunningAsRoot
    def testAddDelBondedNetwork(self, bridged):
        with dummyIf(2) as nics:
            status, msg = self.vdsm_net.addNetwork(NETWORK_NAME,
                                                   bond=BONDING_NAME,
                                                   nics=nics,
                                                   opts={'bridged': bridged})
            self.assertEqual(status, SUCCESS, msg)

            self.assertNetworkExists(NETWORK_NAME, bridged)
            self.assertBondExists(BONDING_NAME, nics)

            status, msg = self.vdsm_net.delNetwork(NETWORK_NAME)
            self.assertEqual(status, SUCCESS, msg)
            self.assertNetworkDoesntExist(NETWORK_NAME)

    @cleanupNet
    @permutations([[True], [False]])
    @RequireDummyMod
    @ValidateRunningAsRoot
    def testAddDelNetwork(self, bridged):
        with dummyIf(1) as nics:
            status, msg = self.vdsm_net.addNetwork(NETWORK_NAME,
                                                   nics=nics,
                                                   opts={'bridged': bridged})
            self.assertEqual(status, SUCCESS, msg)
            self.assertNetworkExists(NETWORK_NAME)

            status, msg = self.vdsm_net.delNetwork(NETWORK_NAME,
                                                   nics=nics,
                                                   opts={'bridged': bridged})
            self.assertEqual(status, SUCCESS, msg)
            self.assertNetworkDoesntExist(NETWORK_NAME)

    @cleanupNet
    @permutations([[True], [False]])
    @RequireDummyMod
    @ValidateRunningAsRoot
    def testFailWithInvalidBondingName(self, bridged):
        with dummyIf(1) as nics:
            invalid_bond_names = ('bond', 'bonda', 'bond0a', 'jamesbond007')
            for bond_name in invalid_bond_names:
                status, msg = self.vdsm_net.addNetwork(NETWORK_NAME,
                                                       bond=bond_name,
                                                       nics=nics,
                                                       opts={'bridged':
                                                             bridged})
                self.assertEqual(status, errors.ERR_BAD_BONDING, msg)

    @cleanupNet
    def testFailWithInvalidBridgeName(self):
        invalid_bridge_names = ('a' * 16, 'a b', 'a\tb', 'a.b', 'a:b')
        for bridge_name in invalid_bridge_names:
            status, msg = self.vdsm_net.addNetwork(bridge_name)
            self.assertEqual(status, errors.ERR_BAD_BRIDGE, msg)

    @cleanupNet
    def testFailWithInvalidIpConfig(self):
        invalid_ip_configs = (dict(IPADDR='1.2.3.4'), dict(NETMASK='1.2.3.4'),
                              dict(GATEWAY='1.2.3.4'),
                              dict(IPADDR='1.2.3', NETMASK='255.255.0.0'),
                              dict(IPADDR='1.2.3.256', NETMASK='255.255.0.0'),
                              dict(IPADDR='1.2.3.4', NETMASK='256.255.0.0'),
                              dict(IPADDR='1.2.3.4.5', NETMASK='255.255.0.0'),
                              dict(IPADDR='1.2.3.4', NETMASK='255.255.0.0',
                                   GATEWAY='1.2.3.256'),
                              )
        for ipconfig in invalid_ip_configs:
            status, msg = self.vdsm_net.addNetwork(NETWORK_NAME,
                                                   opts=ipconfig)
            self.assertEqual(status, errors.ERR_BAD_ADDR, msg)

    @cleanupNet
    @permutations([[True], [False]])
    def testFailWithInvalidNic(self, bridged):
        status, msg = self.vdsm_net.addNetwork(NETWORK_NAME,
                                               nics=['nowaythisnicexists'],
                                               opts={'bridged': bridged})

        self.assertEqual(status, errors.ERR_BAD_NIC, msg)

    @cleanupNet
    @permutations([[True], [False]])
    def testFailWithInvalidParams(self, bridged):
        status, msg = self.vdsm_net.addNetwork(NETWORK_NAME, VLAN_ID,
                                               opts={'bridged': bridged})
        self.assertEqual(status, errors.ERR_BAD_PARAMS, msg)

        status, msg = self.vdsm_net.addNetwork(NETWORK_NAME,
                                               bond=BONDING_NAME,
                                               opts={'bridged': bridged})
        self.assertEqual(status, errors.ERR_BAD_PARAMS, msg)

    @cleanupNet
    @permutations([[True], [False]])
    @RequireDummyMod
    @ValidateRunningAsRoot
    def testAddNetworkManyVlans(self, bridged):
        opts = {'bridged': bridged}
        VLAN_COUNT = 5
        NET_VLANS = [(NETWORK_NAME + str(index), str(index))
                     for index in range(VLAN_COUNT)]
        with dummyIf(1) as nics:
            firstVlan, firstVlanId = NET_VLANS[0]
            status, msg = self.vdsm_net.addNetwork(firstVlan, vlan=firstVlanId,
                                                   nics=nics, opts=opts)
            self.assertEquals(status, SUCCESS, msg)
            with nonChangingOperstate(nics[0]):
                for netVlan, vlanId in NET_VLANS[1:]:
                    status, msg = self.vdsm_net.addNetwork(netVlan,
                                                           vlan=vlanId,
                                                           nics=nics,
                                                           opts=opts)
                    self.assertEquals(status, SUCCESS, msg)

            for netVlan, vlanId in NET_VLANS:
                self.assertNetworkExists(netVlan, bridged=bridged)
                self.assertVlanExists(nics[0] + '.' + str(vlanId))

                status, msg = self.vdsm_net.delNetwork(netVlan)
                self.assertEquals(status, SUCCESS, msg)

    @cleanupNet
    @permutations([[True], [False]])
    @RequireDummyMod
    @ValidateRunningAsRoot
    def testAddNetworkVlan(self, bridged):
        with dummyIf(1) as nics:
            status, msg = self.vdsm_net.addNetwork(NETWORK_NAME, vlan=VLAN_ID,
                                                   nics=nics,
                                                   opts={'bridged': bridged,
                                                         'STP': 'off'})
            self.assertEquals(status, SUCCESS, msg)

            self.assertNetworkExists(NETWORK_NAME, bridged=bridged)
            self.assertVlanExists(nics[0] + '.' + VLAN_ID)

            status, msg = self.vdsm_net.delNetwork(NETWORK_NAME)
            self.assertEquals(status, SUCCESS, msg)

    @cleanupNet
    @permutations([[True], [False]])
    @RequireDummyMod
    @ValidateRunningAsRoot
    def testAddNetworkBondWithManyVlans(self, bridged):
        opts = dict(bridged=bridged)
        VLAN_COUNT = 5
        NET_VLANS = [(NETWORK_NAME + str(index), str(index))
                     for index in range(VLAN_COUNT)]
        with dummyIf(1) as nics:
            firstVlan, firstVlanId = NET_VLANS[0]
            status, msg = self.vdsm_net.addNetwork(firstVlan, vlan=firstVlanId,
                                                   bond=BONDING_NAME,
                                                   nics=nics, opts=opts)
            self.assertEquals(status, SUCCESS, msg)
            with nonChangingOperstate(BONDING_NAME):
                for netVlan, vlanId in NET_VLANS[1:]:
                    status, msg = self.vdsm_net.addNetwork(netVlan,
                                                           vlan=vlanId,
                                                           bond=BONDING_NAME,
                                                           nics=nics,
                                                           opts=opts)
                    self.assertEquals(status, SUCCESS, msg)
                    self.assertNetworkExists(netVlan, bridged=bridged)
            for _, vlanId in NET_VLANS:
                vlanName = '%s.%s' % (BONDING_NAME, vlanId)
                self.assertVlanExists(vlanName)

            for netVlan, vlanId in NET_VLANS:
                status, msg = self.vdsm_net.delNetwork(netVlan, vlan=vlanId,
                                                       bond=BONDING_NAME,
                                                       nics=nics)
                self.assertEqual(status, SUCCESS, msg)

    @cleanupNet
    @permutations([[True], [False]])
    @RequireDummyMod
    @ValidateRunningAsRoot
    def testAddNetworkVlanBond(self, bridged):
        with dummyIf(1) as nics:
            vlan_id = '42'
            status, msg = self.vdsm_net.addNetwork(NETWORK_NAME,
                                                   vlan=vlan_id,
                                                   bond=BONDING_NAME,
                                                   nics=nics,
                                                   opts={'bridged': bridged})
            self.assertEquals(status, SUCCESS, msg)
            self.assertNetworkExists(NETWORK_NAME, bridged=bridged)
            qosInbound, qosOutbound = self.vdsm_net.networkQos(NETWORK_NAME)
            status, msg = self.vdsm_net.delNetwork(NETWORK_NAME, vlan=vlan_id,
                                                   bond=BONDING_NAME,
                                                   nics=nics)
            self.assertEqual({}, qosInbound)
            self.assertEqual({}, qosOutbound)
            self.assertEqual(status, SUCCESS, msg)

    @cleanupNet
    @RequireDummyMod
    @ValidateRunningAsRoot
    def testQosNetwork(self):
        def testNetQoS(qos):
            status, msg = self.vdsm_net.addNetwork(NETWORK_NAME, nics=nics,
                                                   opts=qos)
            self.assertEqual(status, SUCCESS, msg)

            qosInbound, qosOutbound = self.vdsm_net.networkQos(NETWORK_NAME)
            self.assertEqual(qos.get('qosInbound', {}), qosInbound)
            self.assertEqual(qos.get('qosOutbound', {}), qosOutbound)

            status, msg = self.vdsm_net.delNetwork(NETWORK_NAME)
            self.assertEqual(status, SUCCESS, msg)

        with dummyIf(1) as nics:
            qosComplete = {'qosInbound': {'average': 1024, 'burst': 2048,
                                          'peak': 42},
                           'qosOutbound': {'average': 2400, 'burst': 2048,
                                           'peak': 100}}
            testNetQoS(qosComplete)

            qosInbOnly = {'qosInbound': {'average': 1024}}
            testNetQoS(qosInbOnly)

    @cleanupNet
    @permutations([[True], [False]])
    @RequireDummyMod
    @ValidateRunningAsRoot
    def testDelNetworkWithMTU(self, bridged):
        MTU = '1234'
        with dummyIf(1) as nics:
            status, msg = self.vdsm_net.addNetwork(NETWORK_NAME, vlan=VLAN_ID,
                                                   bond=BONDING_NAME,
                                                   nics=nics,
                                                   opts={'mtu': MTU,
                                                         'bridged': bridged})
            vlan_name = '%s.%s' % (BONDING_NAME, VLAN_ID)

            self.assertEqual(status, SUCCESS, msg)
            self.assertEquals(MTU, self.vdsm_net.getMtu(NETWORK_NAME))
            self.assertEquals(MTU, self.vdsm_net.getMtu(vlan_name))
            self.assertEquals(MTU, self.vdsm_net.getMtu(BONDING_NAME))
            self.assertEquals(MTU, self.vdsm_net.getMtu(nics[0]))

            status, msg = self.vdsm_net.delNetwork(NETWORK_NAME)
            self.assertEqual(status, SUCCESS, msg)

    @cleanupNet
    @permutations([[True], [False]])
    @RequireDummyMod
    @ValidateRunningAsRoot
    def testTwiceAdd(self, bridged):
        with dummyIf(1) as nics:
            status, msg = self.vdsm_net.addNetwork(NETWORK_NAME, nics=nics,
                                                   opts={'bridged': bridged})
            self.assertEqual(status, SUCCESS, msg)

            status, msg = self.vdsm_net.addNetwork(NETWORK_NAME, nics=nics)
            self.assertEqual(status, errors.ERR_USED_BRIDGE, msg)

            status, msg = self.vdsm_net.delNetwork(NETWORK_NAME)
            self.assertEqual(status, SUCCESS, msg)

    @cleanupNet
    @permutations([[True], [False]])
    @RequireDummyMod
    @ValidateRunningAsRoot
    def testDelWithoutAdd(self, bridged):
        with dummyIf(1) as nics:
            status, msg = self.vdsm_net.delNetwork(NETWORK_NAME, nics=nics,
                                                   opts={'bridged': bridged})
            self.assertEqual(status, errors.ERR_BAD_BRIDGE, msg)

    @cleanupNet
    @permutations([[True], [False]])
    @RequireDummyMod
    @ValidateRunningAsRoot
    def testEditWithoutAdd(self, bridged):
        with dummyIf(1) as nics:
            status, msg = self.vdsm_net.editNetwork(NETWORK_NAME, NETWORK_NAME,
                                                    nics=nics,
                                                    opts={'bridged': bridged})
            self.assertEqual(status, errors.ERR_BAD_BRIDGE, msg)

    @cleanupNet
    @permutations([[True], [False]])
    @RequireDummyMod
    @ValidateRunningAsRoot
    @brokentest('This test is known to break until initscripts-9.03.41-1.el6 '
                'is released to fix https://bugzilla.redhat.com/1086897')
    def testSetupNetworksAddVlan(self, bridged):
        BRIDGE_OPTS = {'multicast_router': '0', 'multicast_snooping': '0'}
        formattedOpts = ' '.join(
            ['='.join(elem) for elem in BRIDGE_OPTS.items()])
        with dummyIf(1) as nics:
            nic, = nics
            attrs = {'vlan': VLAN_ID, 'nic': nic, 'bridged': bridged,
                     'custom': {'bridge_opts': formattedOpts}}
            status, msg = self.vdsm_net.setupNetworks({NETWORK_NAME:
                                                       attrs}, {}, NOCHK)

            self.assertEqual(status, SUCCESS, msg)
            self.assertNetworkExists(NETWORK_NAME, bridgeOpts=BRIDGE_OPTS)
            self.assertVlanExists('%s.%s' % (nic, VLAN_ID))

            status, msg = self.vdsm_net.setupNetworks({NETWORK_NAME:
                                                       dict(remove=True)},
                                                      {}, NOCHK)
            self.assertEqual(status, SUCCESS, msg)

    @cleanupNet
    def testSetupNetworksNicless(self):
        status, msg = self.vdsm_net.setupNetworks(
            {NETWORK_NAME: {'bridged': True, 'stp': True}}, {},
            NOCHK)
        self.assertEqual(status, SUCCESS, msg)
        self.assertNetworkExists(NETWORK_NAME)
        self.assertEqual(self.vdsm_net.netinfo.bridges[NETWORK_NAME]['stp'],
                         'on')

        status, msg = self.vdsm_net.setupNetworks(
            {NETWORK_NAME: dict(remove=True)}, {},
            NOCHK)
        self.assertEqual(status, SUCCESS, msg)

    @cleanupNet
    def testSetupNetworksNiclessBridgeless(self):
        status, msg = self.vdsm_net.setupNetworks(
            {NETWORK_NAME: {'bridged': False}}, {},
            NOCHK)
        self.assertEqual(status, errors.ERR_BAD_PARAMS, msg)

    @cleanupNet
    @RequireDummyMod
    @ValidateRunningAsRoot
    def testSetupNetworksConvertVlanNetBridgeness(self):
        "Convert a bridged networks to a bridgeless one and viceversa"

        def setupNetworkBridged(nic, bridged):
            networks = {NETWORK_NAME: dict(vlan=VLAN_ID,
                                           nic=nic, bridged=bridged)}
            status, msg = self.vdsm_net.setupNetworks(networks, {},
                                                      NOCHK)
            self.assertEqual(status, SUCCESS, msg)
            self.assertNetworkExists(NETWORK_NAME, bridged)

        with dummyIf(1) as nics:
            setupNetworkBridged(nics[0], True)
            setupNetworkBridged(nics[0], False)
            setupNetworkBridged(nics[0], True)

            status, msg = self.vdsm_net.setupNetworks({NETWORK_NAME:
                                                       dict(remove=True)},
                                                      {}, NOCHK)

        self.assertEqual(status, SUCCESS, msg)

    @permutations([[True], [False]])
    @RequireDummyMod
    @ValidateRunningAsRoot
    def testSetupNetworksAddManyVlans(self, bridged):
        VLAN_COUNT = 5
        NET_VLANS = [(NETWORK_NAME + str(index), str(index))
                     for index in range(VLAN_COUNT)]

        with dummyIf(1) as nics:
            nic, = nics
            networks = dict((vlan_net,
                             {'vlan': str(tag), 'nic': nic,
                              'bridged': bridged})
                            for vlan_net, tag in NET_VLANS)

            status, msg = self.vdsm_net.setupNetworks(networks, {}, NOCHK)
            self.assertEqual(status, SUCCESS, msg)

            for vlan_net, tag in NET_VLANS:
                self.assertNetworkExists(vlan_net, bridged)
                self.assertVlanExists(nic + '.' + tag)

            networks = dict((vlan_net, {'remove': True})
                            for vlan_net, _ in NET_VLANS)
            status, msg = self.vdsm_net.setupNetworks(networks, {}, NOCHK)

            self.assertEqual(status, SUCCESS, msg)

            for vlan_net, tag in NET_VLANS:
                self.assertNetworkDoesntExist(vlan_net)
                self.assertVlanDoesntExist(nic + '.' + tag)

    @cleanupNet
    @RequireDummyMod
    @ValidateRunningAsRoot
    def testSetupNetworksNetCompatibilityBondSingleBridge(self):
        with dummyIf(1) as nics:
            # Only single non-VLANed bridged network allowed
            d = dict(bonding=BONDING_NAME, bridged=True)
            status, msg = self.vdsm_net.setupNetworks({NETWORK_NAME: d},
                                                      {BONDING_NAME:
                                                       dict(nics=nics)},
                                                      NOCHK)
            self.assertEqual(status, SUCCESS, msg)
            self.assertNetworkExists(NETWORK_NAME, bridged=True)

            # Try to add additional bridgeless network, should fail
            netNameBridgeless = NETWORK_NAME + '-2'
            d['bridged'] = False
            status, msg = self.vdsm_net.setupNetworks({netNameBridgeless:
                                                       d}, {}, NOCHK)
            self.assertTrue(status != SUCCESS, msg)

            # Try to add additional bridged network, should fail
            netNameBridged = NETWORK_NAME + '-3'
            d['bridged'] = True
            status, msg = self.vdsm_net.setupNetworks({netNameBridged: d},
                                                      {}, NOCHK)
            self.assertTrue(status != SUCCESS, msg)

            # Try to add additional VLANed bridgeless network, should fail
            netNameVlanBridgeless = NETWORK_NAME + '-4'
            networks = dict(netNameVlanBridgeless={'bonding': BONDING_NAME,
                                                   'vlan': '100',
                                                   'bridged': False})
            status, msg = self.vdsm_net.setupNetworks(networks, {}, NOCHK)
            self.assertTrue(status != SUCCESS, msg)

            # Try to add additional VLANed bridged network, should fail
            netNameVlanBridged = NETWORK_NAME + '-5'
            networks['vlan'] = '200'
            networks['bridged'] = True
            status, msg = self.vdsm_net.setupNetworks({netNameVlanBridged:
                                                       networks}, {}, NOCHK)
            self.assertTrue(status != SUCCESS, msg)

            self.assertNetworkDoesntExist(netNameBridgeless)
            self.assertNetworkDoesntExist(netNameBridged)
            self.assertNetworkDoesntExist(netNameVlanBridgeless)
            self.assertNetworkDoesntExist(netNameVlanBridged)

            # Clean all
            status, msg = self.vdsm_net.setupNetworks({NETWORK_NAME:
                                                       dict(remove=True)},
                                                      {BONDING_NAME:
                                                       dict(remove=True)},
                                                      NOCHK)
            self.assertEquals(status, SUCCESS, msg)

            self.assertNetworkDoesntExist(NETWORK_NAME)
            self.assertBondDoesntExist(BONDING_NAME, nics)

    @cleanupNet
    @RequireDummyMod
    @ValidateRunningAsRoot
    def testSetupNetworksNetCompatibilityBondSingleBridgeless(self):
        with dummyIf(1) as nics:
            # Multiple VLANed networks (bridged/bridgeless) with only one
            # non-VLANed bridgeless network permited
            d = dict(bonding=BONDING_NAME, bridged=False)
            status, msg = self.vdsm_net.setupNetworks({NETWORK_NAME: d},
                                                      {BONDING_NAME:
                                                       dict(nics=nics)},
                                                      NOCHK)
            self.assertEqual(status, SUCCESS, msg)
            self.assertNetworkExists(NETWORK_NAME, bridged=False)

            # Try to add additional bridgeless network, should fail
            netNameBridgeless = NETWORK_NAME + '-2'
            status, msg = self.vdsm_net.setupNetworks({netNameBridgeless:
                                                       d}, {}, NOCHK)
            self.assertTrue(status != SUCCESS, msg)

            # Try to add additional bridged network, should fail
            netNameBridged = NETWORK_NAME + '-3'
            d['bridged'] = True
            status, msg = self.vdsm_net.setupNetworks({netNameBridged: d},
                                                      {}, NOCHK)
            self.assertTrue(status != SUCCESS, msg)

            # Try to add additional VLANed bridgeless network,
            # should succeed
            netNameVlanBridgeless = NETWORK_NAME + '-4'
            d['vlan'], d['bridged'] = '100', False
            networks = {netNameVlanBridgeless: d}
            status, msg = self.vdsm_net.setupNetworks(networks, {}, NOCHK)

            self.assertEqual(status, SUCCESS, msg)

            # Try to add additional VLANed bridged network, should succeed
            netNameVlanBridged = NETWORK_NAME + '-5'
            d['vlan'], d['bridged'] = '200', True
            status, msg = self.vdsm_net.setupNetworks({netNameVlanBridged:
                                                       d}, {}, NOCHK)
            self.assertEqual(status, SUCCESS, msg)

            self.assertNetworkDoesntExist(netNameBridgeless)
            self.assertNetworkDoesntExist(netNameBridged)

            self.assertNetworkExists(netNameVlanBridgeless)
            self.assertNetworkExists(netNameVlanBridged)

            # Clean all
            r = dict(remove=True)
            networks = {NETWORK_NAME: r,
                        netNameVlanBridgeless: r,
                        netNameVlanBridged: r}
            status, msg = self.vdsm_net.setupNetworks(networks,
                                                      {BONDING_NAME: r},
                                                      NOCHK)

            self.assertEqual(status, SUCCESS, msg)

            self.assertNetworkDoesntExist(NETWORK_NAME)
            self.assertNetworkDoesntExist(netNameVlanBridgeless)
            self.assertNetworkDoesntExist(netNameVlanBridged)
            self.assertBondDoesntExist(BONDING_NAME, nics)

    @cleanupNet
    @RequireDummyMod
    @ValidateRunningAsRoot
    def testSetupNetworksNetCompatibilityNicSingleBridge(self):
        with dummyIf(1) as nics:
            nic, = nics
            # Only single non-VLANed bridged network allowed
            networks = {NETWORK_NAME: dict(nic=nic, bridged=True)}
            status, msg = self.vdsm_net.setupNetworks(networks, {}, NOCHK)

            self.assertEquals(status, SUCCESS, msg)
            self.assertNetworkExists(NETWORK_NAME, bridged=True)

            # Try to add additional bridgeless network, should fail
            netNameBridgeless = NETWORK_NAME + '-2'
            networks = {netNameBridgeless: dict(nic=nic, bridged=False)}
            status, msg = self.vdsm_net.setupNetworks(networks, {}, NOCHK)

            self.assertTrue(status != SUCCESS, msg)

            # Try to add additional bridged network, should fail
            netNameBridged = NETWORK_NAME + '-3'
            networks = {netNameBridged: dict(nic=nic, bridged=True)}
            status, msg = self.vdsm_net.setupNetworks(networks, {}, NOCHK)

            self.assertTrue(status != SUCCESS, msg)

            # Try to add additional VLANed bridgeless network, should fail
            netNameVlanBridgeless = NETWORK_NAME + '-4'
            networks = {netNameVlanBridgeless: dict(nic=nic, vlan='100',
                                                    bridged=False)}
            status, msg = self.vdsm_net.setupNetworks(networks, {}, NOCHK)

            self.assertTrue(status != SUCCESS, msg)

            # Try to add additional VLANed bridged network, should fail
            netNameVlanBridged = NETWORK_NAME + '-5'
            networks = {netNameVlanBridged: dict(nic=nic, vlan='200',
                                                 bridged=True)}
            status, msg = self.vdsm_net.setupNetworks(networks, {}, NOCHK)

            self.assertTrue(status != SUCCESS, msg)

            self.assertNetworkDoesntExist(netNameBridgeless)
            self.assertNetworkDoesntExist(netNameBridged)
            self.assertNetworkDoesntExist(netNameVlanBridgeless)
            self.assertNetworkDoesntExist(netNameBridged)

            # Clean all
            status, msg = self.vdsm_net.setupNetworks({NETWORK_NAME:
                                                       dict(remove=True)},
                                                      {}, NOCHK)
            self.assertEquals(status, SUCCESS, msg)
            self.assertNetworkDoesntExist(NETWORK_NAME)

    @cleanupNet
    @RequireDummyMod
    @ValidateRunningAsRoot
    def testSetupNetworksNetCompatibilityNicSingleBridgeless(self):
        with dummyIf(1) as nics:
            nic, = nics
            # Multiple VLANed networks (bridged/bridgeless) with only one
            # non-VLANed bridgeless network permited
            networks = {NETWORK_NAME: dict(nic=nic, bridged=False)}
            status, msg = self.vdsm_net.setupNetworks(networks, {}, NOCHK)

            self.assertEquals(status, SUCCESS, msg)
            self.assertNetworkExists(NETWORK_NAME, bridged=False)

            # Try to add additional bridgeless network, should fail
            netNameBridgeless = NETWORK_NAME + '-2'
            networks = {netNameBridgeless: dict(nic=nic, bridged=False)}
            status, msg = self.vdsm_net.setupNetworks(networks, {}, NOCHK)

            self.assertTrue(status != SUCCESS, msg)

            # Try to add additional bridged network, should fail
            netNameBridged = NETWORK_NAME + '-3'
            networks = {netNameBridged: dict(nic=nic, bridged=True)}
            status, msg = self.vdsm_net.setupNetworks(networks, {}, NOCHK)

            self.assertTrue(status != SUCCESS, msg)

            # Try to add additional VLANed bridgeless network,
            # should succeed
            netNameVlanBridgeless = NETWORK_NAME + '-4'
            networks = {netNameVlanBridgeless: dict(nic=nic, vlan='100',
                                                    bridged=False)}
            status, msg = self.vdsm_net.setupNetworks(networks, {}, NOCHK)

            self.assertEquals(status, SUCCESS, msg)

            # Try to add additional VLANed bridged network, should succeed
            netNameVlanBridged = NETWORK_NAME + '-5'
            networks = {netNameVlanBridged: dict(nic=nic, vlan='200',
                                                 bridged=True)}
            status, msg = self.vdsm_net.setupNetworks(networks, {}, NOCHK)

            self.assertEquals(status, SUCCESS, msg)

            self.assertNetworkDoesntExist(netNameBridgeless)
            self.assertNetworkDoesntExist(netNameBridged)
            self.assertNetworkExists(netNameVlanBridgeless)
            self.assertNetworkExists(netNameVlanBridged, bridged=True)

            # Clean all
            networks = {NETWORK_NAME: dict(remove=True),
                        netNameVlanBridgeless: dict(remove=True),
                        netNameVlanBridged: dict(remove=True)}
            status, msg = self.vdsm_net.setupNetworks(networks, {}, NOCHK)

            self.assertEqual(status, SUCCESS, msg)

            self.assertNetworkDoesntExist(NETWORK_NAME)
            self.assertNetworkDoesntExist(netNameVlanBridgeless)
            self.assertNetworkDoesntExist(netNameVlanBridged)

    @cleanupNet
    @permutations([[True], [False]])
    @RequireDummyMod
    @ValidateRunningAsRoot
    def testSetupNetworksAddNetworkToNicAfterBondResizing(self, bridged):
        with dummyIf(3) as nics:
            networks = {NETWORK_NAME: dict(bonding=BONDING_NAME,
                                           bridged=bridged)}
            status, msg = self.vdsm_net.setupNetworks(networks,
                                                      {BONDING_NAME:
                                                       dict(nics=nics)},
                                                      NOCHK)

            self.assertEquals(status, SUCCESS, msg)

            self.assertNetworkExists(NETWORK_NAME, bridged=bridged)
            self.assertBondExists(BONDING_NAME, nics)

            # Reduce bond size and create Network on detached NIC
            with nonChangingOperstate(BONDING_NAME):
                netName = NETWORK_NAME + '-2'
                networks = {netName: dict(nic=nics[0],
                                          bridged=bridged)}
                bondings = {BONDING_NAME: dict(nics=nics[1:3])}
                status, msg = self.vdsm_net.setupNetworks(networks,
                                                          bondings, NOCHK)

                self.assertEquals(status, SUCCESS, msg)

                self.assertNetworkExists(NETWORK_NAME, bridged=bridged)
                self.assertNetworkExists(netName, bridged=bridged)
                self.assertBondExists(BONDING_NAME, nics[1:3])

            # Clean up
            networks = {NETWORK_NAME: dict(remove=True),
                        netName: dict(remove=True)}
            bondings = {BONDING_NAME: dict(remove=True)}
            status, msg = self.vdsm_net.setupNetworks(networks,
                                                      bondings, NOCHK)
            self.assertEquals(status, SUCCESS, msg)

    @cleanupNet
    @permutations([[True], [False]])
    @RequireDummyMod
    @ValidateRunningAsRoot
    def testSetupNetworksMtus(self, bridged):
        JUMBO = '9000'
        MIDI = '4000'

        with dummyIf(3) as nics:
            networks = {NETWORK_NAME + '1':
                        dict(bonding=BONDING_NAME, bridged=bridged,
                             vlan='100'),
                        NETWORK_NAME + '2':
                        dict(bonding=BONDING_NAME, bridged=bridged,
                             vlan='200', mtu=MIDI)
                        }
            bondings = {BONDING_NAME: dict(nics=nics[:2])}
            status, msg = self.vdsm_net.setupNetworks(networks, bondings,
                                                      NOCHK)

            self.assertEquals(status, SUCCESS, msg)

            self.assertMtu(MIDI, NETWORK_NAME + '2', BONDING_NAME, nics[0],
                           nics[1])

            network = {NETWORK_NAME + '3':
                       dict(bonding=BONDING_NAME, vlan='300', mtu=JUMBO,
                            bridged=bridged)}
            status, msg = self.vdsm_net.setupNetworks(network, {}, NOCHK)

            self.assertEquals(status, SUCCESS, msg)

            self.assertNetworkExists(NETWORK_NAME + '3', bridged=bridged)
            self.assertMtu(JUMBO, NETWORK_NAME + '3', BONDING_NAME, nics[0],
                           nics[1])

            status, msg = self.vdsm_net.setupNetworks({NETWORK_NAME + '3':
                                                       dict(remove=True)},
                                                      {}, NOCHK)

            self.assertEquals(status, SUCCESS, msg)

            self.assertMtu(MIDI, NETWORK_NAME + '2', BONDING_NAME, nics[0],
                           nics[1])

            # Keep last custom MTU on the interfaces
            status, msg = self.vdsm_net.setupNetworks({NETWORK_NAME + '2':
                                                       dict(remove=True)},
                                                      {}, NOCHK)

            self.assertEquals(status, SUCCESS, msg)

            self.assertMtu(MIDI, BONDING_NAME, nics[0], nics[1])

            # Add additional nic to the bond
            status, msg = self.vdsm_net.setupNetworks({}, {BONDING_NAME:
                                                      dict(nics=nics)}, NOCHK)

            self.assertEquals(status, SUCCESS, msg)

            self.assertMtu(MIDI, BONDING_NAME, nics[0], nics[1], nics[2])

            status, msg = self.vdsm_net.setupNetworks({NETWORK_NAME + '1':
                                                       dict(remove=True)},
                                                      {BONDING_NAME:
                                                       dict(remove=True)},
                                                      NOCHK)

            self.assertEquals(status, SUCCESS, msg)

    @cleanupNet
    @permutations([[True], [False]])
    @RequireDummyMod
    @ValidateRunningAsRoot
    def testSetupNetworksAddNetworkToNicAfterBondBreaking(self, bridged):
        with dummyIf(2) as nics:
            networks = {NETWORK_NAME: dict(bonding=BONDING_NAME,
                                           bridged=bridged)}
            status, msg = self.vdsm_net.setupNetworks(networks,
                                                      {BONDING_NAME:
                                                       dict(nics=nics)},
                                                      NOCHK)
            self.assertEquals(status, SUCCESS, msg)

            self.assertNetworkExists(NETWORK_NAME, bridged=bridged)
            self.assertBondExists(BONDING_NAME, nics)

            # Break the bond and create Network on detached NIC
            networks = {NETWORK_NAME: dict(nic=nics[0], bridged=bridged)}
            status, msg = self.vdsm_net.setupNetworks(networks,
                                                      {BONDING_NAME:
                                                       dict(remove=True)},
                                                      NOCHK)
            self.assertEquals(status, SUCCESS, msg)

            self.assertNetworkExists(NETWORK_NAME, bridged=bridged)
            self.assertBondDoesntExist(BONDING_NAME, nics)

            status, msg = self.vdsm_net.setupNetworks({NETWORK_NAME:
                                                       dict(remove=True)},
                                                      {}, NOCHK)
            self.assertEquals(status, SUCCESS, msg)

    @cleanupNet
    @permutations([[True], [False]])
    @RequireDummyMod
    @ValidateRunningAsRoot
    def testSetupNetworksKeepNetworkOnBondAfterBondResizing(self, bridged):
        with dummyIf(3) as nics:
            networks = {NETWORK_NAME: dict(bonding=BONDING_NAME,
                                           bridged=bridged)}
            bondings = {BONDING_NAME: dict(nics=nics[:2])}
            status, msg = self.vdsm_net.setupNetworks(networks,
                                                      bondings, NOCHK)
            self.assertEquals(status, SUCCESS, msg)

            self.assertNetworkExists(NETWORK_NAME, bridged=bridged)
            self.assertBondExists(BONDING_NAME, nics[:2])

            # Increase bond size
            with nonChangingOperstate(BONDING_NAME):
                status, msg = self.vdsm_net.setupNetworks(
                    {}, {BONDING_NAME: dict(nics=nics)}, NOCHK)

                self.assertEquals(status, SUCCESS, msg)

                self.assertNetworkExists(NETWORK_NAME, bridged=bridged)
                self.assertBondExists(BONDING_NAME, nics)

            status, msg = self.vdsm_net.setupNetworks({NETWORK_NAME:
                                                       dict(remove=True)},
                                                      {BONDING_NAME:
                                                       dict(remove=True)},
                                                      NOCHK)
            self.assertEquals(status, SUCCESS, msg)

    def _createBondedNetAndCheck(self, netNum, bondDict, bridged,
                                 **networkOpts):
        netName = NETWORK_NAME + str(netNum)
        networks = {netName: dict(bonding=BONDING_NAME, bridged=bridged,
                                  vlan=str(int(VLAN_ID) + netNum),
                                  **networkOpts)}
        status, msg = self.vdsm_net.setupNetworks(networks,
                                                  {BONDING_NAME: bondDict},
                                                  {})
        self.assertEquals(status, SUCCESS, msg)
        self.assertNetworkExists(netName, bridged=bridged)
        self.assertBondExists(BONDING_NAME, bondDict['nics'],
                              bondDict.get('options'))
        if 'mtu' in networkOpts:
            self.assertMtu(networkOpts['mtu'], netName)

    @cleanupNet
    @permutations([[True], [False]])
    @RequireDummyMod
    @ValidateRunningAsRoot
    def testSetupNetworksStableBond(self, bridged):
        with dummyIf(3) as nics:
            with self.vdsm_net.pinger():
                # Add initial vlanned net over bond
                self._createBondedNetAndCheck(0, {'nics': nics[:2],
                                              'options': 'mode=3 miimon=250'},
                                              bridged)

                with nonChangingOperstate(BONDING_NAME):
                    # Add additional vlanned net over the bond
                    self._createBondedNetAndCheck(1,
                                                  {'nics': nics[:2],
                                                   'options':
                                                   'mode=3 miimon=250'},
                                                  bridged)
                    # Add additional vlanned net over the increasing bond
                    self._createBondedNetAndCheck(2,
                                                  {'nics': nics,
                                                   'options':
                                                   'mode=3 miimon=250'},
                                                  bridged)
                    # Add additional vlanned net over the changing bond
                    self._createBondedNetAndCheck(3,
                                                  {'nics': nics[1:],
                                                   'options':
                                                   'mode=3 miimon=250'},
                                                  bridged)

                # Add a network changing bond options
                with self.assertRaises(OperStateChangedError):
                    with nonChangingOperstate(BONDING_NAME):
                        self._createBondedNetAndCheck(4,
                                                      {'nics': nics[1:],
                                                       'options':
                                                       'mode=4 miimon=9'},
                                                      bridged)

                # cleanup
                networks = dict((NETWORK_NAME + str(num), {'remove': True}) for
                                num in range(5))
                status, msg = self.vdsm_net.setupNetworks(networks,
                                                          {BONDING_NAME:
                                                           dict(remove=True)},
                                                          {})
                self.assertEquals(status, SUCCESS, msg)

    @cleanupNet
    @permutations([[True], [False]])
    @RequireDummyMod
    @ValidateRunningAsRoot
    def testSetupNetworksMultiMTUsOverBond(self, bridged):
        with dummyIf(2) as nics:
            with self.vdsm_net.pinger():
                # Add initial vlanned net over bond
                self._createBondedNetAndCheck(0, {'nics': nics}, bridged,
                                              mtu='1500')
                self.assertEquals('1500',
                                  self.vdsm_net.getMtu(BONDING_NAME))

                with nonChangingOperstate(BONDING_NAME):
                    # Add a network with MTU smaller than existing network
                    self._createBondedNetAndCheck(1, {'nics': nics},
                                                  bridged, mtu='1400')
                    self.assertEquals('1500',
                                      self.vdsm_net.getMtu(BONDING_NAME))

                    # Add a network with MTU bigger than existing network
                    self._createBondedNetAndCheck(2, {'nics': nics},
                                                  bridged, mtu='1600')
                    self.assertEquals('1600',
                                      self.vdsm_net.getMtu(BONDING_NAME))

                # cleanup
                networks = dict((NETWORK_NAME + str(num), {'remove': True}) for
                                num in range(3))
                status, msg = self.vdsm_net.setupNetworks(networks,
                                                          {BONDING_NAME:
                                                           dict(remove=True)},
                                                          {})
                self.assertEquals(status, SUCCESS, msg)

    def _createVlanedNetOverNicAndCheck(self, netNum, bridged, **networkOpts):
        netName = NETWORK_NAME + str(netNum)
        networks = {netName: dict(bridged=bridged,
                                  vlan=str(int(VLAN_ID) + netNum),
                                  **networkOpts)}
        status, msg = self.vdsm_net.setupNetworks(networks, {}, {})
        self.assertEquals(status, SUCCESS, msg)
        self.assertNetworkExists(netName, bridged=bridged)
        if 'mtu' in networkOpts:
            self.assertMtu(networkOpts['mtu'], netName)

    @cleanupNet
    @permutations([[True], [False]])
    @RequireDummyMod
    @ValidateRunningAsRoot
    def testSetupNetworksMultiMTUsOverNic(self, bridged):
        with dummyIf(1) as nics:
            nic, = nics
            with self.vdsm_net.pinger():
                # Add initial vlanned net over bond
                self._createVlanedNetOverNicAndCheck(0, bridged, nic=nic,
                                                     mtu='1500')
                self.assertEquals('1500', self.vdsm_net.getMtu(nic))

                # Add a network with MTU smaller than existing network
                self._createVlanedNetOverNicAndCheck(1, bridged, nic=nic,
                                                     mtu='1400')
                self.assertEquals('1500', self.vdsm_net.getMtu(nic))

                # Add a network with MTU bigger than existing network
                self._createVlanedNetOverNicAndCheck(2, bridged, nic=nic,
                                                     mtu='1600')
                self.assertEquals('1600', self.vdsm_net.getMtu(nic))

                # cleanup
                networks = dict((NETWORK_NAME + str(num), {'remove': True}) for
                                num in range(3))
                status, msg = self.vdsm_net.setupNetworks(networks, {}, {})
                self.assertEquals(status, SUCCESS, msg)

    @permutations([[True], [False]])
    def testSetupNetworksAddBadParams(self, bridged):
        attrs = dict(vlan=VLAN_ID, bridged=bridged)
        status, msg = self.vdsm_net.setupNetworks({NETWORK_NAME: attrs},
                                                  {}, {})

        self.assertNotEqual(status, SUCCESS, msg)

    @cleanupNet
    @RequireDummyMod
    @ValidateRunningAsRoot
    def testDelNetworkBondAccumulation(self):
        with dummyIf(1) as nics:
            for bigBond in ('bond555', 'bond666', 'bond777'):
                status, msg = self.vdsm_net.addNetwork(NETWORK_NAME, VLAN_ID,
                                                       bigBond, nics)

                self.assertEqual(status, SUCCESS, msg)

                self.assertBondExists(bigBond, nics)

                status, msg = self.vdsm_net.delNetwork(NETWORK_NAME)

                self.assertEqual(status, SUCCESS, msg)

                self.assertBondDoesntExist(bigBond, nics)

    @cleanupNet
    @permutations([[True], [False]])
    @RequireDummyMod
    @ValidateRunningAsRoot
    def testSetupNetworksResizeBond(self, bridged):
        with dummyIf(3) as nics:
            with self.vdsm_net.pinger():
                bondings = {BONDING_NAME: dict(nics=nics[:1],
                                               bridged=bridged)}
                status, msg = self.vdsm_net.setupNetworks({}, bondings, {})

                self.assertEquals(status, SUCCESS, msg)

                self.assertBondExists(BONDING_NAME, nics=nics[:1])

                # Increase bond size
                bondings[BONDING_NAME]['nics'] = nics
                status, msg = self.vdsm_net.setupNetworks({}, bondings, {})

                self.assertEquals(status, SUCCESS, msg)

                self.assertBondExists(BONDING_NAME, nics)

                # Reduce bond size
                REQMODE_BROADCAST = '3'
                bondings[BONDING_NAME]['nics'] = nics[:2]
                bondings[BONDING_NAME]['options'] = ('mode=%s' %
                                                     REQMODE_BROADCAST)
                status, msg = self.vdsm_net.setupNetworks({}, bondings, {})

                self.assertEquals(status, SUCCESS, msg)

                self.assertBondExists(BONDING_NAME, nics[:2],
                                      bondings[BONDING_NAME]['options'])

                bondings = {BONDING_NAME: dict(remove=True)}
                status, msg = self.vdsm_net.setupNetworks({}, bondings, {})

                self.assertEquals(status, SUCCESS, msg)

    @cleanupNet
    @permutations([[True], [False]])
    @RequireDummyMod
    @ValidateRunningAsRoot
    def testBondHwAddress(self, bridged=True):
        """
        Test that bond mac address is independent of the ordering of nics arg
        """
        with dummyIf(2) as nics:
            def _getBondHwAddress(*nics):
                status, msg = self.vdsm_net.addNetwork(NETWORK_NAME,
                                                       bond=BONDING_NAME,
                                                       nics=nics,
                                                       opts={'bridged':
                                                             bridged})
                self.assertEquals(status, SUCCESS, msg)
                hwaddr = self.vdsm_net.netinfo.bondings[BONDING_NAME]['hwaddr']

                status, msg = self.vdsm_net.delNetwork(NETWORK_NAME)
                self.assertEquals(status, SUCCESS, msg)

                return hwaddr

            macAddress1 = _getBondHwAddress(nics[0], nics[1])
            macAddress2 = _getBondHwAddress(nics[1], nics[0])
            self.assertEquals(macAddress1, macAddress2)

    @cleanupNet
    @permutations([[True], [False]])
    @RequireDummyMod
    @ValidateRunningAsRoot
    def testSafeNetworkConfig(self, bridged):
        """
        Checks that setSafeNetworkConfig saves
        the configuration between restart.
        """
        with dummyIf(1) as nics:
            status, msg = self.vdsm_net.addNetwork(NETWORK_NAME, nics=nics,
                                                   opts={'bridged': bridged})
            self.assertEquals(status, SUCCESS, msg)

            self.assertNetworkExists(NETWORK_NAME, bridged=bridged)

            self.vdsm_net.save_config()

            self.vdsm_net.restoreNetConfig()

            self.assertNetworkExists(NETWORK_NAME, bridged=bridged)

            status, msg = self.vdsm_net.delNetwork(NETWORK_NAME)
            self.assertEquals(status, SUCCESS, msg)

            self.vdsm_net.save_config()

    @cleanupNet
    @permutations([[True], [False]])
    @RequireDummyMod
    @ValidateRunningAsRoot
    def testVolatileConfig(self, bridged):
        """
        Checks that the network doesn't persist over restart
        """
        with dummyIf(1) as nics:
            status, msg = self.vdsm_net.addNetwork(NETWORK_NAME, nics=nics,
                                                   opts={'bridged':
                                                         bridged})
            self.assertEquals(status, SUCCESS, msg)

            self.assertNetworkExists(NETWORK_NAME, bridged=bridged)

            self.vdsm_net.restoreNetConfig()

            self.assertNetworkDoesntExist(NETWORK_NAME)

    @cleanupRules
    @RequireDummyMod
    @ValidateRunningAsRoot
    def testRuleExists(self):
        with dummyIf(1) as nics:
            nic, = nics
            dummy.setIP(nic, IP_ADDRESS, IP_CIDR)
            try:
                dummy.setLinkUp(nic)

                rules = [Rule(source=IP_NETWORK_AND_CIDR, table=IP_TABLE),
                         Rule(destination=IP_NETWORK_AND_CIDR, table=IP_TABLE,
                              srcDevice=nic)]
                for rule in rules:
                    self.assertFalse(ruleExists(rule))
                    ruleAdd(rule)
                    self.assertTrue(ruleExists(rule))
                    ruleDel(rule)
                    self.assertFalse(ruleExists(rule))
            finally:
                addrFlush(nic)

    @RequireDummyMod
    @ValidateRunningAsRoot
    def testRouteExists(self):
        with dummyIf(1) as nics:
            nic, = nics
            dummy.setIP(nic, IP_ADDRESS, IP_CIDR)
            try:
                dummy.setLinkUp(nic)

                routes = [Route(network='0.0.0.0/0', via=IP_GATEWAY,
                                device=nic, table=IP_TABLE),
                          Route(network=IP_NETWORK_AND_CIDR,
                                via=IP_ADDRESS, device=nic, table=IP_TABLE)]
                for route in routes:
                    self.assertFalse(routeExists(route))
                    routeAdd(route)
                    self.assertTrue(routeExists(route))
                    routeDel(route)
                    self.assertFalse(routeExists(route))
            finally:
                addrFlush(nic)

    @permutations([[True], [False]])
    @cleanupNet
    @RequireDummyMod
    @ValidateRunningAsRoot
    def testStaticSourceRouting(self, bridged=True):
        with dummyIf(1) as nics:
            status, msg = self.vdsm_net.setupNetworks(
                {NETWORK_NAME:
                    {'nic': nics[0], 'bridged': bridged, 'ipaddr': IP_ADDRESS,
                     'netmask': prefix2netmask(int(IP_CIDR)),
                     'gateway': IP_GATEWAY}},
                {}, NOCHK)
            self.assertEqual(status, SUCCESS, msg)
            self.assertNetworkExists(NETWORK_NAME, bridged)

            deviceName = NETWORK_NAME if bridged else nics[0]

            # Assert that routes and rules exist
            routes = [Route(network='0.0.0.0/0', via=IP_GATEWAY,
                            device=deviceName, table=IP_TABLE),
                      Route(network=IP_NETWORK_AND_CIDR,
                            via=IP_ADDRESS, device=deviceName,
                            table=IP_TABLE)]
            rules = [Rule(source=IP_NETWORK_AND_CIDR, table=IP_TABLE),
                     Rule(destination=IP_NETWORK_AND_CIDR, table=IP_TABLE,
                          srcDevice=deviceName)]

            for route in routes:
                self.assertTrue(routeExists(route))
            for rule in rules:
                self.assertTrue(ruleExists(rule))

            status, msg = self.vdsm_net.setupNetworks(
                {NETWORK_NAME: {'remove': True}},
                {}, NOCHK)
            self.assertEqual(status, SUCCESS, msg)

            # Assert that routes and rules don't exist
            for route in routes:
                self.assertFalse(routeExists(route))
            for rule in rules:
                self.assertFalse(ruleExists(rule))

    @cleanupNet
    @RequireDummyMod
    @ValidateRunningAsRoot
    def testAddVlanedBridgeless(self):
        # BZ# 980174
        vlan_name = 'vlan_net'
        with dummyIf(1) as nics:
            nic, = nics
            # net NETWORK_NAME has bootproto:none because we can't use dhcp
            # on dummyIf
            networks = {NETWORK_NAME: {'nic': nic, 'bridged': False,
                                       'bootproto': 'none'},
                        vlan_name: {'nic': nic, 'bridged': True,
                                    'vlan': VLAN_ID, 'bootproto': 'none'}}
            with self.vdsm_net.pinger():
                status, msg = self.vdsm_net.setupNetworks(
                    {NETWORK_NAME: networks[NETWORK_NAME]}, {}, {})
                self.assertEqual(status, SUCCESS, msg)
                self.assertNetworkExists(NETWORK_NAME)
                status, msg, info = self.vdsm_net.getVdsCapabilities()
                self.assertIn('BOOTPROTO', info['nics'][nic]['cfg'])
                bootproto = info['nics'][nic]['cfg']['BOOTPROTO']
                self.assertEqual(bootproto, 'none')

                status, msg = self.vdsm_net.setupNetworks(
                    {vlan_name: networks[vlan_name]}, {}, {})
                self.assertEqual(status, SUCCESS, msg)
                self.assertNetworkExists(vlan_name)
                status, msg, info = self.vdsm_net.getVdsCapabilities()
                self.assertIn('BOOTPROTO', info['nics'][nic]['cfg'])
                bootproto = info['nics'][nic]['cfg']['BOOTPROTO']
                self.assertEqual(bootproto, 'none')

                # network should be fine even after second addition of vlan
                status, msg = self.vdsm_net.setupNetworks(
                    {vlan_name: networks[vlan_name]}, {}, {})
                self.assertEqual(status, SUCCESS, msg)
                status, msg, info = self.vdsm_net.getVdsCapabilities()
                self.assertIn('BOOTPROTO', info['nics'][nic]['cfg'])
                bootproto = info['nics'][nic]['cfg']['BOOTPROTO']
                self.assertEqual(bootproto, 'none')

                delete_networks = {NETWORK_NAME: {'remove': True},
                                   vlan_name: {'remove': True}}
                status, msg = self.vdsm_net.setupNetworks(delete_networks,
                                                          {}, {})
                self.assertEqual(status, SUCCESS, msg)

    @cleanupNet
    @RequireDummyMod
    @ValidateRunningAsRoot
    def testAddVlanedBridgeless_oneCommand(self):
        vlan_name = 'vlan_net'
        with dummyIf(1) as nics:
            nic, = nics
            # net NETWORK_NAME has bootproto:none because we can't use dhcp
            # on dummyIf
            networks = {NETWORK_NAME: {'nic': nic, 'bridged': False,
                                       'bootproto': 'none'},
                        vlan_name: {'nic': nic, 'bridged': True,
                                    'vlan': VLAN_ID, 'bootproto': 'none'}}
            with self.vdsm_net.pinger():
                status, msg = self.vdsm_net.setupNetworks(networks, {}, {})
                self.assertEqual(status, SUCCESS, msg)
                self.assertNetworkExists(NETWORK_NAME)
                self.assertNetworkExists(vlan_name)
                status, msg, info = self.vdsm_net.getVdsCapabilities()
                self.assertIn('BOOTPROTO', info['nics'][nic]['cfg'])
                bootproto = info['nics'][nic]['cfg']['BOOTPROTO']
                self.assertEqual(bootproto, 'none')

                delete_networks = {NETWORK_NAME: {'remove': True},
                                   vlan_name: {'remove': True}}
                status, msg = self.vdsm_net.setupNetworks(delete_networks,
                                                          {}, {})
                self.assertEqual(status, SUCCESS, msg)

    @cleanupNet
    @RequireDummyMod
    @ValidateRunningAsRoot
    @ValidatesHook('before_network_setup', 'testBeforeNetworkSetup.py', True,
                   "#!/usr/bin/env python\n"
                   "import json\n"
                   "import os\n"
                   "\n"
                   "# get the filename where settings are stored\n"
                   "hook_data = os.environ['_hook_json']\n"
                   "network_config = None\n"
                   "with open(hook_data, 'r') as network_config_file:\n"
                   "    network_config = json.load(network_config_file)\n"
                   "\n"
                   "network = network_config['request']['networks']['" +
                   NETWORK_NAME + "']\n"
                   "assert network['custom'] == " + str(CUSTOM_PROPS) + "\n"
                   "\n"
                   "# setup an output config file\n"
                   "cookie_file = open('%(cookiefile)s','w')\n"
                   "cookie_file.write(str(network_config) + '\\n')\n"
                   "network['bridged'] = True\n"
                   "\n"
                   "# output modified config back to the hook_data file\n"
                   "with open(hook_data, 'w') as network_config_file:\n"
                   "    json.dump(network_config, network_config_file)\n"
                   )
    def testBeforeNetworkSetupHook(self, hook_cookiefile):
        with dummyIf(1) as nics:
            nic, = nics
            # Test that the custom network properties reach the hook
            networks = {NETWORK_NAME: {'nic': nic, 'bridged': False,
                                       'custom': CUSTOM_PROPS,
                                       'bootproto': 'none'}}
            with self.vdsm_net.pinger():
                status, msg = self.vdsm_net.setupNetworks(networks, {}, {})
                self.assertEqual(status, SUCCESS, msg)
                self.assertNetworkExists(NETWORK_NAME, bridged=True)

                self.assertTrue(os.path.isfile(hook_cookiefile))

                delete_networks = {NETWORK_NAME: {'remove': True}}
                self.vdsm_net.setupNetworks(delete_networks,
                                            {}, {})

    @cleanupNet
    @RequireDummyMod
    @ValidateRunningAsRoot
    @ValidatesHook('after_network_setup', 'testAfterNetworkSetup.sh', True,
                   "#!/bin/sh\n"
                   "cat $_hook_json > %(cookiefile)s\n"
                   )
    def testAfterNetworkSetupHook(self, hook_cookiefile):
        with dummyIf(1) as nics:
            nic, = nics
            networks = {NETWORK_NAME: {'nic': nic, 'bridged': False,
                                       'bootproto': 'none'}}
            with self.vdsm_net.pinger():
                self.vdsm_net.setupNetworks(networks, {}, {})

                self.assertTrue(os.path.isfile(hook_cookiefile))

                with open(hook_cookiefile, 'r') as cookie_file:
                    network_config = json.load(cookie_file)
                    self.assertIn('networks', network_config['request'])
                    self.assertIn('bondings', network_config['request'])
                    self.assertIn(NETWORK_NAME,
                                  network_config['request']['networks'])

                    # when unified persistence is enabled we also provide
                    # the current configuration to the hook

                    if 'current' in network_config:
                        self.assertTrue(NETWORK_NAME in
                                        network_config['current']['networks'])

                delete_networks = {NETWORK_NAME: {'remove': True}}
                self.vdsm_net.setupNetworks(delete_networks,
                                            {}, {})

    @cleanupNet
    @RequireDummyMod
    @ValidateRunningAsRoot
    def testIPv6ConfigNetwork(self):
        with dummyIf(1) as nics:
            nic, = nics
            networks = {
                NETWORK_NAME + '1':
                {'nic': nic, 'bootproto': 'static', 'ipv6addr': IPv6_ADDRESS,
                 'ipv6gateway': IPv6_GATEWAY},
                NETWORK_NAME + '2':
                {'nic': nic, 'bootproto': 'static', 'ipv6addr': IPv6_ADDRESS,
                 'ipv6gateway': IPv6_GATEWAY, 'ipaddr': IP_ADDRESS,
                 'gateway': IP_GATEWAY,
                 'netmask': prefix2netmask(int(IP_CIDR))}}
            for network, netdict in networks.iteritems():
                with self.vdsm_net.pinger():
                    status, msg = self.vdsm_net.setupNetworks(
                        {network: netdict}, {}, {})
                    self.assertEqual(status, SUCCESS, msg)
                    self.assertNetworkExists(network)
                    self.assertIn(
                        IPv6_ADDRESS,
                        self.vdsm_net.netinfo.networks[network]['ipv6addrs'])
                    self.assertEqual(
                        IPv6_GATEWAY,
                        self.vdsm_net.netinfo.networks[network]['ipv6gateway'])
                    delete = {network: {'remove': True}}
                    status, msg = self.vdsm_net.setupNetworks(delete, {}, {})
                    self.assertEqual(status, SUCCESS, msg)

    @cleanupNet
    @RequireDummyMod
    @ValidateRunningAsRoot
    def testIpLinkWrapper(self):
        """Tests that the created devices are properly parsed by the ipwrapper
        Link class."""
        BIG_MTU = 2000
        VLAN_NAME = '%s.%s' % (BONDING_NAME, VLAN_ID)
        with dummyIf(2) as nics:
            status, msg = self.vdsm_net.setupNetworks(
                {NETWORK_NAME:
                    {'bonding': BONDING_NAME, 'bridged': True,
                        'vlan': VLAN_ID, 'mtu': BIG_MTU}},
                {BONDING_NAME:
                    {'nics': nics}},
                NOCHK)
            deviceLinks = getLinks()
            deviceNames = [device.name for device in deviceLinks]

            # Test all devices to be there.
            self.assertIn(NETWORK_NAME, deviceNames)
            self.assertIn(BONDING_NAME, deviceNames)
            self.assertIn(nics[0], deviceNames)
            self.assertIn(nics[1], deviceNames)
            self.assertIn(VLAN_NAME, deviceNames)

            for device in deviceLinks:
                if device.name == NETWORK_NAME:
                    self.assertEqual(device.type, LinkType.BRIDGE)
                elif device.name in nics:
                    self.assertEqual(device.type, LinkType.DUMMY)
                elif device.name == VLAN_NAME:
                    self.assertEqual(device.type, LinkType.VLAN)
                elif device.name == BONDING_NAME:
                    self.assertEqual(device.type, LinkType.BOND)
                    self.assertEqual(device.mtu, BIG_MTU)

            # Cleanup
            status, msg = self.vdsm_net.setupNetworks(
                {NETWORK_NAME: {'remove': True}},
                {BONDING_NAME: {'remove': True}},
                NOCHK)

    @permutations([[True], [False]])
    @cleanupNet
    @RequireVethMod
    @ValidateRunningAsRoot
    def testSetupNetworksAddDelDhcp(self, bridged):
        with vethIf() as (left, right):
            veth.setIP(left, IP_ADDRESS, IP_CIDR)
            veth.setLinkUp(left)
            with dnsmasqDhcp(left):
                network = {NETWORK_NAME: {'nic': right, 'bridged': bridged,
                                          'bootproto': 'dhcp',
                                          'blockingdhcp': True}}

                status, msg = self.vdsm_net.setupNetworks(network, {}, NOCHK)
                self.assertEqual(status, SUCCESS, msg)
                self.assertNetworkExists(NETWORK_NAME)

                net = self.vdsm_net.netinfo.networks[NETWORK_NAME]
                self.assertEqual(net['bootproto4'], 'dhcp')

                if bridged:
                    self.assertEqual(net['cfg']['BOOTPROTO'], 'dhcp')

                    devs = self.vdsm_net.netinfo.bridges
                    self.assertIn(NETWORK_NAME, devs)
                    self.assertEqual(devs[NETWORK_NAME]['cfg']['BOOTPROTO'],
                                     'dhcp')

                else:
                    devs = self.vdsm_net.netinfo.nics
                    self.assertIn(right, devs)
                    self.assertEqual(devs[right]['cfg']['BOOTPROTO'], 'dhcp')

                network = {NETWORK_NAME: {'remove': True}}
                status, msg = self.vdsm_net.setupNetworks(network, {}, NOCHK)
                self.assertEqual(status, SUCCESS, msg)
                self.assertNetworkDoesntExist(NETWORK_NAME)

    @permutations([['default'], ['local']])
    @cleanupNet
    @RequireVethMod
    @ValidateRunningAsRoot
    def testDhclientLeases(self, dateFormat):
        dhcp4 = set()
        with vethIf() as (server, client):
            with avoidAnotherDhclient(client):

                veth.setIP(server, IP_ADDRESS, IP_CIDR)
                veth.setLinkUp(server)

                with dnsmasqDhcp(server):

                    with namedTemporaryDir(dir='/var/lib/dhclient') as dir:
                        leaseFile = dhcp.runDhclient(client, dir, dateFormat)

                        dhcp4 = getDhclientIfaces([leaseFile])

        self.assertIn(client, dhcp4, 'Test iface not found in a lease file.')

    @RequireDummyMod
    @ValidateRunningAsRoot
    def testGetRouteDeviceTo(self):
        with dummyIf(1) as nics:
            nic, = nics
            dummy.setIP(nic, IP_ADDRESS, IP_CIDR)
            try:
                dummy.setLinkUp(nic)
                self.assertEqual(getRouteDeviceTo(IP_ADDRESS_IN_NETWORK), nic)
            finally:
                addrFlush(nic)

    @RequireDummyMod
    @ValidateRunningAsRoot
    def testBrokenBridgelessNetReplacement(self):
        with dummyIf(1) as nics:
            nic, = nics
            network = {NETWORK_NAME: {'nic': nic, 'vlan': VLAN_ID,
                                      'bridged': False}}
            status, msg = self.vdsm_net.setupNetworks(network, {},
                                                      NOCHK)
            self.assertEqual(status, SUCCESS, msg)
            self.assertNetworkExists(NETWORK_NAME)
            ipwrapper.linkDel(nic + '.' + VLAN_ID)
            self.vdsm_net.refreshNetinfo()
            self.assertNotIn(NETWORK_NAME, self.vdsm_net.netinfo.networks)
            status, msg = self.vdsm_net.setupNetworks(network, {},
                                                      NOCHK)
            self.assertEqual(status, SUCCESS, msg)
            self.assertNetworkExists(NETWORK_NAME)
            network[NETWORK_NAME] = {'remove': True}
            status, msg = self.vdsm_net.setupNetworks(network, {},
                                                      NOCHK)
            self.assertEqual(status, SUCCESS, msg)
            self.assertNetworkDoesntExist(NETWORK_NAME)

    @cleanupNet
    @RequireDummyMod
    @ValidateRunningAsRoot
    def testReconfigureBrNetWithVanishedPort(self):
        """Test for re-defining a bridged network for which the device
        providing connectivity to the bridge had been removed from it"""
        with dummyIf(1) as nics:
            nic, = nics
            network = {NETWORK_NAME: {'nic': nic, 'bridged': True}}
            status, msg = self.vdsm_net.setupNetworks(network, {}, NOCHK)
            self.assertEqual(status, SUCCESS, msg)
            self.assertNetworkExists(NETWORK_NAME)

            # Remove the nic from the bridge
            execCmd([EXT_BRCTL, 'delif', NETWORK_NAME, nic])
            self.vdsm_net.refreshNetinfo()
            self.assertEqual(len(
                self.vdsm_net.netinfo.networks[NETWORK_NAME]['ports']), 0)

            # Attempt to reconfigure the network
            status, msg = self.vdsm_net.setupNetworks(network, {}, NOCHK)
            self.assertEqual(status, SUCCESS, msg)
            self.assertEqual(
                self.vdsm_net.netinfo.networks[NETWORK_NAME]['ports'], [nic])

            # cleanup
            network[NETWORK_NAME] = {'remove': True}
            status, msg = self.vdsm_net.setupNetworks(network, {},
                                                      NOCHK)
            self.assertEqual(status, SUCCESS, msg)
            self.assertNetworkDoesntExist(NETWORK_NAME)

    @RequireDummyMod
    @ValidateRunningAsRoot
    def testNoBridgeLeftovers(self):
        """Test for https://bugzilla.redhat.com/1071398"""
        with dummyIf(2) as nics:
            network = {NETWORK_NAME: {'bonding': BONDING_NAME}}
            bonds = {BONDING_NAME: {'nics': nics}}
            status, msg = self.vdsm_net.setupNetworks(network, bonds, NOCHK)
            self.assertEqual(status, SUCCESS, msg)
            self.assertNetworkExists(NETWORK_NAME)

            # Remove the network but not the bond
            network[NETWORK_NAME] = {'remove': True}
            status, msg = self.vdsm_net.setupNetworks(network, {}, NOCHK)
            self.assertEqual(status, SUCCESS, msg)
            self.assertNotIn(NETWORK_NAME, bridges())

            bonds[BONDING_NAME] = {'remove': True}
            status, msg = self.vdsm_net.setupNetworks({}, bonds, NOCHK)
            self.assertEqual(status, SUCCESS, msg)

    @RequireDummyMod
    @ValidateRunningAsRoot
    def testRedefineBondedNetworkIPs(self):
        """Test for https://bugzilla.redhat.com/1097674"""
        with dummyIf(2) as nics:
            network = {NETWORK_NAME: {'bonding': BONDING_NAME,
                                      'bridged': False, 'ipaddr': '1.1.1.1',
                                      'prefix': '24'}}
            bonds = {BONDING_NAME: {'nics': nics}}
            status, msg = self.vdsm_net.setupNetworks(network, bonds, NOCHK)
            self.assertEqual(status, SUCCESS, msg)
            self.assertNetworkExists(NETWORK_NAME)
            self.assertEqual(
                self.vdsm_net.netinfo.bondings[BONDING_NAME]['addr'],
                network[NETWORK_NAME]['ipaddr'])
            self.assertEqual(len(
                self.vdsm_net.netinfo.bondings[BONDING_NAME]['ipv4addrs']), 1)

            # Redefine the ip address
            network[NETWORK_NAME]['ipaddr'] = '1.1.1.2'
            status, msg = self.vdsm_net.setupNetworks(network, bonds, NOCHK)
            self.assertEqual(status, SUCCESS, msg)
            self.assertNetworkExists(NETWORK_NAME)
            self.assertEqual(
                self.vdsm_net.netinfo.bondings[BONDING_NAME]['addr'],
                network[NETWORK_NAME]['ipaddr'])
            self.assertEqual(len(
                self.vdsm_net.netinfo.bondings[BONDING_NAME]['ipv4addrs']), 1)

            # Redefine the ip address
            network[NETWORK_NAME]['ipaddr'] = '1.1.1.3'
            status, msg = self.vdsm_net.setupNetworks(network, bonds, NOCHK)
            self.assertEqual(status, SUCCESS, msg)
            self.assertNetworkExists(NETWORK_NAME)
            self.assertEqual(
                self.vdsm_net.netinfo.bondings[BONDING_NAME]['addr'],
                network[NETWORK_NAME]['ipaddr'])
            self.assertEqual(len(
                self.vdsm_net.netinfo.bondings[BONDING_NAME]['ipv4addrs']), 1)

            # Cleanup
            network[NETWORK_NAME] = {'remove': True}
            bonds[BONDING_NAME] = {'remove': True}
            status, msg = self.vdsm_net.setupNetworks(network, bonds, NOCHK)
            self.assertEqual(status, SUCCESS, msg)

    @cleanupNet
    @RequireDummyMod
    @ValidateRunningAsRoot
    def testLowerMtuDoesNotOverride(self):
        """Adding multiple vlanned networks with different mtus over a bond
        should have each network with its own mtu and the bond with the maximum
        mtu amongst all the configured networks"""
        with dummyIf(2) as nics:
            MTU_LOWEST, MTU_MAX, MTU_STEP = 2200, 3000, 100

            # We need the dictionary to at least have one smaller mtu network
            # handled after a bigger mtu one. The dictionary order depends on
            # the string hash, so having the net names in deceasing and mtu
            # values in increasing order will help.
            networks = dict(
                (NETWORK_NAME + str(MTU_MAX - mtu),
                 {'mtu': mtu, 'bonding': BONDING_NAME, 'vlan': mtu}) for mtu in
                range(MTU_LOWEST, MTU_MAX, MTU_STEP))
            bonds = {BONDING_NAME: {'nics': nics}}

            status, msg = self.vdsm_net.setupNetworks(networks, bonds, NOCHK)
            self.assertEquals(status, SUCCESS, msg)
            for network, attributes in networks.iteritems():
                self.assertNetworkExists(network)
                self.assertMtu(attributes['mtu'], network)

            # Check that the bond's mtu is the maximum amongst the networks,
            # which range [MTU_LOWEST, MTU_MAX - MTU_STEP]
            self.assertMtu(MTU_MAX - MTU_STEP, BONDING_NAME)

            # cleanup
            for network in networks.iterkeys():
                networks[network] = {'remove': True}
            bonds['BONDING_NAME'] = {'remove': True}
            status, msg = self.vdsm_net.setupNetworks(networks, {}, NOCHK)
            self.assertEquals(status, SUCCESS, msg)

    @cleanupNet
    @ValidateRunningAsRoot
    def testHonorBlockingDhcp(self):
        status, msg = self.vdsm_net.setupNetworks(
            {NETWORK_NAME: {'bridged': True, 'bootproto': 'dhcp',
                            'blockingdhcp': True}}, {}, NOCHK)
        # Without blocking dhcp, the setupNetworks command would return
        # reporting success before knowing if dhclient succeeded. With blocking
        # it must not report success
        self.assertNotEqual(status, SUCCESS, msg)

    @cleanupNet
    @ValidateRunningAsRoot
    def testSetupNetworksOverDhcpIface(self):
        """When asked to setupNetwork on top of an interface with a running
        dhclient process, Vdsm is expected to stop that dhclient and start
        owning the interface. BZ#1100264"""
        with vethIf() as (server, client):
            with avoidAnotherDhclient(client):
                veth.setIP(server, IP_ADDRESS, IP_CIDR)
                veth.setLinkUp(server)
                with dnsmasqDhcp(server):
                    with namedTemporaryDir(dir='/var/lib/dhclient') as dhdir:
                        # Start a non-vdsm owned dhclient for the 'client'
                        # iface
                        dhcp.runDhclient(client, dhdir, 'default')
                        # Set up a network over it and wait for dhcp success
                        status, msg = self.vdsm_net.setupNetworks(
                            {NETWORK_NAME: {'nic': client, 'bridged': False,
                             'bootproto': 'dhcp', 'blockingdhcp': True}}, {},
                            NOCHK)
                        self.assertEquals(status, SUCCESS, msg)
                        self.assertNetworkExists(NETWORK_NAME)

                        # Verify that dhclient is running for the device
                        pids = pgrep('dhclient')
                        ifaces = [open('/proc/%s/cmdline' % pid).read().
                                  strip('\0').split('\0')[-1] for pid in pids]
                        vdsm_dhclient = [iface for iface in ifaces if
                                         iface == client]
                        self.assertEqual(len(vdsm_dhclient), 1,
                                         'There should be one and only one '
                                         'running dhclient for the device')

            # cleanup
            status, msg = self.vdsm_net.setupNetworks(
                {NETWORK_NAME: {'remove': True}}, {}, NOCHK)

    @cleanupNet
    def testSetupNetworksConnectivityCheck(self):
        status, msg = self.vdsm_net.setupNetworks(
            {NETWORK_NAME: {'bridged': True}}, {},
            {'connectivityCheck': True, 'connectivityTimeout': 0.1})
        self.assertEqual(status, errors.ERR_LOST_CONNECTION)
        self.assertNetworkDoesntExist(NETWORK_NAME)
