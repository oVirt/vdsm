#
# Copyright 2013-2020 Red Hat, Inc.
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
from vdsm.network import netswitch
from vdsm.network.ipwrapper import (
    routeExists, ruleExists, LinkType, getLinks, routeShowTable)
from vdsm.network import kernelconfig
from vdsm.network.netinfo.nics import operstate, OPERSTATE_UNKNOWN
from vdsm.network.netlink import monitor
from vdsm.network import sourceroute

from vdsm.common.cmdutils import CommandPath
from vdsm.utils import RollbackContext

from hookValidation import ValidatesHook

from modprobe import RequireDummyMod
from testlib import (VdsmTestCase as TestCaseBase, expandPermutations)
from testValidation import ValidateRunningAsRoot
from network.nettestlib import Dummy
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
    with monitor.object_monitor(groups=('link',), timeout=timeout) as mon:
        if operstate(device) == OPERSTATE_UNKNOWN:
            for event in mon:
                if (event['name'] == device and
                        event['state'] != OPERSTATE_UNKNOWN):
                    break


def _waitForOperstate(device, state, timeout=1):
    """ :param state: please use OPERSTATE_* from lib/vdsm/network/netinfo
    """
    with monitor.object_monitor(groups=('link',), timeout=timeout) as mon:
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
        with monitor.object_monitor(groups=('link',)) as mon:
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
