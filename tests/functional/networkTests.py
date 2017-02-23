#
# Copyright 2013-2016 Red Hat, Inc.
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
import json
import re
import os.path
import six

import netaddr
from nose import with_setup
from nose.plugins.skip import SkipTest

import vdsm.config
from vdsm.constants import EXT_BRCTL, EXT_IFUP, EXT_IFDOWN
from vdsm.network import ipwrapper
from vdsm.network import libvirt
from vdsm.network.ip import dhclient
from vdsm.network.ipwrapper import (
    routeExists, ruleExists, addrFlush, LinkType, getLinks, routeShowTable,
    linkDel, linkSet, addrAdd)
from vdsm.network import kernelconfig
from vdsm.network.netinfo.bonding import BONDING_SLAVES, BONDING_MASTERS
from vdsm.network.netinfo.bridges import bridges
from vdsm.network.netinfo.misc import NET_CONF_PREF
from vdsm.network.netinfo.mtus import DEFAULT_MTU
from vdsm.network.netinfo.nics import (operstate, OPERSTATE_UNKNOWN,
                                       OPERSTATE_UP)
from vdsm.network.netinfo.routes import getDefaultGateway, getRouteDeviceTo
from vdsm.network.netlink import monitor
from vdsm.network.configurators.ifcfg import (Ifcfg, stop_devices,
                                              NET_CONF_BACK_DIR)
from vdsm.network import errors
from vdsm.network import legacy_switch
from vdsm.network import netswitch
from vdsm.network import sourceroute
from vdsm.network import tc

from vdsm import sysctl
from vdsm.commands import execCmd
from vdsm.utils import CommandPath, RollbackContext, pgrep, running

from hookValidation import ValidatesHook

from modprobe import RequireDummyMod, RequireVethMod
from testlib import (VdsmTestCase as TestCaseBase, namedTemporaryDir,
                     expandPermutations, permutations)
from testValidation import brokentest, slowtest, ValidateRunningAsRoot
from network.nettestlib import Dummy, Tap, veth_pair, dnsmasq_run
from network import dhcp
from utils import SUCCESS, getProxy

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

    unified = (
        vdsm.config.config.get('vars', 'net_persistence') == 'unified')
    if unified:
        running_config, kernel_config = _get_running_and_kernel_config(
            vds.config)
        if ((running_config['networks'] != kernel_config['networks']) or
                (running_config['bonds'] != kernel_config['bonds'])):
            raise SkipTest(
                "Tested host is not clean (running vs kernel): "
                "networks: %r != %r; "
                "bonds: %r != %r" % (
                    running_config['networks'], kernel_config['networks'],
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
    netinfo = vdsm.network.netinfo.cache.NetInfo(netswitch.netinfo())
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
            for opt, value in bridgeOpts.iteritems():
                self.assertEqual(value, appliedOpts[opt])
        if hostQos is not None:
            reported_qos = network_netinfo['hostQos']
            _cleanup_qos_definition(reported_qos)
            self.assertEqual(reported_qos, hostQos)

        if not vdsm.config.config.get('vars', 'net_persistence') == 'unified':
            return

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
        self.assertFalse(libvirt.is_libvirt_network(networkName))

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

    def _assert_exact_bond_opts(self, bond, opts):
        """:param opts: list of strings e.g. ['miimon=150', 'mode=4']"""
        # TODO: we should try and call this logic always during
        # TODO: assertBondExists and be stricter. Will probably need to fix a
        # TODO: few tests
        self.assertEqual(
            set(self._get_active_bond_opts(bond)) - set(["mode=0"]),
            set(opts) - set(["mode=0"]))

    def _get_active_bond_opts(self, bondName):
        netinfo = self.vdsm_net.netinfo
        active_options = [opt + '=' + val for (opt, val)
                          in netinfo.bondings[bondName]['opts'].iteritems()]
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

    def assertMtu(self, mtu, *elems):
        # Due to compatibility with engine, the expected mtu type is string
        # REQUIRED_FOR engine < 3.7
        mtu = str(mtu)
        for elem in elems:
            self.assertEqual(mtu, self.vdsm_net.getMtu(elem))

    def assert_active_slave_exists(self, bondName, nics):
        netinfo = self.vdsm_net.netinfo
        self.assertIn(bondName, netinfo.bondings)
        self.assertIn(netinfo.bondings[bondName]['active_slave'], nics)

    def assert_active_slave_doesnt_exist(self, bondName):
        netinfo = self.vdsm_net.netinfo
        self.assertIn(bondName, netinfo.bondings)
        self.assertEqual(netinfo.bondings[bondName]['active_slave'], '')

    def setupNetworks(self, networks, bonds, options, test_kernel_config=True):
        status, msg = self.vdsm_net.setupNetworks(networks, bonds, options)
        unified = (
            vdsm.config.config.get('vars', 'net_persistence') == 'unified')
        if unified and test_kernel_config:
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
    @RequireVethMod
    @ValidateRunningAsRoot
    @brokentest('This test fails because of #1261457')
    def test_getVdsStats(self):
        """This Test will send an ARP request on a created veth interface
        and checks that the TX bytes is in range between 42 and 384 bytes
        the range is set due to DHCP packets that may corrupt the statistics"""
        # TODO disable DHCP service on the veth
        ARP_REQUEST_SIZE = 42
        DHCP_PACKET_SIZE = 342

        def assertTestDevStatsReported():
            status, msg, hostStats = self.vdsm_net.getVdsStats()
            self.assertEqual(status, SUCCESS, msg)
            self.assertIn('network', hostStats)
            self.assertIn(
                left, hostStats['network'], 'could not find veth %s' % left)

        def getStatsFromInterface(iface):
            status, msg, hostStats = self.vdsm_net.getVdsStats()
            self.assertEqual(status, SUCCESS, msg)
            self.assertIn('network', hostStats)
            self.assertIn(iface, hostStats['network'])
            self.assertIn('tx', hostStats['network'][iface])
            self.assertIn('rx', hostStats['network'][iface])
            self.assertIn('sampleTime', hostStats['network'][iface])
            return (int(hostStats['network'][iface]['tx']),
                    hostStats['network'][iface]['sampleTime'])

        def assertStatsInRange():
            curTxStat, curTime = getStatsFromInterface(left)
            self.assertTrue(
                curTime > prevTime,
                'sampleTime is not monotonically increasing')

            diff = (curTxStat - prevTxStat)
            self.assertTrue(ARP_REQUEST_SIZE <= diff <=
                            (ARP_REQUEST_SIZE + DHCP_PACKET_SIZE),
                            '%s is out of range' % diff)

        with veth_pair() as (left, right):
            # disabling IPv6 on Interface for removal of Router Solicitation
            sysctl.disable_ipv6(left)
            sysctl.disable_ipv6(right)
            linkSet(left, ['up'])
            linkSet(right, ['up'])

            # Vdsm scans for new devices every 15 seconds
            self.retryAssert(
                assertTestDevStatsReported, timeout=20)

            prevTxStat, prevTime = getStatsFromInterface(left)
            # running ARP from the interface
            rc, out, err = execCmd([_ARPPING_COMMAND.cmd, '-D', '-I', left,
                                    '-c', '1', IP_ADDRESS_IN_NETWORK])
            if rc != 0:
                raise SkipTest('Could not run arping', out, err)

            # wait for Vdsm to update statistics
            self.retryAssert(assertStatsInRange, timeout=3)

    @cleanupNet
    @permutations([[True], [False]])
    def testSetupNetworksAddOverExistingBond(self, bridged=True):
        with dummyIf(2) as nics:
            status, msg = self.setupNetworks(
                {NETWORK_NAME + '0': {'bonding': BONDING_NAME,
                                      'bridged': False}},
                {BONDING_NAME: {'nics': nics}},
                NOCHK)
            self.assertEqual(status, SUCCESS, msg)
            self.assertBondExists(BONDING_NAME, nics)

            _waitForOperstate(BONDING_NAME, OPERSTATE_UP)
            with nonChangingOperstate(BONDING_NAME):
                status, msg = self.setupNetworks(
                    {NETWORK_NAME:
                        {'bonding': BONDING_NAME, 'bridged': bridged,
                         'vlan': VLAN_ID}},
                    {}, NOCHK)
            self.assertEqual(status, SUCCESS, msg)
            self.assertNetworkExists(NETWORK_NAME, bridged)

            status, msg = self.setupNetworks(
                {NETWORK_NAME: {'remove': True},
                 NETWORK_NAME + '0': {'remove': True}},
                {}, NOCHK)
            self.assertEqual(status, SUCCESS, msg)
            self.assertBondExists(BONDING_NAME, nics)

            status, msg = self.setupNetworks(
                {},
                {BONDING_NAME: {'remove': True}}, NOCHK)
            self.assertEqual(status, SUCCESS, msg)

    @cleanupNet
    def testSetupNetworksDelOneOfBondNets(self):
        NETA_NAME = NETWORK_NAME + 'A'
        NETB_NAME = NETWORK_NAME + 'B'
        NETA_DICT = {'bonding': BONDING_NAME, 'bridged': False, 'mtu': '1600',
                     'vlan': '4090'}
        NETB_DICT = {'bonding': BONDING_NAME, 'bridged': False, 'mtu': '2000',
                     'vlan': '4091'}
        with dummyIf(2) as nics:
            status, msg = self.setupNetworks(
                {NETA_NAME: NETA_DICT,
                 NETB_NAME: NETB_DICT},
                {BONDING_NAME: {'nics': nics}}, NOCHK)
            self.assertEqual(status, SUCCESS, msg)
            self.assertNetworkExists(NETA_NAME)
            self.assertNetworkExists(NETB_NAME)
            self.assertBondExists(BONDING_NAME, nics)
            self.assertMtu(NETB_DICT['mtu'], BONDING_NAME)

            _waitForOperstate(BONDING_NAME, OPERSTATE_UP)
            with nonChangingOperstate(BONDING_NAME):
                status, msg = self.setupNetworks(
                    {NETB_NAME: {'remove': True}}, {}, NOCHK)

            self.assertEqual(status, SUCCESS, msg)
            self.assertNetworkExists(NETA_NAME)
            self.assertNetworkDoesntExist(NETB_NAME)
            # Check that the mtu of the bond has been adjusted to the smaller
            # NETA value
            self.assertMtu(NETA_DICT['mtu'], BONDING_NAME)

            status, msg = self.setupNetworks(
                {NETA_NAME: {'remove': True}},
                {BONDING_NAME: {'remove': True}}, NOCHK)
            self.assertEqual(status, SUCCESS, msg)

    @cleanupNet
    @permutations([[True], [False]])
    def testReorderBondingOptions(self, bridged):
        with dummyIf(2) as nics:
            nets = {NETWORK_NAME: {
                'bridged': bridged, 'bonding': BONDING_NAME}}
            bonds = {BONDING_NAME: {'nics': nics,
                                    'options': 'lacp_rate=fast mode=802.3ad'}}

            status, msg = self.setupNetworks(nets, bonds, NOCHK)
            self.assertEqual(status, SUCCESS, msg)

            self.assertNetworkExists(NETWORK_NAME, bridged)
            self.assertBondExists(BONDING_NAME, nics)

            status, msg = self.setupNetworks(
                {NETWORK_NAME: {'remove': True}},
                {BONDING_NAME: {'remove': True}}, NOCHK)
            self.assertEqual(status, SUCCESS, msg)
            self.assertNetworkDoesntExist(NETWORK_NAME)

    @cleanupNet
    @permutations([[True], [False]])
    def testFailWithInvalidBondingName(self, bridged):
        with dummyIf(2) as nics:
            invalid_bond_names = ('bond', 'bonda', 'bond0a', 'jamesbond007')
            for bond_name in invalid_bond_names:
                status, msg = self.setupNetworks(
                    {NETWORK_NAME: {'bonding': bond_name,
                                    'bridged': bridged}},
                    {bond_name: {'nics': nics}}, NOCHK)
                self.assertEqual(status, errors.ERR_BAD_BONDING, msg)

    @cleanupNet
    def testFailWithInvalidBridgeName(self):
        invalid_bridge_names = ('a' * 16, 'a b', 'a\tb', 'a.b', 'a:b')
        for bridge_name in invalid_bridge_names:
            status, msg = self.setupNetworks({bridge_name: {}}, {}, NOCHK)
            self.assertEqual(status, errors.ERR_BAD_BRIDGE, msg)

    @cleanupNet
    def testFailWithInvalidIpConfig(self):
        invalid_ip_configs = (dict(ipaddr='1.2.3.4'), dict(netmask='1.2.3.4'),
                              dict(gateway='1.2.3.4'),
                              dict(ipaddr='1.2.3', netmask='255.255.0.0'),
                              dict(ipaddr='1.2.3.256', netmask='255.255.0.0'),
                              dict(ipaddr='1.2.3.4', netmask='256.255.0.0'),
                              dict(ipaddr='1.2.3.4.5', netmask='255.255.0.0'),
                              dict(ipaddr='1.2.3.4', netmask='255.255.0.0',
                                   gateway='1.2.3.256'),
                              )
        for ipconfig in invalid_ip_configs:
            status, msg = self.setupNetworks({NETWORK_NAME: ipconfig},
                                             {}, NOCHK)
            self.assertEqual(status, errors.ERR_BAD_ADDR, msg)

    @cleanupNet
    @permutations([[True], [False]])
    def testFailWithInvalidNic(self, bridged):
        status, msg = self.setupNetworks(
            {NETWORK_NAME: {'nic': 'nowaythisnicexists', 'bridged': bridged}},
            {}, NOCHK)
        self.assertEqual(status, errors.ERR_BAD_NIC, msg)

    @cleanupNet
    @permutations([[True], [False]])
    def testFailWithInvalidParams(self, bridged):
        status, msg = self.setupNetworks(
            {NETWORK_NAME: {'vlan': VLAN_ID, 'bridged': bridged}}, {}, NOCHK)
        self.assertEqual(status, errors.ERR_BAD_PARAMS, msg)

        status, msg = self.setupNetworks(
            {NETWORK_NAME: {'bonding': BONDING_NAME, 'bridged': bridged}},
            {BONDING_NAME: {'nics': []}}, NOCHK)
        self.assertEqual(status, errors.ERR_BAD_PARAMS, msg)

    def _setup_overExistingBridge():
        rc, _, err = execCmd([EXT_BRCTL, 'addbr', NETWORK_NAME])
        if rc != 0:
            raise errors.ConfigNetworkError(errors.ERR_FAILED_IFUP, err)

    def _teardown_overExistingBridge():
        if os.path.exists('/sys/class/net/%s/bridge' % NETWORK_NAME):
            rc, _, err = execCmd([EXT_BRCTL, 'delbr', NETWORK_NAME])
            if rc != 0:
                raise errors.ConfigNetworkError(errors.ERR_FAILED_IFDOWN, err)

    @cleanupNet
    @with_setup(_setup_overExistingBridge, _teardown_overExistingBridge)
    def testSetupNetworksOverExistingBridge(self):
        status, msg = self.setupNetworks(
            {NETWORK_NAME: {'bridged': True}}, {}, NOCHK)
        self.assertEqual(status, SUCCESS, msg)
        self.assertNetworkExists(NETWORK_NAME, True)
        status, msg = self.setupNetworks(
            {NETWORK_NAME: {'remove': True}}, {}, NOCHK)
        self.assertEqual(status, SUCCESS, msg)

    @cleanupNet
    @permutations([[True], [False]])
    def testDelNetworkWithMTU(self, bridged):
        MTU = 1280  # required for sysctl.disable_ipv6() on the bridge
        with dummyIf(2) as nics:
            status, msg = self.setupNetworks(
                {NETWORK_NAME: {'vlan': VLAN_ID, 'bonding': BONDING_NAME,
                                'mtu': MTU, 'bridged': bridged}},
                {BONDING_NAME: {'nics': nics}}, NOCHK)
            vlan_name = '%s.%s' % (BONDING_NAME, VLAN_ID)

            self.assertEqual(status, SUCCESS, msg)
            self.assertMtu(MTU, NETWORK_NAME, vlan_name, BONDING_NAME, nics[0])

            status, msg = self.setupNetworks(
                {NETWORK_NAME: {'remove': True}}, {}, NOCHK)
            self.assertEqual(status, SUCCESS, msg)

    @cleanupNet
    @permutations([[True], [False]])
    def testTwiceAdd(self, bridged):
        with dummyIf(1) as nics:
            nic, = nics
            net = {NETWORK_NAME: {'nic': nic, 'bridged': bridged}}
            status, msg = self.setupNetworks(net, {}, NOCHK)
            self.assertEqual(status, SUCCESS, msg)

            status, msg = self.setupNetworks(net, {}, NOCHK)
            self.assertEqual(status, SUCCESS, msg)

            status, msg = self.setupNetworks(
                {NETWORK_NAME: {'remove': True}}, {}, NOCHK)
            self.assertEqual(status, SUCCESS, msg)

    @cleanupNet
    def testDelWithoutAdd(self):
        status, msg = self.setupNetworks(
            {NETWORK_NAME: {'remove': True}}, {}, NOCHK)
        self.assertEqual(status, errors.ERR_BAD_BRIDGE, msg)

    @cleanupNet
    @permutations([[True], [False]])
    def testSetupNetworksAddVlan(self, bridged):
        BRIDGE_OPTS = {'multicast_router': '0', 'multicast_snooping': '0'}
        formattedOpts = ' '.join(
            ['='.join(elem) for elem in BRIDGE_OPTS.items()])
        with dummyIf(1) as nics:
            nic, = nics
            attrs = {'vlan': VLAN_ID, 'nic': nic, 'bridged': bridged,
                     'custom': {'bridge_opts': formattedOpts}}
            status, msg = self.setupNetworks(
                {NETWORK_NAME: attrs}, {}, NOCHK, test_kernel_config=False)

            self.assertEqual(status, SUCCESS, msg)
            self.assertNetworkExists(NETWORK_NAME, bridgeOpts=BRIDGE_OPTS)
            self.assertVlanExists('%s.%s' % (nic, VLAN_ID))

            status, msg = self.setupNetworks({NETWORK_NAME: dict(remove=True)},
                                             {}, NOCHK)
            self.assertEqual(status, SUCCESS, msg)

    @cleanupNet
    def testSetupNetworksNicless(self):
        status, msg = self.setupNetworks(
            {NETWORK_NAME: {'bridged': True, 'stp': True}}, {},
            NOCHK)
        self.assertEqual(status, SUCCESS, msg)
        self.assertNetworkExists(NETWORK_NAME)
        self.assertEqual(self.vdsm_net.netinfo.bridges[NETWORK_NAME]['stp'],
                         'on')

        status, msg = self.setupNetworks({NETWORK_NAME: dict(remove=True)}, {},
                                         NOCHK)
        self.assertEqual(status, SUCCESS, msg)

    @cleanupNet
    def testSetupNetworksNiclessBridgeless(self):
        status, msg = self.setupNetworks({NETWORK_NAME: {'bridged': False}},
                                         {}, NOCHK)
        self.assertEqual(status, errors.ERR_BAD_PARAMS, msg)

    @cleanupNet
    def testSetupNetworksConvertVlanNetBridgeness(self):
        """Convert a bridged networks to a bridgeless one and viceversa"""

        def setupNetworkBridged(nic, bridged):
            networks = {NETWORK_NAME: dict(vlan=VLAN_ID,
                                           nic=nic, bridged=bridged)}
            status, msg = self.setupNetworks(networks, {}, NOCHK)
            self.assertEqual(status, SUCCESS, msg)
            self.assertNetworkExists(NETWORK_NAME, bridged)

        with dummyIf(1) as (nic, ):
            setupNetworkBridged(nic, True)
            setupNetworkBridged(nic, False)
            setupNetworkBridged(nic, True)

            status, msg = self.setupNetworks({NETWORK_NAME: dict(remove=True)},
                                             {}, NOCHK)

        self.assertEqual(status, SUCCESS, msg)

    @permutations([[True], [False]])
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

            status, msg = self.setupNetworks(networks, {}, NOCHK)
            self.assertEqual(status, SUCCESS, msg)

            for vlan_net, tag in NET_VLANS:
                self.assertNetworkExists(vlan_net, bridged)
                self.assertVlanExists(nic + '.' + tag)

            networks = dict((vlan_net, {'remove': True})
                            for vlan_net, _ in NET_VLANS)
            status, msg = self.setupNetworks(networks, {}, NOCHK)

            self.assertEqual(status, SUCCESS, msg)

            for vlan_net, tag in NET_VLANS:
                self.assertNetworkDoesntExist(vlan_net)
                self.assertVlanDoesntExist(nic + '.' + tag)

    @cleanupNet
    @permutations([[True], [False]])
    def testSetupNetworksNetCompatibilityMultipleNetsSameNic(self, bridged):
        with dummyIf(3) as (nic, another_nic, yet_another_nic):

            net_untagged = NETWORK_NAME
            networks = {net_untagged: dict(nic=nic, bridged=bridged)}
            status, msg = self.setupNetworks(networks, {}, NOCHK)
            self.assertEqual(status, SUCCESS, msg)
            self.assertNetworkExists(net_untagged, bridged=bridged)

            other_net_untagged = NETWORK_NAME + '2'
            networks = {other_net_untagged: dict(nic=nic, bridged=bridged)}
            status, msg = self.setupNetworks(networks, {}, NOCHK)
            self.assertNotEqual(status, SUCCESS, msg)
            self.assertNetworkDoesntExist(other_net_untagged)

            net_tagged = NETWORK_NAME + '3'
            networks = {net_tagged: dict(nic=nic, bridged=bridged, vlan='100')}
            status, msg = self.setupNetworks(networks, {}, NOCHK)
            self.assertEqual(status, SUCCESS, msg)
            self.assertNetworkExists(net_tagged, bridged=bridged)

            other_net_same_tag = NETWORK_NAME + '4'
            networks = {other_net_same_tag: dict(nic=nic, bridged=bridged,
                                                 vlan='100')}
            status, msg = self.setupNetworks(networks, {}, NOCHK)
            self.assertNotEqual(status, SUCCESS, msg)
            self.assertNetworkDoesntExist(other_net_same_tag)

            networks = {other_net_same_tag: dict(nic=another_nic,
                                                 bridged=bridged, vlan='100')}
            status, msg = self.setupNetworks(networks, {}, NOCHK)
            self.assertEqual(status, SUCCESS, msg)
            self.assertNetworkExists(other_net_same_tag)

            other_net_different_tag = NETWORK_NAME + '5'
            networks = {other_net_different_tag: dict(nic=nic, bridged=bridged,
                                                      vlan='200')}
            status, msg = self.setupNetworks(networks, {}, NOCHK)
            self.assertEqual(status, SUCCESS, msg)
            self.assertNetworkExists(other_net_different_tag, bridged=bridged)

            nets_to_clean = [net_untagged, net_tagged, other_net_same_tag,
                             other_net_different_tag]
            # we can also define an untagged bridged and a tagged bridged
            # networks on the same interface at the same time
            if bridged:
                yet_another_bridged = NETWORK_NAME + '6'
                yet_another_tagged_bridged = NETWORK_NAME + '6'
                networks = {yet_another_bridged: dict(nic=yet_another_nic,
                                                      bridged=True),
                            yet_another_tagged_bridged: dict(
                                nic=yet_another_nic, bridged=True, vlan='300')}
                status, msg = self.setupNetworks(networks, {}, NOCHK)
                self.assertEqual(status, SUCCESS, msg)
                self.assertNetworkExists(yet_another_bridged, bridged=True)
                self.assertNetworkExists(yet_another_tagged_bridged,
                                         bridged=True)

                nets_to_clean += [yet_another_bridged,
                                  yet_another_tagged_bridged]

            # Clean all
            networks = dict((net, dict(remove=True)) for net in nets_to_clean)
            status, msg = self.setupNetworks(networks, {}, NOCHK)
            self.assertEqual(status, SUCCESS, msg)

            for net in nets_to_clean:
                self.assertNetworkDoesntExist(net)

    @cleanupNet
    @RequireDummyMod
    @ValidateRunningAsRoot
    def testSetupNetworksDeletesTheBridgeOnlyWhenItIsReconfigured(self):
        def get_bridge_index():
            link = ipwrapper.getLink(NETWORK_NAME)
            return link.index

        def add_tap_to_bridge():
            tap = Tap(prefix='vnet')
            tap.addDevice()
            rc, _, _ = execCmd([EXT_BRCTL, 'addif', NETWORK_NAME, tap.devName])
            self.assertEqual(rc, 0, 'brctl addif failed: rc=%s' % (rc,))
            return tap

        STANDARD, BIG = 1500, 2000
        with dummyIf(2) as nics:
            first, second = nics
            first_net = {NETWORK_NAME: dict(bridged=True, nic=first,
                                            mtu=STANDARD)}
            status, msg = self.setupNetworks(first_net, {}, NOCHK)
            self.assertEqual(status, SUCCESS, msg)
            self.assertMtu(STANDARD, NETWORK_NAME, first)
            bridge_index = get_bridge_index()
            # simulate a vm connected to the bridge
            tap = add_tap_to_bridge()
            try:
                second_net = {NETWORK_NAME: dict(bridged=True, nic=second,
                                                 mtu=BIG, vlan=VLAN_ID)}
                status, msg = self.setupNetworks(second_net, {}, NOCHK)
                self.assertEqual(status, SUCCESS, msg)
                second_bridge_index = get_bridge_index()
                self.assertEqual(bridge_index, second_bridge_index)
            finally:
                tap.delDevice()
            # the kernel bridge driver automatically updates the bridge to the
            # new minimum MTU of all of its connected interfaces
            self.assertMtu(BIG, NETWORK_NAME, second)

            if legacy_switch.ConfiguratorClass == Ifcfg:
                # verify that the ifcfg configuration files are also updated
                # with the new MTU
                rc, _, _ = execCmd([EXT_IFDOWN, NETWORK_NAME])
                self.assertEqual(rc, 0, 'ifdown failed: rc=%s' % (rc,))
                rc, _, _ = execCmd([EXT_IFUP, NETWORK_NAME])
                self.assertEqual(rc, 0, 'ifup failed: rc=%s' % (rc,))
                self.vdsm_net.refreshNetinfo()
                self.assertMtu(BIG, NETWORK_NAME, second)

            third_net = {
                NETWORK_NAME: dict(bridged=True, nic=second, mtu=BIG,
                                   ipaddr=IP_ADDRESS, netmask=IP_MASK)}
            status, msg = self.setupNetworks(third_net, {}, NOCHK)
            self.assertEqual(status, SUCCESS, msg)
            self.assertNotEqual(second_bridge_index, get_bridge_index())

            status, msg = self.setupNetworks({NETWORK_NAME: {'remove': True}},
                                             {}, NOCHK)
            self.assertEqual(status, SUCCESS, msg)

    @cleanupNet
    @permutations([[True], [False]])
    def testSetupNetworksAddNetworkToNicAfterBondResizing(self, bridged):
        with dummyIf(3) as nics:
            networks = {NETWORK_NAME: dict(bonding=BONDING_NAME,
                                           bridged=bridged)}
            status, msg = self.setupNetworks(
                networks, {BONDING_NAME: dict(nics=nics)}, NOCHK)

            self.assertEqual(status, SUCCESS, msg)

            self.assertNetworkExists(NETWORK_NAME, bridged=bridged)
            self.assertBondExists(BONDING_NAME, nics)

            # Reduce bond size and create Network on detached NIC
            _waitForOperstate(BONDING_NAME, OPERSTATE_UP)
            with nonChangingOperstate(BONDING_NAME):
                netName = NETWORK_NAME + '-2'
                networks = {netName: dict(nic=nics[0],
                                          bridged=bridged)}
                bondings = {BONDING_NAME: dict(nics=nics[1:3])}
                status, msg = self.setupNetworks(networks, bondings, NOCHK)

                self.assertEqual(status, SUCCESS, msg)

                self.assertNetworkExists(NETWORK_NAME, bridged=bridged)
                self.assertNetworkExists(netName, bridged=bridged)
                self.assertBondExists(BONDING_NAME, nics[1:3])

            # Clean up
            networks = {NETWORK_NAME: dict(remove=True),
                        netName: dict(remove=True)}
            bondings = {BONDING_NAME: dict(remove=True)}
            status, msg = self.setupNetworks(networks, bondings, NOCHK)
            self.assertEqual(status, SUCCESS, msg)

    @cleanupNet
    @permutations([[True], [False]])
    def testSetupNetworksMtus(self, bridged):
        JUMBO = 9000
        MIDI = 4000

        with dummyIf(3) as nics:
            # Add two networks, one with default MTU and the other with MIDI.
            # Expect to see nics MTU to be MIDI. (MIDI > DEFAULT MTU)
            networks = {NETWORK_NAME + '1':
                        dict(bonding=BONDING_NAME, bridged=bridged,
                             vlan='100'),
                        NETWORK_NAME + '2':
                        dict(bonding=BONDING_NAME, bridged=bridged,
                             vlan='200', mtu=MIDI)
                        }
            bondings = {BONDING_NAME: dict(nics=nics[:2])}
            status, msg = self.setupNetworks(networks, bondings, NOCHK)

            self.assertEqual(status, SUCCESS, msg)

            self.assertMtu(MIDI, NETWORK_NAME + '2', BONDING_NAME, nics[0],
                           nics[1])

            # Add a 3rd network, with JUMBO MTU.
            # Expect to see nics MTU to be JUMBO. (JUMBO > MIDI)
            network = {NETWORK_NAME + '3':
                       dict(bonding=BONDING_NAME, vlan='300', mtu=JUMBO,
                            bridged=bridged)}
            status, msg = self.setupNetworks(network, {}, NOCHK)

            self.assertEqual(status, SUCCESS, msg)

            self.assertNetworkExists(NETWORK_NAME + '3', bridged=bridged)
            self.assertMtu(JUMBO, NETWORK_NAME + '3', BONDING_NAME, nics[0],
                           nics[1])

            # Remove the 3rd network (with JUMBO MTU)
            # Expect to see nics MTU to be MIDI.
            status, msg = self.setupNetworks(
                {NETWORK_NAME + '3': dict(remove=True)}, {}, NOCHK)

            self.assertEqual(status, SUCCESS, msg)

            self.assertMtu(MIDI, NETWORK_NAME + '2', BONDING_NAME, nics[0],
                           nics[1])

            # Remove the 2nd network (with MIDI MTU)
            # Expect to see nics MTU to be DEFAULT.
            status, msg = self.setupNetworks(
                {NETWORK_NAME + '2': dict(remove=True)}, {}, NOCHK)

            self.assertEqual(status, SUCCESS, msg)

            self.assertMtu(DEFAULT_MTU, BONDING_NAME, nics[0], nics[1])

            # Add additional nic to the bond
            # Expect to see nics MTU to be DEFAULT.
            status, msg = self.setupNetworks(
                {}, {BONDING_NAME: dict(nics=nics)}, NOCHK)

            self.assertEqual(status, SUCCESS, msg)

            self.assertMtu(DEFAULT_MTU, BONDING_NAME,
                           nics[0], nics[1], nics[2])

            status, msg = self.setupNetworks(
                {NETWORK_NAME + '1': dict(remove=True)},
                {BONDING_NAME: dict(remove=True)}, NOCHK)

            self.assertEqual(status, SUCCESS, msg)

            # Add additional nic to a bond on a non default mtu network
            # Expect to see nics MTU to be non default.
            network = {NETWORK_NAME:
                       dict(bonding=BONDING_NAME, vlan='10', mtu=JUMBO,
                            bridged=bridged)}
            bondings = {BONDING_NAME: dict(nics=nics[:2])}
            status, msg = self.setupNetworks(network, bondings, NOCHK)
            self.assertEqual(status, SUCCESS, msg)

            status, msg = self.setupNetworks(
                {}, {BONDING_NAME: dict(nics=nics)}, NOCHK)
            self.assertEqual(status, SUCCESS, msg)

            self.assertMtu(JUMBO, BONDING_NAME, nics[0], nics[1], nics[2])

            status, msg = self.setupNetworks(
                {NETWORK_NAME: dict(remove=True)},
                {BONDING_NAME: dict(remove=True)}, NOCHK)
            self.assertEqual(status, SUCCESS, msg)

    @cleanupNet
    @permutations([[True], [False]])
    def testSetupNetworksAddNetworkToNicAfterBondBreaking(self, bridged):
        with dummyIf(2) as nics:
            networks = {NETWORK_NAME: dict(bonding=BONDING_NAME,
                                           bridged=bridged)}
            status, msg = self.setupNetworks(
                networks, {BONDING_NAME: dict(nics=nics)}, NOCHK)
            self.assertEqual(status, SUCCESS, msg)

            self.assertNetworkExists(NETWORK_NAME, bridged=bridged)
            self.assertBondExists(BONDING_NAME, nics)

            # Break the bond and create Network on detached NIC
            networks = {NETWORK_NAME: dict(nic=nics[0], bridged=bridged)}
            status, msg = self.setupNetworks(
                networks, {BONDING_NAME: dict(remove=True)}, NOCHK)
            self.assertEqual(status, SUCCESS, msg)

            self.assertNetworkExists(NETWORK_NAME, bridged=bridged)
            self.assertBondDoesntExist(BONDING_NAME, nics)

            status, msg = self.setupNetworks(
                {NETWORK_NAME: dict(remove=True)}, {}, NOCHK)
            self.assertEqual(status, SUCCESS, msg)

    @cleanupNet
    @permutations([[True], [False]])
    def testSetupNetworksKeepNetworkOnBondAfterBondResizing(self, bridged):
        with dummyIf(3) as nics:
            networks = {NETWORK_NAME: dict(bonding=BONDING_NAME,
                                           bridged=bridged)}
            bondings = {BONDING_NAME: dict(nics=nics[:2])}
            status, msg = self.setupNetworks(networks, bondings, NOCHK)
            self.assertEqual(status, SUCCESS, msg)

            self.assertNetworkExists(NETWORK_NAME, bridged=bridged)
            self.assertBondExists(BONDING_NAME, nics[:2])

            # Increase bond size
            _waitForOperstate(BONDING_NAME, OPERSTATE_UP)
            with nonChangingOperstate(BONDING_NAME):
                status, msg = self.setupNetworks(
                    {}, {BONDING_NAME: dict(nics=nics)}, NOCHK)

                self.assertEqual(status, SUCCESS, msg)

                self.assertNetworkExists(NETWORK_NAME, bridged=bridged)
                self.assertBondExists(BONDING_NAME, nics)

            status, msg = self.setupNetworks(
                {NETWORK_NAME: dict(remove=True)},
                {BONDING_NAME: dict(remove=True)}, NOCHK)
            self.assertEqual(status, SUCCESS, msg)

    def _createBondedNetAndCheck(self, netNum, bondDict, bridged,
                                 **networkOpts):
        netName = NETWORK_NAME + str(netNum)
        networks = {netName: dict(bonding=BONDING_NAME, bridged=bridged,
                                  vlan=str(int(VLAN_ID) + netNum),
                                  **networkOpts)}
        status, msg = self.setupNetworks(
            networks, {BONDING_NAME: bondDict}, {})
        self.assertEqual(status, SUCCESS, msg)
        self.assertNetworkExists(netName, bridged=bridged)
        self.assertBondExists(BONDING_NAME, bondDict['nics'],
                              bondDict.get('options'))
        if 'mtu' in networkOpts:
            self.assertMtu(networkOpts['mtu'], netName)

    @cleanupNet
    @permutations([[True], [False]])
    def testSetupNetworksStableBond(self, bridged):
        with dummyIf(3) as nics:
            with self.vdsm_net.pinger():
                # Add initial vlanned net over bond
                self._createBondedNetAndCheck(0, {'nics': nics[:2],
                                              'options': 'mode=3 miimon=250'},
                                              bridged)

                _waitForOperstate(BONDING_NAME, OPERSTATE_UP)
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
                    _waitForOperstate(BONDING_NAME, OPERSTATE_UP)
                    with nonChangingOperstate(BONDING_NAME):
                        self._createBondedNetAndCheck(4,
                                                      {'nics': nics[1:],
                                                       'options':
                                                       'mode=4 miimon=9'},
                                                      bridged)

                # cleanup
                networks = dict((NETWORK_NAME + str(num), {'remove': True}) for
                                num in range(5))
                status, msg = self.setupNetworks(
                    networks, {BONDING_NAME: dict(remove=True)}, {})
                self.assertEqual(status, SUCCESS, msg)

    @cleanupNet
    @permutations([[True], [False]])
    def testSetupNetworksMultiMTUsOverBond(self, bridged):
        with dummyIf(2) as nics:
            with self.vdsm_net.pinger():
                # Add initial vlanned net over bond
                self._createBondedNetAndCheck(0, {'nics': nics}, bridged,
                                              mtu=1500)
                self.assertMtu(1500, BONDING_NAME)

                _waitForOperstate(BONDING_NAME, OPERSTATE_UP)
                with nonChangingOperstate(BONDING_NAME):
                    # Add a network with MTU smaller than existing network
                    self._createBondedNetAndCheck(1, {'nics': nics},
                                                  bridged, mtu=1400)
                    self.assertMtu(1500, BONDING_NAME)

                    # Add a network with MTU bigger than existing network
                    self._createBondedNetAndCheck(2, {'nics': nics},
                                                  bridged, mtu=1600)
                    self.assertMtu(1600, BONDING_NAME)

                # cleanup
                networks = dict((NETWORK_NAME + str(num), {'remove': True}) for
                                num in range(3))
                status, msg = self.setupNetworks(
                    networks, {BONDING_NAME: dict(remove=True)}, {})
                self.assertEqual(status, SUCCESS, msg)

    def _createVlanedNetOverNicAndCheck(self, netNum, bridged, **networkOpts):
        netName = NETWORK_NAME + str(netNum)
        networks = {netName: dict(bridged=bridged,
                                  vlan=str(int(VLAN_ID) + netNum),
                                  **networkOpts)}
        status, msg = self.setupNetworks(networks, {}, {})
        self.assertEqual(status, SUCCESS, msg)
        self.assertNetworkExists(netName, bridged=bridged)
        if 'mtu' in networkOpts:
            self.assertMtu(networkOpts['mtu'], netName)

    @cleanupNet
    @permutations([[True], [False]])
    def testSetupNetworksMultiMTUsOverNic(self, bridged):
        with dummyIf(1) as nics:
            nic, = nics
            with self.vdsm_net.pinger():
                # Add initial vlanned net over bond
                self._createVlanedNetOverNicAndCheck(0, bridged, nic=nic,
                                                     mtu=1500)
                self.assertMtu(1500, nic)

                # Add a network with MTU smaller than existing network
                self._createVlanedNetOverNicAndCheck(1, bridged, nic=nic,
                                                     mtu=1400)
                self.assertMtu(1500, nic)

                # Add a network with MTU bigger than existing network
                self._createVlanedNetOverNicAndCheck(2, bridged, nic=nic,
                                                     mtu=1600)
                self.assertMtu(1600, nic)

                # cleanup
                networks = dict((NETWORK_NAME + str(num), {'remove': True}) for
                                num in range(3))
                status, msg = self.setupNetworks(networks, {}, {})
                self.assertEqual(status, SUCCESS, msg)

    @permutations([[True], [False]])
    def testSetupNetworksAddBadParams(self, bridged):
        attrs = dict(vlan=VLAN_ID, bridged=bridged)
        status, msg = self.setupNetworks({NETWORK_NAME: attrs}, {}, {})

        self.assertNotEqual(status, SUCCESS, msg)

    @cleanupNet
    def testDelNetworkBondAccumulation(self):
        with dummyIf(2) as nics:
            for bigBond in ('bond555', 'bond666', 'bond777'):
                status, msg = self.setupNetworks(
                    {NETWORK_NAME: {'vlan': VLAN_ID, 'bonding': bigBond}},
                    {bigBond: {'nics': nics}}, NOCHK)
                self.assertEqual(status, SUCCESS, msg)

                self.assertBondExists(bigBond, nics)

                status, msg = self.setupNetworks(
                    {NETWORK_NAME: {'remove': True}},
                    {bigBond: {'remove': True}}, NOCHK)

                self.assertEqual(status, SUCCESS, msg)

                self.assertBondDoesntExist(bigBond, nics)

    @cleanupNet
    @permutations([[True], [False]])
    def testBondHwAddress(self, bridged=True):
        """
        Test that bond mac address is independent of the ordering of nics arg
        """
        with dummyIf(2) as nics:
            def _getBondHwAddress(*nics):
                nets = {NETWORK_NAME: {'bridged': bridged,
                                       'bonding': BONDING_NAME}}
                bonds = {BONDING_NAME: {'nics': nics}}
                status, msg = self.setupNetworks(nets, bonds, NOCHK)
                self.assertEqual(status, SUCCESS, msg)
                hwaddr = self.vdsm_net.netinfo.bondings[BONDING_NAME]['hwaddr']

                status, msg = self.setupNetworks(
                    {NETWORK_NAME: {'remove': True}},
                    {BONDING_NAME: {'remove': True}}, NOCHK)
                self.assertEqual(status, SUCCESS, msg)

                return hwaddr

            macAddress1 = _getBondHwAddress(nics[0], nics[1])
            macAddress2 = _getBondHwAddress(nics[1], nics[0])
            self.assertEqual(macAddress1, macAddress2)

    @cleanupNet
    @permutations([[True], [False]])
    def testSafeNetworkConfig(self, bridged):
        """
        Checks that setSafeNetworkConfig saves
        the configuration between restart.
        """
        with dummyIf(1) as nics:
            nic, = nics
            status, msg = self.setupNetworks(
                {NETWORK_NAME: {'nic': nic, 'bridged': bridged}}, {}, NOCHK)
            self.assertEqual(status, SUCCESS, msg)

            self.assertNetworkExists(NETWORK_NAME, bridged=bridged)

            self.vdsm_net.save_config()

            self.vdsm_net.restoreNetConfig()

            self.assertNetworkExists(NETWORK_NAME, bridged=bridged)

            status, msg = self.setupNetworks(
                {NETWORK_NAME: {'remove': True}}, {}, NOCHK)
            self.assertEqual(status, SUCCESS, msg)

            self.vdsm_net.save_config()

    @requiresUnifiedPersistence("with ifcfg persistence, this test is "
                                "irrelevant")
    @cleanupNet
    @RequireVethMod
    @ValidateRunningAsRoot
    def testRestoreToBlockingDHCP(self):
        """test that regardless of what is written in the unified persistence,
        restoration of bootprot=dhcp networks is down synchronously. with
        ifcfg persistence, this is what happens thanks to initscripts,
        regardless of vdsm. Hence, this test is irrelevant there. """

        def _get_blocking_dhcp(net_name):
            self.vdsm_net.refreshNetinfo()
            return self.vdsm_net.config.networks[net_name].get('blockingdhcp')

        with veth_pair() as (server, client):
            addrAdd(server, IP_ADDRESS, IP_CIDR)
            linkSet(server, ['up'])
            dhcp_async_net = {'nic': client, 'bridged': False,
                              'bootproto': 'dhcp', 'blockingdhcp': False}
            status, msg = self.setupNetworks(
                {NETWORK_NAME: dhcp_async_net}, {}, NOCHK)
            self.assertEqual(status, SUCCESS, msg)

            self.assertNetworkExists(NETWORK_NAME)
            self.assertFalse(_get_blocking_dhcp(NETWORK_NAME))

            self.vdsm_net.save_config()

            # Terminate the dhclient spawned by the setup to avoid a race
            # with the source route thread.
            dhclient.kill(client)
            # TODO: Fix sourceroute thread and make sure it fails supervdsm
            # if it is crashes.

            with dnsmasq_run(server, DHCP_RANGE_FROM, DHCP_RANGE_TO,
                             DHCPv6_RANGE_FROM, DHCPv6_RANGE_TO, IP_GATEWAY):
                self.vdsm_net.restoreNetConfig()
                self.assertTrue(_get_blocking_dhcp(NETWORK_NAME))

            # cleanup
            status, msg = self.setupNetworks(
                {NETWORK_NAME: {'remove': True}}, {}, NOCHK)
            self.assertEqual(status, SUCCESS, msg)

    @cleanupNet
    def testRemovingBridgeDoesNotLeaveBridge(self):
        with dummyIf(1) as nics:
            nic, = nics
            status, msg = self.setupNetworks(
                {NETWORK_NAME: {'nic': nic, 'STP': 'no', 'bridged': 'true',
                                'mtu': 1500}}, {}, NOCHK)
            self.assertEqual(status, SUCCESS, msg)
            bridge = ipwrapper.getLink(NETWORK_NAME)
            status, msg = self.setupNetworks(
                {NETWORK_NAME: {'nic': nic, 'bridged': 'false', 'mtu': 1500}},
                {}, NOCHK)
            self.assertEqual(status, SUCCESS, msg)
            self.assertNotIn(
                bridge.name, (l.name for l in ipwrapper.getLinks()))
            status, msg = self.setupNetworks(
                {NETWORK_NAME: {'remove': True}}, {}, NOCHK)
            self.assertEqual(status, SUCCESS, msg)

    @requiresUnifiedPersistence("with ifcfg persistence, "
                                "vdsm-restore-net-config selective restoration"
                                "is not supported")
    @cleanupNet
    def testRestoreNetworksOnlyRestoreUnchangedDevices(self):
        BOND_UNCHANGED = 'bond100'
        BOND_MISSING = 'bond102'
        IP_ADD_UNCHANGED = '240.0.0.100'
        VLAN_UNCHANGED = 100
        NET_UNCHANGED = NETWORK_NAME + '100'
        NET_CHANGED = NETWORK_NAME + '108'
        NET_MISSING = NETWORK_NAME + '116'
        IP_ADDR_NET_CHANGED = '240.0.0.108'
        IP_ADDR_MISSING = '204.0.0.116'
        IP_NETMASK = '255.255.255.248'
        IP_GATEWAY = '240.0.0.102'
        nets = {
            NET_UNCHANGED: {
                'bootproto': 'none', 'ipaddr': IP_ADD_UNCHANGED,
                'vlan': str(VLAN_UNCHANGED), 'netmask': IP_NETMASK,
                'gateway': IP_GATEWAY,
                'bonding': BOND_UNCHANGED, 'defaultRoute': True},
            NET_CHANGED: {
                'bootproto': 'none',
                'ipaddr': IP_ADDR_NET_CHANGED,
                'vlan': str(VLAN_UNCHANGED + 1),
                'netmask': IP_NETMASK, 'bonding': BOND_UNCHANGED,
                'defaultRoute': False},
            NET_MISSING: {
                'bootproto': 'none',
                'ipaddr': IP_ADDR_MISSING,
                'vlan': str(VLAN_UNCHANGED + 2),
                'netmask': IP_NETMASK, 'bonding': BOND_MISSING},
        }

        def _assert_all_nets_exist():
            self.vdsm_net.refreshNetinfo()
            self.assertNetworkExists(NET_UNCHANGED)
            self.assertNetworkExists(NET_CHANGED)
            self.assertNetworkExists(NET_MISSING)
            self.assertBondExists(BOND_UNCHANGED, [nic_a])
            self.assertBondExists(BOND_MISSING, [nic_b])

        with dummyIf(2) as nics:
            nic_a, nic_b = nics
            bonds = {BOND_UNCHANGED: {'nics': [nic_a]},
                     BOND_MISSING: {'nics': [nic_b]}
                     }
            status, msg = self.setupNetworks(nets, bonds, NOCHK)
            self.assertEqual(status, SUCCESS, msg)
            _assert_all_nets_exist()
            try:
                self.vdsm_net.save_config()

                addrFlush(NET_CHANGED)
                linkSet(NET_MISSING, ['down'])
                execCmd([EXT_BRCTL, 'delbr', NET_MISSING])
                linkDel(BOND_MISSING)
                self.vdsm_net.refreshNetinfo()
                self.assertEqual(
                    self.vdsm_net.netinfo.networks[NET_CHANGED]['addr'], '')
                self.assertNotIn(NET_MISSING, self.vdsm_net.netinfo.networks)
                self.assertNotIn(BOND_MISSING, self.vdsm_net.netinfo.bondings)

                with nonChangingOperstate(NET_UNCHANGED):
                    self.vdsm_net.restoreNetConfig()

                _assert_all_nets_exist()
                # no ifcfg backups should be left now that all ifcfgs are owned
                # by vdsm
                self.assertEqual([], os.listdir(NET_CONF_BACK_DIR))
                # another 'boot' should restore nothing
                with nonChangingOperstate(NET_UNCHANGED):
                    with nonChangingOperstate(NET_CHANGED):
                        with nonChangingOperstate(NET_MISSING):
                            self.vdsm_net.restoreNetConfig()
            finally:
                self.setupNetworks(
                    {NET_UNCHANGED: {'remove': True},
                     NET_CHANGED: {'remove': True},
                     NET_MISSING: {'remove': True}},
                    {BOND_MISSING: {'remove': True},
                     BOND_UNCHANGED: {'remove': True}},
                    NOCHK)
                self.vdsm_net.save_config()
                self.assertNetworkDoesntExist(NET_UNCHANGED)
                self.assertNetworkDoesntExist(NET_CHANGED)
                self.assertNetworkDoesntExist(NET_MISSING)
                self.assertBondDoesntExist(BOND_MISSING, [nic_b])
                self.assertBondDoesntExist(BOND_UNCHANGED, [nic_a])

    @requiresUnifiedPersistence("with ifcfg persistence, "
                                "vdsm-restore-net-config selective restoration"
                                "is not supported")
    @cleanupNet
    def testSelectiveRestoreDuringUpgrade(self):
        BOND_UNCHANGED = 'bond100'
        BOND_CHANGED = 'bond101'
        IP_MGMT = '240.0.0.100'
        NET_MGMT = NETWORK_NAME + '100'
        NET_UNCHANGED = NETWORK_NAME + '108'
        NET_CHANGED = NETWORK_NAME + '116'
        NET_ADDITIONAL = NETWORK_NAME + '124'
        IP_ADDR_UNCHANGED = '240.0.0.108'
        IP_ADDR_CHANGED = '204.0.0.116'
        IP_ADDR_ADDITIONAL = '204.0.0.124'
        IP_NETMASK = '255.255.255.248'
        IP_GATEWAY = '240.0.0.102'
        nets = {
            NET_MGMT: {
                'bootproto': 'none', 'ipaddr': IP_MGMT, 'gateway': IP_GATEWAY,
                'netmask': IP_NETMASK, 'defaultRoute': True},
            NET_UNCHANGED: {
                'bootproto': 'none',
                'ipaddr': IP_ADDR_UNCHANGED,
                'netmask': IP_NETMASK, 'bonding': BOND_UNCHANGED,
                'defaultRoute': False},
            NET_CHANGED: {
                'bootproto': 'none',
                'ipaddr': IP_ADDR_CHANGED,
                'netmask': IP_NETMASK, 'bonding': BOND_CHANGED},
        }
        net_additional_attrs = {
            'bootproto': 'none', 'ipaddr': IP_ADDR_ADDITIONAL,
            'netmask': IP_NETMASK}

        def _assert_all_nets_exist():
            self.vdsm_net.refreshNetinfo()
            self.assertNetworkExists(NET_MGMT)
            self.assertNetworkExists(NET_UNCHANGED)
            self.assertNetworkExists(NET_CHANGED)
            self.assertBondExists(BOND_UNCHANGED, [nic_b])
            self.assertBondExists(BOND_CHANGED, [nic_c], options='mode=4')

        def _simulate_boot(change_bond=False, after_upgrade=False):
            device_names = (NET_UNCHANGED, BOND_UNCHANGED, nic_b, NET_CHANGED,
                            BOND_CHANGED, nic_c)
            if after_upgrade:
                stop_devices((NET_CONF_PREF + name for name in device_names))
            for dev in device_names:
                with open(NET_CONF_PREF + dev) as f:
                    content = f.read()
                if after_upgrade:
                    # all non-management devices are down and have ONBOOT=no
                    # from older vdsm versions.
                    content = re.sub('ONBOOT=yes', 'ONBOOT=no', content)
                if change_bond and dev == BOND_CHANGED:
                    # also test the case that a bond is different from it's
                    # backup
                    content = re.sub('mode=4', 'mode=0', content)
                with open(NET_CONF_PREF + dev, 'w') as f:
                    f.write(content)

        def _verify_running_config_intact():
            self.assertEqual({NET_MGMT, NET_CHANGED, NET_UNCHANGED,
                              NET_ADDITIONAL},
                             set(self.vdsm_net.config.networks.keys()))
            self.assertEqual({BOND_CHANGED, BOND_UNCHANGED},
                             set(self.vdsm_net.config.bonds.keys()))

        with dummyIf(4) as nics:
            nic_a, nic_b, nic_c, nic_d = nics
            nets[NET_MGMT]['nic'] = nic_a
            net_additional_attrs['nic'] = nic_d
            bonds = {BOND_UNCHANGED: {'nics': [nic_b]},
                     BOND_CHANGED: {'nics': [nic_c], 'options': "mode=4"}
                     }
            status, msg = self.setupNetworks(nets, bonds, NOCHK)
            self.assertEqual(status, SUCCESS, msg)
            _assert_all_nets_exist()
            try:
                self.vdsm_net.save_config()

                _simulate_boot(change_bond=True, after_upgrade=True)

                with nonChangingOperstate(NET_MGMT):
                    self.vdsm_net.restoreNetConfig()
                # no ifcfg backups should be left now that all ifcfgs are owned
                # by vdsm
                self.assertEqual([], os.listdir(NET_CONF_BACK_DIR))

                status, msg = self.setupNetworks(
                    {NET_ADDITIONAL: net_additional_attrs}, {}, NOCHK)
                self.assertEqual(status, SUCCESS, msg)
                _assert_all_nets_exist()
                _verify_running_config_intact()
                self.assertEqual({'ifcfg-%s' % NET_ADDITIONAL,
                                  'ifcfg-%s' % nic_d},
                                 set(os.listdir(NET_CONF_BACK_DIR)))

                # another 'boot' should restore nothing,
                # except remove NET_ADDITIONAL
                _simulate_boot()
                with nonChangingOperstate(NET_MGMT):
                    with nonChangingOperstate(NET_UNCHANGED):
                        with nonChangingOperstate(NET_CHANGED):
                            self.vdsm_net.restoreNetConfig()

                _assert_all_nets_exist()
                self.assertEqual([], os.listdir(NET_CONF_BACK_DIR))

            finally:
                status, msg = self.setupNetworks(
                    {NET_MGMT: {'remove': True},
                     NET_UNCHANGED: {'remove': True},
                     NET_CHANGED: {'remove': True}},
                    {BOND_CHANGED: {'remove': True},
                     BOND_UNCHANGED: {'remove': True}},
                    NOCHK)
                self.assertEqual(status, SUCCESS, msg)
                self.vdsm_net.save_config()
                self.assertNetworkDoesntExist(NET_MGMT)
                self.assertNetworkDoesntExist(NET_UNCHANGED)
                self.assertNetworkDoesntExist(NET_CHANGED)
                self.assertNetworkDoesntExist(NET_ADDITIONAL)
                self.assertBondDoesntExist(BOND_UNCHANGED, [nic_b])
                self.assertBondDoesntExist(BOND_CHANGED, [nic_c])

    @requiresUnifiedPersistence("with ifcfg persistence, "
                                "vdsm-restore-net-config selective restoration"
                                "is not supported")
    @cleanupNet
    def testSelectiveRestoreIgnoresVdsmRegParams(self):
        with dummyIf(1) as nics:
            nic, = nics
            # let _assert_kernel_config_matches_running_config do the job
            status, msg = self.setupNetworks(
                {NETWORK_NAME: {'nic': nic, 'IPV6_AUTOCONF': 'no',
                                'PEERNTP': 'yes', 'IPV6INIT': 'no'}},
                {}, NOCHK)
            self.assertEqual(status, SUCCESS, msg)
            self.assertNetworkExists(NETWORK_NAME)
            status, msg = self.setupNetworks(
                {NETWORK_NAME: {'remove': True}}, {}, NOCHK)
            self.assertEqual(status, SUCCESS, msg)
            self.assertNetworkDoesntExist(NETWORK_NAME)

    @cleanupNet
    @permutations([[True], [False]])
    def testVolatileConfig(self, bridged):
        """
        Checks that the network doesn't persist over restart
        """
        with dummyIf(1) as nics:
            nic, = nics
            status, msg = self.setupNetworks(
                {NETWORK_NAME: {'nic': nic, 'bridged': bridged}}, {}, NOCHK)
            self.assertEqual(status, SUCCESS, msg)

            self.assertNetworkExists(NETWORK_NAME, bridged=bridged)

            self.vdsm_net.restoreNetConfig()

            self.assertNetworkDoesntExist(NETWORK_NAME)

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
    def test_ipv4_default_route(self):
        DEFAULT_GATEWAY_NET = 'DG-NET'
        GATEWAY_NET = 'GW-NET'
        NO_GATEWAY_NET = 'NOGW-NET'
        IP = [{'ipaddr': '192.168.{}.1'.format(i),
               'netmask': '255.255.255.0',
               'gateway': '192.168.{}.254'.format(i)}
              for i in range(3)]
        del IP[2]['gateway']

        def create_network(net_name, ipdata, nic, is_default_route=False):
            net_attrs = {'nic': nic, 'defaultRoute': is_default_route}
            net_attrs.update(ipdata)
            status, msg = self.setupNetworks({net_name: net_attrs}, {}, NOCHK)
            self.assertEqual(status, SUCCESS, msg)

        with dummyIf(3) as nics:
            create_network(DEFAULT_GATEWAY_NET, IP[0], nics[0],
                           is_default_route=True)
            gw = self.vdsm_net.netinfo.networks[DEFAULT_GATEWAY_NET]['gateway']
            self.assertEqual(IP[0]['gateway'], gw)
            self.assertEqual(IP[0]['gateway'], getDefaultGateway().via)

            create_network(GATEWAY_NET, IP[1], nics[1])
            gw = self.vdsm_net.netinfo.networks[GATEWAY_NET]['gateway']
            self.assertEqual(IP[1]['gateway'], gw)
            self.assertEqual(IP[0]['gateway'], getDefaultGateway().via)

            create_network(NO_GATEWAY_NET, IP[2], nics[2])
            gw = self.vdsm_net.netinfo.networks[NO_GATEWAY_NET]['gateway']
            self.assertEqual('', gw)
            self.assertEqual(IP[0]['gateway'], getDefaultGateway().via)

            status, msg = self.setupNetworks(
                {NO_GATEWAY_NET: {'remove': True}}, {}, NOCHK)
            self.assertEqual(status, SUCCESS, msg)
            self.assertEqual(IP[0]['gateway'], getDefaultGateway().via)

            status, msg = self.setupNetworks(
                {GATEWAY_NET: {'remove': True}}, {}, NOCHK)
            self.assertEqual(status, SUCCESS, msg)
            self.assertEqual(IP[0]['gateway'], getDefaultGateway().via)

            status, msg = self.setupNetworks(
                {DEFAULT_GATEWAY_NET: {'remove': True}}, {}, NOCHK)
            self.assertEqual(status, SUCCESS, msg)

            dg_obj = getDefaultGateway()
            if dg_obj:
                self.assertNotEqual(IP[0]['gateway'], dg_obj.via)

    @cleanupNet
    def testAddVlanedBridgeless(self):
        # BZ# 980174
        vlan_name = NETWORK_NAME + '-v'
        with dummyIf(1) as nics:
            nic, = nics
            # net NETWORK_NAME has bootproto:none because we can't use dhcp
            # on dummyIf
            bridgless = {'nic': nic, 'bridged': False, 'bootproto': 'none'}
            bridged = {'nic': nic, 'bridged': True, 'vlan': VLAN_ID,
                       'bootproto': 'none'}

            with self.vdsm_net.pinger():
                status, msg = self.setupNetworks(
                    {NETWORK_NAME: bridgless}, {}, {})
                self.assertEqual(status, SUCCESS, msg)
                self.assertNetworkExists(NETWORK_NAME)
                status, msg, info = self.vdsm_net.getVdsCapabilities()
                self.assertFalse(info['nics'][nic]['dhcpv4'])

                status, msg = self.setupNetworks(
                    {vlan_name: bridged}, {}, {})
                self.assertEqual(status, SUCCESS, msg)
                self.assertNetworkExists(vlan_name)
                status, msg, info = self.vdsm_net.getVdsCapabilities()
                self.assertFalse(info['nics'][nic]['dhcpv4'])

                # network should be fine even after second addition of vlan
                status, msg = self.setupNetworks(
                    {vlan_name: bridged}, {}, {})
                self.assertEqual(status, SUCCESS, msg)
                status, msg, info = self.vdsm_net.getVdsCapabilities()
                self.assertFalse(info['nics'][nic]['dhcpv4'])

                delete_networks = {NETWORK_NAME: {'remove': True},
                                   vlan_name: {'remove': True}}
                status, msg = self.setupNetworks(delete_networks, {}, {})
                self.assertEqual(status, SUCCESS, msg)

    @cleanupNet
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
                status, msg = self.setupNetworks(networks, {}, {})
                self.assertEqual(status, SUCCESS, msg)
                self.assertNetworkExists(NETWORK_NAME)
                self.assertNetworkExists(vlan_name)
                status, msg, info = self.vdsm_net.getVdsCapabilities()
                self.assertFalse(info['nics'][nic]['dhcpv4'])

                delete_networks = {NETWORK_NAME: {'remove': True},
                                   vlan_name: {'remove': True}}
                status, msg = self.setupNetworks(delete_networks, {}, {})
                self.assertEqual(status, SUCCESS, msg)

    @cleanupNet
    @ValidatesHook('before_network_setup', 'testBeforeNetworkSetup.py', True,
                   "#!/usr/bin/python2\n"
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

    def test_static_ip_configuration_v4_to_dual(self):
        self._test_static_ip_configuration(([4], [4, 6]))

    def test_static_ip_configuration_dual_to_v4(self):
        self._test_static_ip_configuration(([4, 6], [4]))

    def test_static_ip_configuration_dual_to_v6_and_back(self):
        self._test_static_ip_configuration(([4, 6], [6], [4, 6]))

    @cleanupNet
    def _test_static_ip_configuration(self, use_case):
        """
        Show that configuring IPv4 and/or IPv6 works. This is shown by going
        e.g. from v4 to v6 (or dual-stack), depending on the given use-case.
        At each step it is checked that IPv6 is disabled (by sysctl) when not
        requested, and that IPv6 link-local address doesn't exist in that case.
        """
        with dummyIf(1) as nics:
            nic, = nics
            IPv4 = dict(nic=nic, bootproto='none', ipaddr=IP_ADDRESS,
                        netmask=IP_MASK, gateway=IP_GATEWAY)
            IPv6 = dict(nic=nic, bootproto='none', ipv6gateway=IPv6_GATEWAY,
                        ipv6addr=IPv6_ADDRESS_AND_CIDR)

            def change_ip_configuration_and_verify(families):
                netdict = dict(IPv4) if 4 in families else {}
                if 6 in families:
                    netdict.update(IPv6)
                status, msg = self.setupNetworks(
                    {NETWORK_NAME: netdict}, {}, {})
                self.assertEqual(status, SUCCESS, msg)
                self.assertNetworkExists(NETWORK_NAME)
                test_net = self.vdsm_net.netinfo.networks[NETWORK_NAME]
                if 4 in families:
                    self.assertEqual(IP_ADDRESS, test_net['addr'])
                    self.assertEqual(IP_MASK, test_net['netmask'])
                    self.assertIn(IP_ADDRESS_AND_CIDR, test_net['ipv4addrs'])
                    self.assertEqual(IP_GATEWAY, test_net['gateway'])
                if 6 in families:
                    self.assertIn(IPv6_ADDRESS_AND_CIDR, test_net['ipv6addrs'])
                    self.assertEqual(IPv6_GATEWAY, test_net['ipv6gateway'])
                else:
                    self.assertEqual([], test_net['ipv6addrs'])
                    self.assertTrue(sysctl.is_disabled_ipv6(nic))

            with self.vdsm_net.pinger():
                for ip_families in use_case:
                    change_ip_configuration_and_verify(ip_families)

                delete = {NETWORK_NAME: {'remove': True}}
                status, msg = self.setupNetworks(delete, {}, {})
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

    @permutations([(True, (4,)), (True, (6,)), (True, (4, 6)),
                   (False, (4,)), (False, (6,)), (False, (4, 6))])
    @cleanupNet
    @RequireVethMod
    def testSetupNetworksAddDelDhcp(self, bridged, families):
        def _assert_applied(network_name, requested, reported):
            self.assertNetworkExists(network_name)
            reported_network = reported.networks[network_name]

            if requested['bridged']:
                reported_devices = reported.bridges
                device_name = network_name
            else:
                reported_devices = reported.nics
                device_name = requested['nic']
            self.assertIn(device_name, reported_devices)
            reported_device = reported_devices[device_name]

            requested_dhcpv4 = requested['bootproto'] == 'dhcp'
            self.assertEqual(reported_network['dhcpv4'], requested_dhcpv4)
            self.assertEqual(reported_network['dhcpv6'], requested['dhcpv6'])

            self.assertEqual(reported_device['dhcpv4'], requested_dhcpv4)
            self.assertEqual(reported_device['dhcpv6'], requested['dhcpv6'])

            if requested_dhcpv4:
                self.assertEqual(reported_network['gateway'], IP_GATEWAY)
                # TODO: source routing not ready for IPv6
                ip_addr = reported_network['addr']
                self.assertSourceRoutingConfiguration(device_name,
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

    @permutations([[False], [True]])
    @cleanupNet
    def testBrokenNetworkReplacement(self, bridged):
        with dummyIf(1) as nics:
            nic, = nics
            network = {NETWORK_NAME: {'nic': nic, 'vlan': VLAN_ID,
                                      'bridged': bridged}}
            status, msg = self.setupNetworks(network, {}, NOCHK)
            self.assertEqual(status, SUCCESS, msg)
            self.assertNetworkExists(NETWORK_NAME)
            if bridged:
                ipwrapper.linkDel(NETWORK_NAME)
            else:
                ipwrapper.linkDel(nic + '.' + VLAN_ID)

            self.vdsm_net.refreshNetinfo()
            self.assertNotIn(NETWORK_NAME, self.vdsm_net.netinfo.networks)
            status, msg = self.setupNetworks(network, {}, NOCHK)
            self.assertEqual(status, SUCCESS, msg)
            self.assertNetworkExists(NETWORK_NAME)
            network[NETWORK_NAME] = {'remove': True}
            status, msg = self.setupNetworks(network, {}, NOCHK)
            self.assertEqual(status, SUCCESS, msg)
            self.assertNetworkDoesntExist(NETWORK_NAME)

    @cleanupNet
    def testReconfigureBrNetWithVanishedPort(self):
        """Test for re-defining a bridged network for which the device
        providing connectivity to the bridge had been removed from it"""
        with dummyIf(1) as nics:
            nic, = nics
            network = {NETWORK_NAME: {'nic': nic, 'bridged': True}}
            status, msg = self.setupNetworks(network, {}, NOCHK)
            self.assertEqual(status, SUCCESS, msg)
            self.assertNetworkExists(NETWORK_NAME)

            # Remove the nic from the bridge
            execCmd([EXT_BRCTL, 'delif', NETWORK_NAME, nic])
            self.vdsm_net.refreshNetinfo()
            self.assertEqual(len(
                self.vdsm_net.netinfo.networks[NETWORK_NAME]['ports']), 0)

            # Attempt to reconfigure the network
            status, msg = self.setupNetworks(network, {}, NOCHK)
            self.assertEqual(status, SUCCESS, msg)
            self.assertEqual(
                self.vdsm_net.netinfo.networks[NETWORK_NAME]['ports'], [nic])

            # cleanup
            network[NETWORK_NAME] = {'remove': True}
            status, msg = self.setupNetworks(network, {}, NOCHK)
            self.assertEqual(status, SUCCESS, msg)
            self.assertNetworkDoesntExist(NETWORK_NAME)

    def testNoBridgeLeftovers(self):
        """Test for https://bugzilla.redhat.com/1071398"""
        with dummyIf(2) as nics:
            network = {NETWORK_NAME: {'bonding': BONDING_NAME}}
            bonds = {BONDING_NAME: {'nics': nics}}
            status, msg = self.setupNetworks(network, bonds, NOCHK)
            self.assertEqual(status, SUCCESS, msg)
            self.assertNetworkExists(NETWORK_NAME)

            # Remove the network but not the bond
            network[NETWORK_NAME] = {'remove': True}
            status, msg = self.setupNetworks(network, {}, NOCHK)
            self.assertEqual(status, SUCCESS, msg)
            self.assertNotIn(NETWORK_NAME, bridges())

            bonds[BONDING_NAME] = {'remove': True}
            status, msg = self.setupNetworks({}, bonds, NOCHK)
            self.assertEqual(status, SUCCESS, msg)

    def testRedefineBondedNetworkIPs(self):
        """Test for https://bugzilla.redhat.com/1097674"""
        with dummyIf(2) as nics:
            network = {NETWORK_NAME: {'bonding': BONDING_NAME,
                                      'bridged': False, 'ipaddr': '1.1.1.1',
                                      'prefix': '24'}}
            bonds = {BONDING_NAME: {'nics': nics}}
            status, msg = self.setupNetworks(network, bonds, NOCHK)
            self.assertEqual(status, SUCCESS, msg)
            self.assertNetworkExists(NETWORK_NAME)
            self.assertEqual(
                self.vdsm_net.netinfo.bondings[BONDING_NAME]['addr'],
                network[NETWORK_NAME]['ipaddr'])
            self.assertEqual(len(
                self.vdsm_net.netinfo.bondings[BONDING_NAME]['ipv4addrs']), 1)

            # Redefine the ip address
            network[NETWORK_NAME]['ipaddr'] = '1.1.1.2'
            status, msg = self.setupNetworks(network, bonds, NOCHK)
            self.assertEqual(status, SUCCESS, msg)
            self.assertNetworkExists(NETWORK_NAME)
            self.assertEqual(
                self.vdsm_net.netinfo.bondings[BONDING_NAME]['addr'],
                network[NETWORK_NAME]['ipaddr'])
            self.assertEqual(len(
                self.vdsm_net.netinfo.bondings[BONDING_NAME]['ipv4addrs']), 1)

            # Redefine the ip address
            network[NETWORK_NAME]['ipaddr'] = '1.1.1.3'
            status, msg = self.setupNetworks(network, bonds, NOCHK)
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
            status, msg = self.setupNetworks(network, bonds, NOCHK)
            self.assertEqual(status, SUCCESS, msg)

    @cleanupNet
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

            status, msg = self.setupNetworks(networks, bonds, NOCHK)
            self.assertEqual(status, SUCCESS, msg)
            for network, attributes in networks.iteritems():
                self.assertNetworkExists(network)
                self.assertMtu(attributes['mtu'], network)

            # Check that the bond's mtu is the maximum amongst the networks,
            # which range [MTU_LOWEST, MTU_MAX - MTU_STEP]
            self.assertMtu(MTU_MAX - MTU_STEP, BONDING_NAME)

            # cleanup
            for network in networks.iterkeys():
                networks[network] = {'remove': True}
            bonds[BONDING_NAME] = {'remove': True}
            status, msg = self.setupNetworks(networks, bonds, NOCHK)
            self.assertEqual(status, SUCCESS, msg)

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
    def testSetupNetworksConnectivityCheck(self):
        status, msg = self.setupNetworks(
            {NETWORK_NAME: {'bridged': True}}, {},
            {'connectivityCheck': True, 'connectivityTimeout': 0.1})
        self.assertEqual(status, errors.ERR_LOST_CONNECTION)
        self.assertNetworkDoesntExist(NETWORK_NAME)

    @cleanupNet
    def testSetupNetworksConnectivityCheckOverExistingBond(self):
        with dummyIf(2) as nics:
            # setup initial bonding
            status, msg = self.setupNetworks(
                {}, {BONDING_NAME: {'nics': nics}}, NOCHK)
            self.assertEqual(status, SUCCESS, msg)
            self.assertBondExists(BONDING_NAME, nics)

            # setup a network on top of existing bond
            status, msg = self.setupNetworks(
                {NETWORK_NAME: {'bridged': True, 'bonding': BONDING_NAME}}, {},
                {'connectivityCheck': True, 'connectivityTimeout': 0.1})
            self.assertEqual(status, errors.ERR_LOST_CONNECTION)
            self.assertNetworkDoesntExist(NETWORK_NAME)
            self.assertBondExists(BONDING_NAME, nics)

            # cleanup
            status, msg = self.setupNetworks(
                {}, {BONDING_NAME: {'remove': True}}, NOCHK)
            self.assertEqual(status, SUCCESS, msg)
            self.assertBondDoesntExist(BONDING_NAME, nics)

    @permutations([[True], [False]])
    @cleanupNet
    def testSetupNetworkOutboundQos(self, bridged):
        hostQos = {
            'out': {
                'ls': {
                    'm1': 4 * 1000 ** 2,  # 4Mbit/s
                    'd': 100 * 1000,  # 100 microseconds
                    'm2': 3 * 1000 ** 2},  # 3Mbit/s
                'ul': {
                    'm2': 8 * 1000 ** 2}}}  # 8Mbit/s
        with dummyIf(1) as nics:
            nic, = nics
            attrs = {'vlan': VLAN_ID, 'nic': nic, 'bridged': bridged,
                     'hostQos': hostQos}
            status, msg = self.setupNetworks({NETWORK_NAME: attrs}, {}, NOCHK)

            self.assertEqual(status, SUCCESS, msg)
            self.assertNetworkExists(NETWORK_NAME, hostQos=hostQos)

            # Cleanup
            status, msg = self.setupNetworks(
                {NETWORK_NAME: dict(remove=True)}, {}, NOCHK)
            self.assertEqual([], list(tc._filters(nic)),
                             'Failed to cleanup tc filters')
            self.assertEqual([], list(tc.classes(nic)),
                             'Failed to cleanup tc classes')
            # Real devices always get a qdisc, dummies don't, so 0 after
            # deletion.
            self.assertEqual(0, len(list(tc.qdiscs(nic))),
                             'Failed to cleanup tc hfsc and ingress qdiscs')
            self.assertEqual(status, SUCCESS, msg)

    @cleanupNet
    def testSetupNetworksActiveSlave(self):
        def create_bond_with_mode(nics, mode):
            bonding = {BONDING_NAME: {'nics': nics}}
            bonding[BONDING_NAME]['options'] = 'mode=%s' % mode
            status, msg = self.setupNetworks({}, bonding, NOCHK)
            self.assertEqual(status, SUCCESS, msg)
        with dummyIf(2) as nics:
            create_bond_with_mode(nics, 1)
            self.assert_active_slave_exists(BONDING_NAME, nics)
            create_bond_with_mode(nics, 4)
            self.assert_active_slave_doesnt_exist(BONDING_NAME)
            status, msg = self.setupNetworks(
                {}, {BONDING_NAME: {'remove': True}}, NOCHK)
            self.assertEqual(status, SUCCESS, msg)

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

    @cleanupNet
    def testSetupNetworksRemoveBondWithKilledEnslavedNics(self):

        dummy = Dummy()
        dummy.create()
        nics = [dummy.devName]
        try:
            status, msg = self.setupNetworks(
                {NETWORK_NAME:
                    {'bonding': BONDING_NAME, 'bridged': False}},
                {BONDING_NAME: {'nics': nics}}, NOCHK)
            self.assertEqual(status, SUCCESS, msg)
            self.assertNetworkExists(NETWORK_NAME)
            self.assertBondExists(BONDING_NAME, nics)
        finally:
            dummy.remove()

        status, msg = self.setupNetworks(
            {NETWORK_NAME: {'remove': True}},
            {BONDING_NAME: {'remove': True}}, NOCHK)
        self.assertEqual(status, SUCCESS, msg)
        self.assertNetworkDoesntExist(NETWORK_NAME)
        self.assertBondDoesntExist(BONDING_NAME, nics)

    @requiresUnifiedPersistence("with ifcfg persistence, "
                                "vdsm-restore-net-config doesn't restore "
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
                                "vdsm-restore-net-config doesn't restore "
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

    @cleanupNet
    @ValidateRunningAsRoot
    def test_setupNetworks_bond_with_custom_option(self):
        with dummyIf(2) as nics:
            status, msg = self.setupNetworks(
                {},
                {BONDING_NAME: {'nics': nics,
                                'options': 'custom=foo:bar mode=4'}},
                NOCHK)
            self.assertEqual(status, SUCCESS, msg)
            self.assertBondExists(BONDING_NAME, nics)
            if vdsm.config.config.get('vars', 'net_persistence') == 'unified':
                # custom property has to be persisted (if unified persistence
                # is used), but not reported by netinfo.
                self._assert_exact_bond_opts(BONDING_NAME, ['mode=4'])
                bond = self.vdsm_net.config.bonds.get(BONDING_NAME)
                self.assertSetEqual(set(['mode=4', 'custom=foo:bar']),
                                    set(bond.get('options').split()))

            status, msg = self.setupNetworks(
                {}, {BONDING_NAME: {'remove': True}}, NOCHK)
            self.assertEqual(status, SUCCESS, msg)
            self.assertBondDoesntExist(BONDING_NAME, nics)

    @cleanupNet
    @ValidateRunningAsRoot
    def test_remove_bond_under_network(self):
        with dummyIf(1) as nics:
            status, msg = self.setupNetworks(
                {NETWORK_NAME:
                    {'bonding': BONDING_NAME, 'bridged': False}},
                {BONDING_NAME: {'nics': nics}}, NOCHK)
            self.assertEqual(status, SUCCESS, msg)
            self.assertNetworkExists(NETWORK_NAME)
            self.assertBondExists(BONDING_NAME, nics)

            status, msg = self.setupNetworks(
                {}, {BONDING_NAME: {'remove': True}}, NOCHK)
            self.assertEqual(status, errors.ERR_USED_BOND, msg)
            self.assertNetworkExists(NETWORK_NAME)
            self.assertBondExists(BONDING_NAME, nics)

            status, msg = self.setupNetworks(
                {NETWORK_NAME: {'remove': True}},
                {BONDING_NAME: {'remove': True}}, NOCHK)
            self.assertEqual(status, SUCCESS, msg)
            self.assertNetworkDoesntExist(NETWORK_NAME)
            self.assertBondDoesntExist(BONDING_NAME, nics)

    @cleanupNet
    @ValidateRunningAsRoot
    def test_drop_initial_network_nic_ip_config(self):
        with dummyIf(1) as nics:
            nic, = nics
            sysctl.disable_ipv6(nic, False)
            addrAdd(nic, IP_ADDRESS, IP_CIDR)
            addrAdd(nic, IPv6_ADDRESS, IPv6_CIDR, family=6)
            try:
                status, msg = self.setupNetworks(
                    {NETWORK_NAME: {'nic': nic, 'bridged': True}}, {}, NOCHK)
                self.assertEqual(status, SUCCESS, msg)

                ipv4addrs = self.vdsm_net.netinfo.nics[nic]['ipv4addrs']
                ipv6addrs = self.vdsm_net.netinfo.nics[nic]['ipv6addrs']
                self.assertNotIn(IP_ADDRESS_AND_CIDR, ipv4addrs)
                self.assertNotIn(IPv6_ADDRESS_AND_CIDR, ipv6addrs)

                status, msg = self.setupNetworks(
                    {NETWORK_NAME: {'remove': True}}, {}, NOCHK)
                self.assertEqual(status, SUCCESS, msg)
                self.assertNetworkDoesntExist(NETWORK_NAME)
            finally:
                addrFlush(nic)

    @cleanupNet
    @ValidateRunningAsRoot
    def test_drop_initial_bond_slaves_ip_config(self):
        with dummyIf(2) as nics:
            nic_1, nic_2 = nics
            sysctl.disable_ipv6(nic_1, False)
            addrAdd(nic_1, IP_ADDRESS, IP_CIDR)
            addrAdd(nic_1, IPv6_ADDRESS, IPv6_CIDR, family=6)
            try:
                status, msg = self.setupNetworks(
                    {}, {BONDING_NAME: {'nics': [nic_1, nic_2]}}, NOCHK)
                self.assertEqual(status, SUCCESS, msg)

                ipv4addrs = self.vdsm_net.netinfo.nics[nic_1]['ipv4addrs']
                ipv6addrs = self.vdsm_net.netinfo.nics[nic_1]['ipv6addrs']
                self.assertNotIn(IP_ADDRESS_AND_CIDR, ipv4addrs)
                self.assertNotIn(IPv6_ADDRESS_AND_CIDR, ipv6addrs)

                status, msg = self.setupNetworks(
                    {}, {BONDING_NAME: {'remove': True}}, NOCHK)
                self.assertEqual(status, SUCCESS, msg)
                self.assertBondDoesntExist(BONDING_NAME, nics)
            finally:
                addrFlush(nic_1)

    @cleanupNet
    def test_rollback(self):
        with dummyIf(3) as nics:
            NET1 = NETWORK_NAME + '1'
            NET2 = NETWORK_NAME + '2'

            # setup initial network
            status, msg = self.setupNetworks(
                {NET1:
                 {'bonding': BONDING_NAME, 'bridged': True}},
                {BONDING_NAME: {'nics': nics[:2]}}, NOCHK)
            self.assertEqual(status, SUCCESS, msg)
            self.assertNetworkExists(NET1)
            self.assertBondExists(BONDING_NAME, nics[:2])

            # setup network with invalid IP, expecting failure
            status, msg = self.setupNetworks(
                {NET2:
                 {'nic': nics[2], 'bridged': True, 'vlan': VLAN_ID,
                  'netmask': '300.300.300.300', 'ipaddr': '300.300.300.300'}},
                {}, NOCHK)
            self.assertNotEqual(status, SUCCESS, msg)
            self.assertNetworkDoesntExist(NET2)

            # test if initial network is still there
            self.assertNetworkExists(NET1)
            self.assertBondExists(BONDING_NAME, nics[:2])

            # cleanup
            status, msg = self.setupNetworks(
                {NET1: {'remove': True}},
                {BONDING_NAME: {'remove': True}}, NOCHK)
            self.assertEqual(status, SUCCESS, msg)
            self.assertNetworkDoesntExist(NET1)
            self.assertBondDoesntExist(BONDING_NAME, nics)

    @cleanupNet
    def test_setupNetworks_swap_slaves_between_bonds(self):
        with dummyIf(4) as nics:
            nics0 = nics[0:2]
            nics1 = nics[2:4]
            bondings = {
                'bond0': {'nics': nics0},
                'bond1': {'nics': nics1}
            }
            status, msg = self.setupNetworks({}, bondings, NOCHK)
            self.assertEqual(status, SUCCESS, msg)
            self.assertBondExists('bond0', nics0)
            self.assertBondExists('bond1', nics1)

            bondings = {
                'bond1': {'nics': nics0},
                'bond0': {'nics': nics1}
            }
            status, msg = self.setupNetworks({}, bondings, NOCHK)
            self.assertEqual(status, SUCCESS, msg)
            self.assertBondExists('bond0', nics1)
            self.assertBondExists('bond1', nics0)

            # cleanup
            bondings = {
                'bond0': {'remove': True},
                'bond1': {'remove': True},
            }
            status, msg = self.setupNetworks({}, bondings, NOCHK)
            self.assertEqual(status, SUCCESS, msg)
            self.assertBondDoesntExist('bond0')
            self.assertBondDoesntExist('bond1')

    @contextmanager
    def setup_bonds_with_veth_pair(self, bond_options):
        with veth_pair() as (n1, n2), veth_pair() as (n3, n4):
            nics = [n1, n2, n3, n4]
            bonds = {BONDING_NAME: (n1, n3), BONDING_NAME + "0": (n2, n4)}
            for bond, pair in six.iteritems(bonds):
                bonding = {'nics': pair}
                bonding.update({'options': bond_options})
                status, msg = self.setupNetworks(
                    {},
                    {bond: bonding},
                    NOCHK)
                self.assertEqual(status, SUCCESS, msg)
                self.assertBondExists(bond, pair)
            status, msg, info = self.vdsm_net.getVdsCapabilities()
            bond_caps, nic_caps = info['bondings'], info['nics']
            try:
                yield bonds, nics, bond_caps, nic_caps
            finally:
                for bond in bonds:
                    status, msg = self.setupNetworks(
                        {}, {bond: {'remove': True}}, NOCHK)
                    self.assertEqual(status, SUCCESS, msg)
                    self.assertBondDoesntExist(bond, bonds[bond])

    @cleanupNet
    @ValidateRunningAsRoot
    def test_bond_mode4_caps_aggregator_id(self):
        with self.setup_bonds_with_veth_pair(
                'mode=4 lacp_rate=1 miimon=100'
        ) as (bonds, nics, bond_caps, nic_caps):
            for bond in bonds:
                self.assertIn('ad_aggregator_id', bond_caps[bond])
                self.assertIn('ad_partner_mac', bond_caps[bond])
            bond1, bond2 = bonds
            self.assertEqual(
                bond_caps[bond1]['ad_partner_mac'],
                bond_caps[bond2]['hwaddr']
            )
            self.assertEqual(
                bond_caps[bond2]['ad_partner_mac'],
                bond_caps[bond1]['hwaddr']
            )
            for nic in nics:
                self.assertIn('ad_aggregator_id', nic_caps[nic])
                self.assertNotEqual(nic_caps[nic]['ad_aggregator_id'], None)

    @cleanupNet
    @ValidateRunningAsRoot
    def test_bond_mode0_caps_aggregator_id(self):
        with self.setup_bonds_with_veth_pair(
                'mode=0'
        ) as (bonds, nics, bond_caps, nic_caps):
            for bond in bonds:
                self.assertNotIn('ad_aggregator_id', bond_caps[bond])
                self.assertNotIn('ad_partner_mac', bond_caps[bond])
            for nic in nics:
                self.assertNotIn('ad_aggregator_id', nic_caps[nic])

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

        rc, _, err = execCmd([EXT_IFUP, bond_name])
        self.assertEqual(rc, SUCCESS, err)
        rc, _, err = execCmd([EXT_IFUP, bond_name + '.' + vlan_id])
        self.assertEqual(rc, SUCCESS, err)

        try:
            yield
        finally:
            rc, _, err = execCmd([EXT_IFDOWN, bond_name + '.' + vlan_id])
            self.assertEqual(rc, SUCCESS, err)
            rc, _, err = execCmd([EXT_IFDOWN, bond_name])
            self.assertEqual(rc, SUCCESS, err)

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
