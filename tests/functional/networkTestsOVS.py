#
# Copyright 2015 Red Hat, Inc.
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
from functools import wraps

from nose.plugins.skip import SkipTest

from vdsm.utils import RollbackContext

from testlib import expandPermutations, permutations
from testValidation import RequireVethMod

import networkTests
from networkTests import (setupModule, tearDownModule, NetworkTest, dummyIf,
                          _get_source_route, dnsmasqDhcp, NETWORK_NAME,
                          IP_ADDRESS, IP_MASK, IP_CIDR, IP_GATEWAY,
                          IPv6_ADDRESS, IPv6_CIDR, VLAN_ID, NOCHK, SUCCESS)
from utils import VdsProxy
import veth
import dhcp

# WARNING: because of this module changes networkTests module, we cannot run
# networkTests.py and networkTestsOVS.py in one run

# Make Pyflakes happy
setupModule
tearDownModule

# Do not trigger NetworkTest
NetworkTest.__test__ = False

BRIDGE_NAME = 'ovsbr0'

# Tests which are not supported by OVS hook (because of OVS hook or because of
# tests themselves). Some of these tests should be inherited and 'repaired'
# for OVS, or rewritten.
not_supported = [
    'testAddVlanedBridgeless',  # bridgeless
    'testAddVlanedBridgeless_oneCommand',  # bridgeless
    'testAfterNetworkSetupHook',  # bridgeless
    'testBeforeNetworkSetupHook',  # bridgeless
    'testBrokenNetworkReplacement(False)',  # bridgeless
    'testBrokenNetworkReplacement(True)',  # uses `ip l`
    'testDhcpReplaceNicWithBridge',  # bridgeless
    'testIpLinkWrapper',  # uses netlink.iter_links
    'testReconfigureBrNetWithVanishedPort',  # uses brctl
    'testRedefineBondedNetworkIPs',  # bridgeless
    'testRemovingBridgeDoesNotLeaveBridge',  # uses netlink.iter_links
    'testRestoreNetworksOnlyRestoreUnchangedDevices',  # bond with one slave
    'testRestoreToBlockingDHCP',  # bridgeless
    'testSelectiveRestoreDuringUpgrade',  # multiple untagged nets
    'testSetupNetworkOutboundQos(False)',  # bridgeless
    'testSetupNetworksActiveSlave',  # ovs doesn't report fake active slaves
    'testSetupNetworksAddBadParams(False)',  # bridgeless
    'testSetupNetworksAddBondWithManyVlans(False)',  # bridgeless
    'testSetupNetworksAddDelBondedNetwork(False)',  # bridgeless
    'testSetupNetworksAddDelDhcp(False, (4, 6))',  # bridgeless
    'testSetupNetworksAddDelDhcp(False, (4,))',  # bridgeless
    'testSetupNetworksAddDelDhcp(False, (6,))',  # bridgeless
    'testSetupNetworksAddManyVlans(False)',  # bridgeless
    'testSetupNetworksAddNetworkToNicAfterBondBreaking(False)',  # bridgeless
    'testSetupNetworksAddNetworkToNicAfterBondResizing(False)',  # bridgeless
    'testSetupNetworksAddNetworkToNicAfterBondResizing(True)',  # untagged nets
    'testSetupNetworksAddOverExistingBond(False)',  # bridgeless
    'testSetupNetworksAddOverExistingBond(True)',  # bridgeless
    'testSetupNetworksAddVlan(False)',  # bridgeless
    'testSetupNetworksConvertVlanNetBridgeness',  # bridgeless
    'testSetupNetworksDelOneOfBondNets',  # bridgeless
    'testSetupNetworksDeletesTheBridgeOnlyWhenItIsReconfigured',  # netlink
    'testSetupNetworksEmergencyDevicesCleanupBondOverwrite(False)',  # brless
    'testSetupNetworksEmergencyDevicesCleanupVlanOverwrite(False)',  # brless
    'testSetupNetworksKeepNetworkOnBondAfterBondResizing(False)',  # bridgeless
    'testSetupNetworksMtus(False)',  # bridgeless
    'testSetupNetworksMultiMTUsOverBond(False)',  # bridgeless
    'testSetupNetworksMultiMTUsOverNic(False)',  # bridgeless
    'testSetupNetworksNetCompatibilityMultipleNetsSameNic(False)',  # brless
    'testSetupNetworksNiclessBridgeless',  # bridgeless
    'testSetupNetworksOverDhcpIface',  # bridgeless
    'testSetupNetworksRemoveBondWithKilledEnslavedNics',  # bridgeless
    'testSetupNetworksRemoveSlavelessBond',  # bridgeless
    'testSetupNetworksResizeBond(False)',  # bridgeless
    'testSetupNetworksResizeBond(True)',  # assert exact custom=ovs=True
    'testSetupNetworksStableBond(False)',  # bridgeless
    'testSetupNetworksStableBond(True)',  # OVS wont change operstate
    'testStaticSourceRouting(False)',  # bridgeless
    'test_setupNetworks_bond_with_custom_option',  # has custom=ovs=True
    'test_setupNetworks_on_external_bond',  # uses /proc/sys/net
    'test_setupNetworks_on_external_vlaned_bond'  # uses ifcfg
]
# Test which are not using OVS hook. It make sense to run them anyways,
# but could be skipped.
does_not_use_ovs = [
    'testAddDelBondedNetwork(False)',
    'testAddDelBondedNetwork(True)',
    'testAddDelNetwork(False)',
    'testAddDelNetwork(True)',
    'testAddNetworkBondWithManyVlans(False)',
    'testAddNetworkBondWithManyVlans(True)',
    'testAddNetworkManyVlans(False)',
    'testAddNetworkManyVlans(True)',
    'testAddNetworkVlan(False)',
    'testAddNetworkVlan(True)',
    'testAddNetworkVlanBond(False)',
    'testAddNetworkVlanBond(True)',
    'testBondHwAddress(False)',
    'testBondHwAddress(True)',
    'testDelNetworkBondAccumulation',
    'testDelNetworkWithMTU(False)',
    'testDelNetworkWithMTU(True)',
    'testDelWithoutAdd(False)',
    'testDelWithoutAdd(True)',
    "testDhclientLeases(4, 'default')",
    "testDhclientLeases(4, 'local')",
    'testDhclientLeases(6, None)',
    'testEditWithoutAdd(False)',
    'testEditWithoutAdd(True)',
    'testFailWithInvalidBondingName(False)',
    'testFailWithInvalidBondingName(True)',
    'testFailWithInvalidBridgeName',
    'testFailWithInvalidIpConfig',
    'testFailWithInvalidNic(False)',
    'testFailWithInvalidNic(True)',
    'testFailWithInvalidParams(False)',
    'testFailWithInvalidParams(True)',
    'testGetRouteDeviceTo',
    'testReorderBondingOptions(False)',
    'testReorderBondingOptions(True)',
    'testSafeNetworkConfig(False)',
    'testSafeNetworkConfig(True)',
    'testTwiceAdd(False)',
    'testTwiceAdd(True)',
    'testVolatileConfig(False)',
    'testVolatileConfig(True)',
    'test_getVdsStats'
]
for t in does_not_use_ovs:
    delattr(NetworkTest, t)
for t in not_supported:
    delattr(NetworkTest, t)


# When we set OVS bond device up, it does not turn UP, but only UNKNOWN
def _fakeWaitForKnownOperstate(*args, **kwargs):
    pass
networkTests._waitForKnownOperstate = _fakeWaitForKnownOperstate


class OVSVdsProxy(VdsProxy):

    def setupNetworks(self, networks, bonds, options):
        if options.pop('ovs', True):
            # skip non-bridged networks and networks without a nic/bonding,
            # such tests should be listed in not_suported list
            for _, attrs in networks.items():
                if not attrs.get('bridged', True):
                    raise SkipTest('OVS does not support bridgeless networks')

            # setup every network as OVS network
            for network, attrs in networks.items():
                if 'remove' not in attrs:
                    networks[network].update({'custom': {'ovs': True}})
            for bond, attrs in bonds.items():
                if 'remove' not in attrs:
                    bond_opts = bonds[bond].get('options', '').split()
                    modified = False
                    for i in range(len(bond_opts)):
                        if bond_opts[i].startswith('custom='):
                            bond_opts[i] = ('custom=%s,ovs=True' %
                                            bond_opts[i].split('=', 1)[1])
                            modified = True
                            break
                    if not modified:
                        bond_opts.append('custom=ovs=True')
                    bonds[bond]['options'] = ' '.join(bond_opts)

        return super(OVSVdsProxy, self).setupNetworks(networks, bonds, options)


@expandPermutations
class OVSNetworkTest(NetworkTest):
    __test__ = True

    def cleanupNet(func):
        """ Copied from networkTests.py
        Instance method decorator. Restores a previously persisted network
        config in case of a test failure, traceback is kept. Assumes root
        privileges.
        """

        @wraps(func)
        def wrapper(*args, **kwargs):
            with RollbackContext(on_exception_only=True) as rollback:
                rollback.prependDefer(args[0].vdsm_net.restoreNetConfig)
                func(*args, **kwargs)
        return wrapper

    def setUp(self):
        self.vdsm_net = OVSVdsProxy()

    def setupNetworks(self, *args, **kwargs):
        # Do not run test_kernel_config
        if 'test_kernel_config' in kwargs:
            kwargs.pop('test_kernel_config')
        return self.vdsm_net.setupNetworks(*args, **kwargs)

    @cleanupNet
    def test_ovirtmgmtm_to_ovs(self):
        """ Test transformation of initial management network to OVS.
        # TODO: test it with ovirtmgmt and test-network
        # NOTE: without default route
        # TODO: more asserts
        """
        with veth.pair() as (left, right):
            veth.setIP(left, IP_ADDRESS, IP_CIDR)
            veth.setIP(left, IPv6_ADDRESS, IPv6_CIDR, 6)
            veth.setLinkUp(left)
            with dnsmasqDhcp(left):
                network = {
                    NETWORK_NAME: {'nic': right, 'bootproto': 'dhcp',
                                   'bridged': True, 'blockingdhcp': True}}
                options = NOCHK
                options['ovs'] = False

                try:
                    status, msg = self.setupNetworks(network, {}, options)
                    self.assertEqual(status, SUCCESS, msg)
                    self.assertNetworkExists(NETWORK_NAME)

                    options['ovs'] = True
                    status, msg = self.setupNetworks(network, {}, options)
                    self.assertEqual(status, SUCCESS, msg)
                    self.assertNetworkExists(NETWORK_NAME)
                finally:
                    dhcp.delete_dhclient_leases(NETWORK_NAME, True, False)

    @permutations([(True,)])
    @cleanupNet
    def testStaticSourceRouting(self, bridged):
        """ Copied from networkTests.py, network changed to vlaned. """
        with dummyIf(1) as nics:
            status, msg = self.setupNetworks(
                {NETWORK_NAME:
                    {'nic': nics[0], 'bridged': bridged, 'ipaddr': IP_ADDRESS,
                     'netmask': IP_MASK, 'gateway': IP_GATEWAY,
                     'vlan': VLAN_ID}},
                {}, NOCHK)
            self.assertEqual(status, SUCCESS, msg)
            self.assertNetworkExists(NETWORK_NAME, bridged)

            deviceName = NETWORK_NAME if bridged else nics[0]
            ip_addr = self.vdsm_net.netinfo.networks[NETWORK_NAME]['addr']
            self.assertSourceRoutingConfiguration(deviceName, ip_addr)

            status, msg = self.setupNetworks(
                {NETWORK_NAME: {'remove': True}}, {}, NOCHK)
            self.assertEqual(status, SUCCESS, msg)

            # Assert that routes and rules don't exist
            source_route = _get_source_route(deviceName, ip_addr)
            for route in source_route._buildRoutes():
                self.assertRouteDoesNotExist(route)
            for rule in source_route._buildRules():
                self.assertRuleDoesNotExist(rule)

    @permutations([(True, (4,)), (True, (6,)), (True, (4, 6))])
    @cleanupNet
    @RequireVethMod
    def testSetupNetworksAddDelDhcp(self, bridged, families):
        """ Copied from networkTests.py, source_route checking changed from
        device_name to BRIDGE_NAME.
        """
        with veth.pair() as (left, right):
            veth.setIP(left, IP_ADDRESS, IP_CIDR)
            veth.setIP(left, IPv6_ADDRESS, IPv6_CIDR, 6)
            veth.setLinkUp(left)
            with dnsmasqDhcp(left):
                dhcpv4 = 4 in families
                dhcpv6 = 6 in families
                bootproto = 'dhcp' if dhcpv4 else 'none'
                network = {NETWORK_NAME: {'nic': right, 'bridged': bridged,
                                          'bootproto': bootproto,
                                          'dhcpv6': dhcpv6,
                                          'blockingdhcp': True}}
                try:
                    status, msg = self.setupNetworks(network, {}, NOCHK)
                    self.assertEqual(status, SUCCESS, msg)
                    self.assertNetworkExists(NETWORK_NAME)

                    test_net = self.vdsm_net.netinfo.networks[NETWORK_NAME]
                    self.assertEqual(test_net['dhcpv4'], dhcpv4)
                    self.assertEqual(test_net['dhcpv6'], dhcpv6)

                    if bridged:
                        self.assertEqual(test_net['cfg']['BOOTPROTO'],
                                         bootproto)
                        devs = self.vdsm_net.netinfo.bridges
                        device_name = NETWORK_NAME
                    else:
                        devs = self.vdsm_net.netinfo.nics
                        device_name = right

                    self.assertIn(device_name, devs)
                    net_attrs = devs[device_name]
                    self.assertEqual(net_attrs['cfg']['BOOTPROTO'], bootproto)
                    self.assertEqual(net_attrs['dhcpv4'], dhcpv4)
                    self.assertEqual(net_attrs['dhcpv6'], dhcpv6)

                    if dhcpv4:
                        self.assertEqual(test_net['gateway'], IP_GATEWAY)
                        # TODO: source routing not ready for IPv6
                        ip_addr = test_net['addr']
                        self.assertSourceRoutingConfiguration(BRIDGE_NAME,
                                                              ip_addr)

                    # Do not report DHCP from (typically still valid) leases
                    network[NETWORK_NAME]['bootproto'] = 'none'
                    network[NETWORK_NAME]['dhcpv6'] = False
                    status, msg = self.setupNetworks(network, {}, NOCHK)
                    self.assertEqual(status, SUCCESS, msg)
                    test_net = self.vdsm_net.netinfo.networks[NETWORK_NAME]
                    self.assertEqual(test_net['dhcpv4'], False)
                    self.assertEqual(test_net['dhcpv6'], False)

                    network = {NETWORK_NAME: {'remove': True}}
                    status, msg = self.setupNetworks(network, {}, NOCHK)
                    self.assertEqual(status, SUCCESS, msg)
                    self.assertNetworkDoesntExist(NETWORK_NAME)

                    # Assert that routes and rules don't exist
                    if dhcpv4:
                        source_route = _get_source_route(BRIDGE_NAME, ip_addr)
                        for route in source_route._buildRoutes():
                            self.assertRouteDoesNotExist(route)
                        for rule in source_route._buildRules():
                            self.assertRuleDoesNotExist(rule)
                finally:
                    dhcp.delete_dhclient_leases(
                        NETWORK_NAME if bridged else right, dhcpv4, dhcpv6)
