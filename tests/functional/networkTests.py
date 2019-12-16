#
# Copyright 2013-2017 Red Hat, Inc.
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

from __future__ import absolute_import
from __future__ import division
from contextlib import contextmanager
from functools import wraps
import json
import os.path

import netaddr
from nose.plugins.skip import SkipTest
import six

import vdsm.config
from vdsm.network.configurators.ifcfg import EXT_IFDOWN, EXT_IFUP
from vdsm.network import netswitch
from vdsm.network.ip import dhclient
from vdsm.network.ipwrapper import (
    routeExists, ruleExists, addrFlush, LinkType, getLinks, routeShowTable,
    linkSet, addrAdd)
from vdsm.network import kernelconfig
from vdsm.network.link.bond.sysfs_driver import BONDING_MASTERS
from vdsm.network.netinfo.bonding import BONDING_SLAVES
from vdsm.network.netinfo.misc import NET_CONF_PREF
from vdsm.network.netinfo.nics import operstate, OPERSTATE_UNKNOWN
from vdsm.network.netinfo.routes import getRouteDeviceTo
from vdsm.network.netlink import monitor
from vdsm.network import sourceroute
from vdsm.network import sysctl

from vdsm.common.cmdutils import CommandPath
from vdsm.common import commands
from vdsm.common.proc import pgrep
from vdsm.utils import RollbackContext

from hookValidation import ValidatesHook

from modprobe import RequireDummyMod, RequireVethMod
from testlib import (VdsmTestCase as TestCaseBase, namedTemporaryDir,
                     expandPermutations, permutations)
from testValidation import slowtest, ValidateRunningAsRoot
from network.nettestlib import Dummy, veth_pair, dnsmasq_run, running
from network import dhcp
from .utils import SUCCESS, getProxy


NETWORK_NAME = 'test-network'
VLAN_ID = '27'
BONDING_NAME = 'bond11'
# Use TEST-NET network (RFC 5737)
IP_ADDRESS = '192.0.2.1'
IP_NETWORK = '192.0.2.0'
IP_ADDRESS_IN_NETWORK = '192.0.2.2'
IP_CIDR = '24'
IP_ADDRESS_AND_CIDR = IP_ADDRESS + '/' + IP_CIDR
IP_NETWORK_AND_CIDR = IP_NETWORK + '/' + IP_CIDR
_ip_network = netaddr.IPNetwork(IP_NETWORK_AND_CIDR)
IP_MASK = str(_ip_network.netmask)
IP_GATEWAY = str(_ip_network.broadcast - 1)
DHCP_RANGE_FROM = '192.0.2.10'
DHCP_RANGE_TO = '192.0.2.100'
DHCPv6_RANGE_FROM = 'fdb3:84e5:4ff4:55e3::a'
DHCPv6_RANGE_TO = 'fdb3:84e5:4ff4:55e3::64'
CUSTOM_PROPS = {'linux': 'rules', 'vdsm': 'as well'}

IPv6_ADDRESS = 'fdb3:84e5:4ff4:55e3::1'
IPv6_CIDR = '64'
IPv6_ADDRESS_AND_CIDR = IPv6_ADDRESS + '/' + IPv6_CIDR
IPv6_ADDRESS_IN_NETWORK = 'fdb3:84e5:4ff4:55e3:0:ffff:ffff:0'
IPv6_GATEWAY = 'fdb3:84e5:4ff4:55e3::ff'

_ARPPING_COMMAND = CommandPath('arping', '/usr/sbin/arping')

dummyPool = set()
DUMMY_POOL_SIZE = 5

NOCHK = {'connectivityCheck': False}


@ValidateRunningAsRoot
@RequireDummyMod
def setupModule():
    vds = getProxy()

    running_config, kernel_config = _get_running_and_kernel_config(vds.config)
    if ((running_config['networks'] != kernel_config['networks']) or
            (running_config['bonds'] != kernel_config['bonds'])):
        raise SkipTest('Tested host is not clean (running vs kernel): '
                       'networks: %r != %r; '
                       'bonds: %r != %r' %
                       (running_config['networks'], kernel_config['networks'],
                        running_config['bonds'], kernel_config['bonds']))

    vds.save_config()
    for _ in range(DUMMY_POOL_SIZE):
        dummy = Dummy()
        dummy.create()
        dummyPool.add(dummy)


def tearDownModule():
    """Restores the network configuration previous to running tests."""
    getProxy().restoreNetConfig()
    for nic in dummyPool:
        nic.remove()


@contextmanager
def dummyIf(num):
    """Manages a list of num dummy interfaces. Assumes root privileges."""
    dummies = []
    try:
        for _ in range(num):
            dummies.append(dummyPool.pop())
        yield [d.devName for d in dummies]
    finally:
        for nic in dummies:
            dummyPool.add(nic)


def _waitForKnownOperstate(device, timeout=1):
    with monitor.Monitor(groups=('link',), timeout=timeout) as mon:
        if operstate(device) == OPERSTATE_UNKNOWN:
            for event in mon:
                if (event['name'] == device and
                        event['state'] != OPERSTATE_UNKNOWN):
                    break


def _waitForOperstate(device, state, timeout=1):
    """ :param state: please use OPERSTATE_* from lib/vdsm/network/netinfo
    """
    with monitor.Monitor(groups=('link',), timeout=timeout) as mon:
        if state != operstate(device):
            for event in mon:
                if event['name'] == device and event['state'] == state:
                    break


class OperStateChangedError(ValueError):
    pass


@contextmanager
def nonChangingOperstate(device):
    """Raises an exception if it detects that the device link state changes."""
    originalState = operstate(device).lower()
    try:
        with monitor.Monitor(groups=('link',)) as mon:
            yield
    finally:
        changes = [(event['name'], event['state']) for event in mon
                   if event['name'] == device]
        for _, state in changes:
            if state != originalState:
                raise OperStateChangedError('%s operstate changed: %s -> %r' %
                                            (device, originalState, changes))


def _cleanup_qos_definition(qos):
    for key, value in qos.items():
        for curve, attrs in value.items():
            if attrs.get('m1') == 0:
                del attrs['m1']
            if attrs.get('d') == 0:
                del attrs['d']


def _get_source_route(device_name, ipv4_address):
    return sourceroute.StaticSourceRoute(
        device_name, ipv4_address, IP_MASK, IP_GATEWAY)


def requiresUnifiedPersistence(reason):
    def wrapper(test_method):
        @wraps(test_method)
        def wrapped_test_method(*args, **kwargs):
            if vdsm.config.config.get('vars', 'net_persistence') == 'ifcfg':
                raise SkipTest(reason)
            test_method(*args, **kwargs)
        return wrapped_test_method
    return wrapper


def _get_running_and_kernel_config(bare_running_config):
    """:param config: vdsm configuration, could be retrieved from getProxy()
    """
    netinfo = vdsm.network.netinfo.cache.NetInfo(
        netswitch.configurator.netinfo())
    bare_kernel_config = kernelconfig.KernelConfig(netinfo)
    normalized_running_config = kernelconfig.normalize(bare_running_config)
    # Unify strings to unicode instances so differences are easier to
    # understand. This won't be needed once we move to Python 3.
    return (normalized_running_config.as_unicode(),
            bare_kernel_config.as_unicode())


@expandPermutations
class NetworkTest(TestCaseBase):

    def setUp(self):
        self.vdsm_net = getProxy()

    def cleanupNet(func):
        """
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

    def assertNetworkExists(self, networkName, bridged=None, bridgeOpts=None,
                            hostQos=None, assert_in_running_conf=True):
        netinfo = self.vdsm_net.netinfo
        network_netinfo = netinfo.networks[networkName]
        self.assertIn(networkName, netinfo.networks)
        if bridged is not None:
            self.assertEqual(bridged, network_netinfo['bridged'])
            if bridged:
                self.assertIn(networkName, netinfo.bridges)
            else:
                self.assertNotIn(networkName, netinfo.bridges)
        if bridgeOpts is not None and network_netinfo['bridged']:
            appliedOpts = netinfo.bridges[networkName]['opts']
            for opt, value in six.viewitems(bridgeOpts):
                self.assertEqual(value, appliedOpts[opt])
        if hostQos is not None:
            reported_qos = network_netinfo['hostQos']
            _cleanup_qos_definition(reported_qos)
            self.assertEqual(reported_qos, hostQos)

        running_config = self.vdsm_net.config
        network_config = running_config.networks[networkName]
        if assert_in_running_conf:
            self.assertIn(networkName, running_config.networks)
            if bridged is not None:
                self.assertEqual(network_config.get('bridged'), bridged)
        physical_iface = (network_config.get('bonding') or
                          network_config.get('nic'))
        vlan_name = ('%s.%s' % (physical_iface, network_config['vlan'])
                     if 'vlan' in network_config else None)
        if network_config.get('bridged', True):
            expected_iface = networkName
        elif network_config.get('vlan') is not None:
            expected_iface = vlan_name
        else:
            expected_iface = physical_iface
        self.assertEqual(network_netinfo['iface'], expected_iface)
        if 'vlan' in network_config:
            self.assertTrue(isinstance(netinfo.vlans[vlan_name]['vlanid'],
                                       int))

    def assertNetworkDoesntExist(self, networkName):
        netinfo = self.vdsm_net.netinfo
        self.assertNotIn(networkName, netinfo.networks)
        self.assertNotIn(networkName, netinfo.bridges)
        if self.vdsm_net.config is not None:
            self.assertNotIn(networkName, self.vdsm_net.config.networks)
        self.assertFalse(
            networkName in kernelconfig.networks_northbound_ifaces())

    def assertBridgeExists(self, bridgeName):
        netinfo = self.vdsm_net.netinfo
        self.assertIn(bridgeName, netinfo.bridges)

    def assertBridgeDoesntExist(self, bridgeName):
        netinfo = self.vdsm_net.netinfo
        self.assertNotIn(bridgeName, netinfo.bridges)

    def assertBondExists(self, bondName, nics=None, options=None,
                         assert_in_running_conf=True):
        netinfo = self.vdsm_net.netinfo
        config = self.vdsm_net.config
        self.assertIn(bondName, netinfo.bondings)
        if nics is not None:
            self.assertEqual(set(nics),
                             set(netinfo.bondings[bondName]['slaves']))
        if assert_in_running_conf and config is not None:
            self.assertIn(bondName, config.bonds)
            self.assertEqual(set(nics),
                             set(config.bonds[bondName].get('nics')))
        if options is not None:
            active_opts = self._get_active_bond_opts(bondName)
            self.assertTrue(set(options.split()) <= set(active_opts))

    def _get_active_bond_opts(self, bondName):
        netinfo = self.vdsm_net.netinfo
        active_options = [opt + '=' + val for (opt, val)
                          in six.viewitems(netinfo.bondings[bondName]['opts'])]
        return active_options

    def assertBondDoesntExist(self, bondName, nics=None):
        netinfo = self.vdsm_net.netinfo
        config = self.vdsm_net.config
        if nics is None:
            self.assertNotIn(bondName, netinfo.bondings,
                             '%s found unexpectedly' % bondName)
        else:
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

    def assertRouteExists(self, route, routing_table='all'):
        if not routeExists(route):
            raise self.failureException(
                "routing rule [%s] wasn't found. existing rules: \n%s" % (
                    route, routeShowTable(str(routing_table))))

    def assertRouteDoesNotExist(self, route, routing_table='all'):
        if routeExists(route):
            raise self.failureException(
                "routing rule [%s] found. existing rules: \n%s" % (
                    route, routeShowTable(routing_table)))

    def assertRuleExists(self, rule):
        if not ruleExists(rule):
            raise self.failureException("routing rule {0} not "
                                        "found".format(rule))

    def assertRuleDoesNotExist(self, rule):
        if ruleExists(rule):
            raise self.failureException("routing rule {0} found".format(rule))

    def assertSourceRoutingConfiguration(self, device_name, ipv4_address):
        """assert that the IP rules and the routing tables pointed by them
        are configured correctly in order to implement source routing"""
        source_route = _get_source_route(device_name, ipv4_address)
        for route in source_route._buildRoutes():
            self.assertRouteExists(route, source_route._table)
        for rule in source_route._buildRules():
            self.assertRuleExists(rule)

    def setupNetworks(self, networks, bonds, options, test_kernel_config=True):
        status, msg = self.vdsm_net.setupNetworks(networks, bonds, options)
        if test_kernel_config:
            self._assert_kernel_config_matches_running_config()
        return status, msg

    def _assert_kernel_config_matches_running_config(self):
        running_config, kernel_config = _get_running_and_kernel_config(
            self.vdsm_net.config)
        # Do not use KernelConfig.__eq__ to get a better exception if something
        # breaks.
        self.assertEqual(running_config['networks'], kernel_config['networks'])
        self.assertEqual(running_config['bonds'], kernel_config['bonds'])

    @permutations([[True], [False]])
    @cleanupNet
    def testStaticSourceRouting(self, bridged=True):
        with dummyIf(1) as nics:
            status, msg = self.setupNetworks(
                {NETWORK_NAME:
                    {'nic': nics[0], 'bridged': bridged, 'ipaddr': IP_ADDRESS,
                     'netmask': IP_MASK, 'gateway': IP_GATEWAY}},
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

    @cleanupNet
    @ValidatesHook('before_network_setup', 'testBeforeNetworkSetup.py', True,
                   "#!/usr/bin/python3\n"
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
                status, msg = self.setupNetworks(
                    networks, {}, {}, test_kernel_config=False)
                self.assertEqual(status, SUCCESS, msg)
                self.assertNetworkExists(NETWORK_NAME, bridged=True)

                self.assertTrue(os.path.isfile(hook_cookiefile))

                status, msg = self.setupNetworks(
                    {NETWORK_NAME: {'remove': True, 'custom': CUSTOM_PROPS}},
                    {}, {})
                self.assertEqual(status, SUCCESS, msg)

    @cleanupNet
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
                self.setupNetworks(networks, {}, {})

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
                status, msg = self.setupNetworks(delete_networks, {}, {})
                self.assertEqual(status, SUCCESS, msg)

    @cleanupNet
    def testIpLinkWrapper(self):
        """Tests that the created devices are properly parsed by the ipwrapper
        Link class."""
        BIG_MTU = 2000
        VLAN_NAME = '%s.%s' % (BONDING_NAME, VLAN_ID)
        with dummyIf(2) as nics:
            status, msg = self.setupNetworks(
                {NETWORK_NAME:
                    {'bonding': BONDING_NAME, 'bridged': True,
                        'vlan': VLAN_ID, 'mtu': BIG_MTU}},
                {BONDING_NAME:
                    {'nics': nics}},
                NOCHK)
            self.assertEqual(status, SUCCESS, msg)
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
            status, msg = self.setupNetworks(
                {NETWORK_NAME: {'remove': True}},
                {BONDING_NAME: {'remove': True}},
                NOCHK)
            self.assertEqual(status, SUCCESS, msg)

    @cleanupNet
    @RequireVethMod
    def testDhcpReplaceNicWithBridge(self):
        with veth_pair() as (left, right):
            addrAdd(left, IP_ADDRESS, IP_CIDR)
            addrAdd(left, IPv6_ADDRESS, IPv6_CIDR, 6)
            linkSet(left, ['up'])
            with dnsmasq_run(left, DHCP_RANGE_FROM, DHCP_RANGE_TO,
                             DHCPv6_RANGE_FROM, DHCPv6_RANGE_TO, IP_GATEWAY):

                # first, a network without a bridge should get a certain
                # address

                network = {NETWORK_NAME: {'nic': right, 'bridged': False,
                                          'bootproto': 'dhcp',
                                          'blockingdhcp': True}}
                try:
                    status, msg = self.setupNetworks(network, {}, NOCHK)
                    self.assertEqual(status, SUCCESS, msg)
                    self.assertNetworkExists(NETWORK_NAME)

                    test_net = self.vdsm_net.netinfo.networks[NETWORK_NAME]
                    self.assertEqual(test_net['dhcpv4'], True)
                    devs = self.vdsm_net.netinfo.nics
                    device_name = right

                    self.assertIn(device_name, devs)
                    net_attrs = devs[device_name]
                    self.assertEqual(net_attrs['dhcpv4'], True)

                    self.assertEqual(test_net['gateway'], IP_GATEWAY)
                    ip_addr = test_net['addr']
                    self.assertSourceRoutingConfiguration(device_name, ip_addr)

                    # now, a bridged network should get the same address
                    # (because dhclient should send the same dhcp-client-
                    #  identifier)

                    network[NETWORK_NAME]['bridged'] = True
                    status, msg = self.setupNetworks(network, {}, NOCHK)
                    self.assertEqual(status, SUCCESS, msg)
                    test_net = self.vdsm_net.netinfo.networks[NETWORK_NAME]
                    self.assertEqual(ip_addr, test_net['addr'])

                    network = {NETWORK_NAME: {'remove': True}}
                    status, msg = self.setupNetworks(network, {}, NOCHK)
                    self.assertEqual(status, SUCCESS, msg)
                    self.assertNetworkDoesntExist(NETWORK_NAME)

                finally:
                    dhcp.delete_dhclient_leases(right, True, False)
                    dhcp.delete_dhclient_leases(NETWORK_NAME, True, False)

    @cleanupNet
    @RequireVethMod
    def testSetupNetworksReconfigureBridge(self):
        def setup_test_network(dhcp=True):
            network_params = {'nic': right, 'bridged': True}
            if dhcp:
                network_params.update(
                    {'bootproto': 'dhcp', 'blockingdhcp': True})
            else:
                network_params.update(
                    {'ipaddr': IP_ADDRESS_IN_NETWORK, 'netmask': IP_MASK,
                     'gateway': IP_GATEWAY})

            status, msg = self.setupNetworks(
                {NETWORK_NAME: network_params}, {}, NOCHK)
            self.assertEqual(status, SUCCESS, msg)
            self.assertNetworkExists(NETWORK_NAME)

            test_net = self.vdsm_net.netinfo.networks[NETWORK_NAME]
            self.assertEqual(test_net['dhcpv4'], dhcp)

            bridges = self.vdsm_net.netinfo.bridges
            self.assertIn(NETWORK_NAME, bridges)
            self.assertEqual(bridges[NETWORK_NAME]['dhcpv4'], dhcp)

        with veth_pair() as (left, right):
            addrAdd(left, IP_ADDRESS, IP_CIDR)
            linkSet(left, ['up'])
            with dnsmasq_run(left, DHCP_RANGE_FROM, DHCP_RANGE_TO,
                             DHCPv6_RANGE_FROM, DHCPv6_RANGE_TO, IP_GATEWAY):
                try:
                    setup_test_network(dhcp=True)
                    dhcp.delete_dhclient_leases(NETWORK_NAME, dhcpv4=True)
                    setup_test_network(dhcp=False)
                finally:
                    dhcp.delete_dhclient_leases(NETWORK_NAME, dhcpv4=True)

    @permutations([(4, 'default'), (4, 'local'), (6, None)])
    @cleanupNet
    @RequireVethMod
    def testDhclientLeases(self, family, dateFormat):
        with veth_pair() as (server, client):
            addrAdd(server, IP_ADDRESS, IP_CIDR)
            addrAdd(server, IPv6_ADDRESS, IPv6_CIDR, 6)
            linkSet(server, ['up'])

            with dnsmasq_run(server, DHCP_RANGE_FROM, DHCP_RANGE_TO,
                             DHCPv6_RANGE_FROM, DHCPv6_RANGE_TO, IP_GATEWAY):

                with namedTemporaryDir(dir='/var/lib/dhclient') as dir:
                    dhclient_runner = dhcp.DhclientRunner(
                        client, family, dir, dateFormat)
                    try:
                        with running(dhclient_runner):
                            is_dhcpv4 = dhclient.is_active(client, family=4)
                            is_dhcpv6 = dhclient.is_active(client, family=6)
                    except dhcp.ProcessCannotBeKilled:
                        raise SkipTest('dhclient could not be killed')

        if family == 4:
            self.assertTrue(is_dhcpv4)
        else:
            self.assertTrue(is_dhcpv6)

    def testGetRouteDeviceTo(self):
        with dummyIf(1) as nics:
            nic, = nics

            addrAdd(nic, IP_ADDRESS, IP_CIDR)
            try:
                linkSet(nic, ['up'])
                self.assertEqual(getRouteDeviceTo(IP_ADDRESS_IN_NETWORK), nic)
            finally:
                addrFlush(nic)

            sysctl.disable_ipv6(nic, False)
            addrAdd(nic, IPv6_ADDRESS, IPv6_CIDR, family=6)
            try:
                linkSet(nic, ['up'])
                self.assertEqual(getRouteDeviceTo(IPv6_ADDRESS_IN_NETWORK),
                                 nic)
            finally:
                addrFlush(nic)

    @slowtest
    @cleanupNet
    def testHonorBlockingDhcp(self):
        status, msg = self.setupNetworks(
            {NETWORK_NAME: {'bridged': True, 'bootproto': 'dhcp',
                            'blockingdhcp': True}}, {}, NOCHK)
        # Without blocking dhcp, the setupNetworks command would return
        # reporting success before knowing if dhclient succeeded. With blocking
        # it must not report success
        self.assertNotEqual(status, SUCCESS, msg)
        self.assertBridgeDoesntExist(NETWORK_NAME)

    @slowtest
    @permutations([[True], [False]])
    @cleanupNet
    def testSetupNetworksEmergencyDevicesCleanupVlanOverwrite(self, bridged):
        with dummyIf(2) as nics:
            nic = nics[0]
            network = {NETWORK_NAME: {'vlan': VLAN_ID, 'bridged': bridged,
                                      'nic': nic}}
            status, msg = self.setupNetworks(network, {}, NOCHK)
            self.assertEqual(status, SUCCESS, msg)

            network = {NETWORK_NAME: {'vlan': VLAN_ID, 'bridged': True,
                                      'bonding': BONDING_NAME,
                                      'bootproto': 'dhcp',
                                      'blockingdhcp': True}}
            bonding = {BONDING_NAME: {'nics': nics}}
            status, msg = self.setupNetworks(network, bonding, NOCHK)
            self.assertNotEqual(status, SUCCESS, msg)
            if bridged:
                self.assertBridgeExists(NETWORK_NAME)
            else:
                self.assertBridgeDoesntExist(NETWORK_NAME)
            self.assertVlanExists(nic + '.' + VLAN_ID)
            self.assertBondDoesntExist(BONDING_NAME)

    @slowtest
    @permutations([[True], [False]])
    @cleanupNet
    def testSetupNetworksEmergencyDevicesCleanupBondOverwrite(self, bridged):
        with dummyIf(2) as nics:
            network = {NETWORK_NAME: {'bridged': bridged,
                                      'bonding': BONDING_NAME}}
            bonding = {BONDING_NAME: {'nics': nics}}
            status, msg = self.setupNetworks(network, bonding, NOCHK)
            self.assertEqual(status, SUCCESS, msg)

            network = {NETWORK_NAME: {'vlan': VLAN_ID, 'bridged': True,
                                      'bonding': BONDING_NAME,
                                      'bootproto': 'dhcp',
                                      'blockingdhcp': True}}
            bonding = {BONDING_NAME: {'nics': nics}}
            status, msg = self.setupNetworks(network, bonding, NOCHK)
            self.assertNotEqual(status, SUCCESS, msg)
            if bridged:
                self.assertBridgeExists(NETWORK_NAME)
            else:
                self.assertBridgeDoesntExist(NETWORK_NAME)
            self.assertVlanDoesntExist(NETWORK_NAME + '.' + VLAN_ID)
            self.assertBondExists(BONDING_NAME, nics)

            status, msg = self.setupNetworks({NETWORK_NAME: {'remove': True}},
                                             {BONDING_NAME: {'remove': True}},
                                             NOCHK)
            self.assertEqual(status, SUCCESS, msg)

    @cleanupNet
    def testSetupNetworksOverDhcpIface(self):
        """When asked to setupNetwork on top of an interface with a running
        dhclient process, Vdsm is expected to stop that dhclient and start
        owning the interface. BZ#1100264"""
        def _get_dhclient_ifaces():
            pids = pgrep('dhclient')
            return [open('/proc/%s/cmdline' % pid).read().strip('\0')
                    .split('\0')[-1] for pid in pids]

        with veth_pair() as (server, client):
            addrAdd(server, IP_ADDRESS, IP_CIDR)
            linkSet(server, ['up'])
            with dnsmasq_run(server, DHCP_RANGE_FROM, DHCP_RANGE_TO,
                             DHCPv6_RANGE_FROM, DHCPv6_RANGE_TO, IP_GATEWAY):
                with namedTemporaryDir(dir='/var/lib/dhclient') as dhdir:
                    # Start a non-vdsm owned dhclient for the 'client' iface
                    dhclient_runner = dhcp.DhclientRunner(
                        client, 4, dhdir, 'default')
                    with running(dhclient_runner):
                        # Set up a network over it and wait for dhcp success
                        status, msg = self.setupNetworks(
                            {
                                NETWORK_NAME: {
                                    'nic': client, 'bridged': False,
                                    'bootproto': 'dhcp',
                                    'blockingdhcp': True
                                }
                            },
                            {},
                            NOCHK)
                        self.assertEqual(status, SUCCESS, msg)
                        self.assertNetworkExists(NETWORK_NAME)

                        # Verify that dhclient is running for the device
                        ifaces = _get_dhclient_ifaces()
                        vdsm_dhclient = [iface for iface in ifaces if
                                         iface == client]
                        self.assertEqual(len(vdsm_dhclient), 1,
                                         'There should be one and only one '
                                         'running dhclient for the device')

            # cleanup
            self.setupNetworks({NETWORK_NAME: {'remove': True}}, {}, NOCHK)

    @cleanupNet
    def testSetupNetworksRemoveSlavelessBond(self):
        with dummyIf(2) as nics:
            status, msg = self.setupNetworks(
                {NETWORK_NAME:
                    {'bonding': BONDING_NAME, 'bridged': False}},
                {BONDING_NAME: {'nics': nics}}, NOCHK)
            self.assertEqual(status, SUCCESS, msg)
            self.assertNetworkExists(NETWORK_NAME)
            self.assertBondExists(BONDING_NAME, nics)

            with open(BONDING_SLAVES % BONDING_NAME, 'w') as f:
                for nic in nics:
                    f.write('-%s\n' % nic)

            status, msg = self.setupNetworks(
                {NETWORK_NAME: {'remove': True}},
                {BONDING_NAME: {'remove': True}}, NOCHK)
            self.assertEqual(status, SUCCESS, msg)
            self.assertNetworkDoesntExist(NETWORK_NAME)
            self.assertBondDoesntExist(BONDING_NAME, nics)

    @requiresUnifiedPersistence("with ifcfg persistence, "
                                "restoreNetConfig doesn't restore "
                                "in-kernel state")
    @cleanupNet
    @ValidateRunningAsRoot
    def test_setupNetworks_on_external_bond(self):
        with dummyIf(2) as nics:
            with _create_external_bond(BONDING_NAME, nics):
                status, msg = self.setupNetworks(
                    {NETWORK_NAME:
                        {'bonding': BONDING_NAME, 'bridged': False}},
                    {BONDING_NAME: {'nics': nics}}, NOCHK)
                self.assertEqual(status, SUCCESS, msg)
                self.assertNetworkExists(NETWORK_NAME)
                self.assertBondExists(BONDING_NAME, nics)

            self.vdsm_net.save_config()
            self.vdsm_net.restoreNetConfig()

            self.assertNetworkExists(NETWORK_NAME)
            self.assertBondExists(BONDING_NAME, nics)

            status, msg = self.setupNetworks(
                {NETWORK_NAME: {'remove': True}},
                {BONDING_NAME: {'remove': True}}, NOCHK)
            self.assertEqual(status, SUCCESS, msg)
            self.assertNetworkDoesntExist(NETWORK_NAME)
            self.assertBondDoesntExist(BONDING_NAME, nics)
            self.vdsm_net.save_config()

    @requiresUnifiedPersistence("with ifcfg persistence, "
                                "restoreNetConfig doesn't restore "
                                "in-kernel state")
    @cleanupNet
    @ValidateRunningAsRoot
    def test_setupNetworks_on_external_vlaned_bond(self):
        with dummyIf(2) as nics:
            with self._create_external_ifcfg_bond(BONDING_NAME, nics, VLAN_ID):
                status, msg = self.setupNetworks(
                    {NETWORK_NAME: {'bonding': BONDING_NAME, 'bridged': True,
                                    'vlan': VLAN_ID}}, {}, NOCHK)
                self.assertEqual(status, SUCCESS, msg)
                self.assertNetworkExists(NETWORK_NAME)

            self.vdsm_net.save_config()
            self.vdsm_net.restoreNetConfig()

            self.assertNetworkExists(NETWORK_NAME)
            self.assertBondExists(BONDING_NAME, nics)
            self.assertVlanExists(BONDING_NAME + '.' + VLAN_ID)

            status, msg = self.setupNetworks(
                {NETWORK_NAME: {'remove': True}},
                {BONDING_NAME: {'remove': True}}, NOCHK)
            self.assertEqual(status, SUCCESS, msg)
            self.assertNetworkDoesntExist(NETWORK_NAME)
            self.assertBondDoesntExist(BONDING_NAME, nics)
            self.vdsm_net.save_config()

    @contextmanager
    def _create_external_ifcfg_bond(self, bond_name, nics, vlan_id):
        IFCFG_SLAVE_TEMPLATE = """DEVICE=%s
MASTER=%s
SLAVE=yes
ONBOOT=yes
MTU=1500
NM_CONTROLLED=no"""

        IFCFG_BOND_TEMPLATE = """DEVICE=%s
BONDING_OPTS='mode=802.3ad miimon=150'
ONBOOT=yes
BOOTPROTO=none
DEFROUTE=yes
NM_CONTROLLED=no
HOTPLUG=no"""

        IFCFG_VLAN_TEMPLATE = """DEVICE=%s.%s
VLAN=yes
ONBOOT=yes
BOOTPROTO=static
NM_CONTROLLED=no
HOTPLUG=no"""

        with open(NET_CONF_PREF + nics[0], 'w') as f:
            f.write(IFCFG_SLAVE_TEMPLATE % (nics[0], bond_name))
        with open(NET_CONF_PREF + nics[1], 'w') as f:
            f.write(IFCFG_SLAVE_TEMPLATE % (nics[1], bond_name))
        with open(NET_CONF_PREF + bond_name, 'w') as f:
            f.write(IFCFG_BOND_TEMPLATE % bond_name)
        with open(NET_CONF_PREF + bond_name + '.' + vlan_id, 'w') as f:
            f.write(IFCFG_VLAN_TEMPLATE % (bond_name, vlan_id))

        commands.run([EXT_IFUP, bond_name])
        commands.run([EXT_IFUP, bond_name + '.' + vlan_id])

        try:
            yield
        finally:
            commands.run([EXT_IFDOWN, bond_name + '.' + vlan_id])
            commands.run([EXT_IFDOWN, bond_name])

            # The bond needs to be removed by force
            with open(BONDING_MASTERS, 'w') as bonds:
                bonds.write('-%s\n' % bond_name)

            os.remove(NET_CONF_PREF + nics[0])
            os.remove(NET_CONF_PREF + nics[1])
            os.remove(NET_CONF_PREF + bond_name)
            os.remove(NET_CONF_PREF + bond_name + '.' + vlan_id)


@contextmanager
def _create_external_bond(name, slaves):
    with open(BONDING_MASTERS, 'w') as bonds:
        bonds.write('+%s\n' % name)
    try:
        with open(BONDING_SLAVES % BONDING_NAME, 'w') as f:
            for slave in slaves:
                linkSet(slave, ['down'])
                f.write('+%s\n' % slave)
        yield
    finally:
        with open(BONDING_MASTERS, 'w') as bonds:
            bonds.write('-%s\n' % name)
