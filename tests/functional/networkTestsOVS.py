#
# Copyright 2015-2016 Red Hat, Inc.
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

from vdsm.ipwrapper import linkSet, addrAdd
from vdsm.utils import RollbackContext

from modprobe import RequireVethMod
from network.nettestlib import veth_pair, dhcp, dnsmasq_run
from testlib import expandPermutations, permutations

import networkTests
from networkTests import (setupModule, tearDownModule, NetworkTest, dummyIf,
                          _get_source_route, NETWORK_NAME,
                          IP_ADDRESS, IP_MASK, IP_CIDR, IP_GATEWAY,
                          IPv6_ADDRESS, IPv6_CIDR, VLAN_ID, NOCHK, SUCCESS)

# WARNING: because of this module changes networkTests module, we cannot run
# networkTests.py and networkTestsOVS.py in one run

# Make Pyflakes happy
setupModule
tearDownModule

# Do not trigger NetworkTest
NetworkTest.__test__ = False

BRIDGE_NAME = 'ovsbr0'

DHCP_RANGE_FROM = '192.0.2.10'
DHCP_RANGE_TO = '192.0.2.100'
DHCPv6_RANGE_FROM = 'fdb3:84e5:4ff4:55e3::a'
DHCPv6_RANGE_TO = 'fdb3:84e5:4ff4:55e3::64'

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
    # two networks cannot share one tag
    'testSetupNetworksNetCompatibilityMultipleNetsSameNic(True)',
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
    'testBondHwAddress(False)',
    'testBondHwAddress(True)',
    'testDelNetworkBondAccumulation',
    'testDelNetworkWithMTU(False)',
    'testDelNetworkWithMTU(True)',
    'testDelWithoutAdd',
    "testDhclientLeases(4, 'default')",
    "testDhclientLeases(4, 'local')",
    'testDhclientLeases(6, None)',
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
def _fakeWaitForOperstate(*args, **kwargs):
    pass
networkTests._waitForOperstate = _fakeWaitForOperstate
networkTests._waitForKnownOperstate = _fakeWaitForOperstate


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

    def setupNetworks(self, nets, bonds, opts, **kwargs):
        if opts.pop('ovs', True):
            # setup every network as OVS network
            for net, attrs in nets.items():
                if not attrs.get('bridged', True):
                    raise SkipTest('OVS does not support bridgeless networks')
                if 'remove' not in attrs:
                    nets[net].update({'custom': {'ovs': True}})
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

        return self.vdsm_net.setupNetworks(nets, bonds, opts)

    @cleanupNet
    def test_ovirtmgmtm_to_ovs(self):
        """ Test transformation of initial management network to OVS.
        # TODO: test it with ovirtmgmt and test-network
        # NOTE: without default route
        # TODO: more asserts
        """
        with veth_pair() as (left, right):
            addrAdd(left, IP_ADDRESS, IP_CIDR)
            addrAdd(left, IPv6_ADDRESS, IPv6_CIDR, 6)
            linkSet(left, ['up'])
            with dnsmasq_run(left, DHCP_RANGE_FROM, DHCP_RANGE_TO,
                             DHCPv6_RANGE_FROM, DHCPv6_RANGE_TO, IP_GATEWAY):
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
        """Copied from networkTests.py, source_route checking changed from
        device_name to BRIDGE_NAME.
        """
        def _assert_applied(network_name, requested, reported):
            self.assertNetworkExists(network_name)
            reported_network = reported.networks[network_name]

            if requested['bridged']:
                self.assertEqual(reported_network['cfg']['BOOTPROTO'],
                                 requested['bootproto'])
                reported_devices = reported.bridges
                device_name = network_name
            else:
                # CHANGED: always bridged
                pass
            self.assertIn(device_name, reported_devices)
            reported_device = reported_devices[device_name]

            requested_dhcpv4 = requested['bootproto'] == 'dhcp'
            self.assertEqual(reported_network['dhcpv4'], requested_dhcpv4)
            self.assertEqual(reported_network['dhcpv6'], requested['dhcpv6'])

            self.assertEqual(reported_device['cfg']['BOOTPROTO'],
                             requested['bootproto'])
            self.assertEqual(reported_device['dhcpv4'], requested_dhcpv4)
            self.assertEqual(reported_device['dhcpv6'], requested['dhcpv6'])

            if requested_dhcpv4:
                self.assertEqual(reported_network['gateway'], IP_GATEWAY)
                # TODO: source routing not ready for IPv6
                ip_addr = reported_network['addr']
                self.assertSourceRoutingConfiguration(BRIDGE_NAME,  # CHANGED
                                                      ip_addr)
                return device_name, ip_addr
            return None, None

        with veth_pair() as (left, right):
            addrAdd(left, IP_ADDRESS, IP_CIDR)
            addrAdd(left, IPv6_ADDRESS, IPv6_CIDR, 6)
            linkSet(left, ['up'])
            with dnsmasq_run(left, DHCP_RANGE_FROM, DHCP_RANGE_TO,
                             DHCPv6_RANGE_FROM, DHCPv6_RANGE_TO, IP_GATEWAY):
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

                    device_name, ip_addr = _assert_applied(
                        NETWORK_NAME, network[NETWORK_NAME],
                        self.vdsm_net.netinfo)

                    # Do not report DHCP from (typically still valid) leases
                    network[NETWORK_NAME]['bootproto'] = 'none'
                    network[NETWORK_NAME]['dhcpv6'] = False
                    status, msg = self.setupNetworks(network, {}, NOCHK)
                    self.assertEqual(status, SUCCESS, msg)

                    _assert_applied(NETWORK_NAME, network[NETWORK_NAME],
                                    self.vdsm_net.netinfo)

                    network = {NETWORK_NAME: {'remove': True}}
                    status, msg = self.setupNetworks(network, {}, NOCHK)
                    self.assertEqual(status, SUCCESS, msg)
                    self.assertNetworkDoesntExist(NETWORK_NAME)

                    # Assert that routes and rules don't exist
                    if dhcpv4:
                        source_route = _get_source_route(device_name, ip_addr)
                        for route in source_route._buildRoutes():
                            self.assertRouteDoesNotExist(route)
                        for rule in source_route._buildRules():
                            self.assertRuleDoesNotExist(rule)
                finally:
                    dhcp.delete_dhclient_leases(
                        NETWORK_NAME if bridged else right, dhcpv4, dhcpv6)
