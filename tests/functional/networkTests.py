#
# Copyright 2013 Red Hat, Inc.
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
from threading import Thread
import time

import neterrors

from testrunner import (VdsmTestCase as TestCaseBase,
                        expandPermutations, permutations)
from testValidation import RequireDummyMod, ValidateRunningAsRoot

import dummy
from dummy import dummyIf
from utils import cleanupNet, restoreNetConfig, SUCCESS, VdsProxy, cleanupRules

from vdsm.ipwrapper import (ruleAdd, ruleDel, routeAdd, routeDel, routeExists,
                            ruleExists, Route, Rule)

from vdsm.netinfo import operstate, prefix2netmask


NETWORK_NAME = 'test-network'
VLAN_ID = '27'
BONDING_NAME = 'bond0'
IP_ADDRESS = '240.0.0.1'
IP_NETWORK = '240.0.0.0'
IP_CIDR = '24'
IP_NETWORK_AND_CIDR = IP_NETWORK + '/' + IP_CIDR
IP_GATEWAY = '240.0.0.254'
IP_TABLE = '4026531841'  # Current implementation converts ip to its 32 bit int
                         # representation


def setupModule():
    """Persists network configuration."""
    vdsm = VdsProxy()
    vdsm.save_config()


def tearDownModule():
    """Restores the network configuration previous to running tests."""
    restoreNetConfig()


class OperStateChangedError(ValueError):
    pass


@contextmanager
def nonChangingOperstate(device):
    """Raises an exception if it detects that the device operstate changes."""
    # The current implementation is raceful (but empirically tested to work)
    # due to the fact that two changes could happen between iterations and no
    # exception would be raised.
    def changed(dev, changes):
        status = operstate(dev)
        while not done:
            try:
                newState = operstate(dev)
                time.sleep(0.1)
            except IOError as ioe:
                _, message = ioe.args
                changes.append(message)
                break
            if status != newState:
                changes.append(newState)
                status = newState

    try:
        done = False
        changes = []
        monitoring_t = Thread(target=changed, name='operstate_mon',
                              args=(device, changes))
        monitoring_t.start()
        yield
    finally:
        time.sleep(3)  # So that the last action in yield gets to kernel
        done = True
        if changes:
            raise OperStateChangedError('%s operstate changed: %r' %
                                        (device, changes))


@expandPermutations
class NetworkTest(TestCaseBase):

    def setUp(self):
        self.vdsm_net = VdsProxy()

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

            with self.vdsm_net.pinger():
                status, msg = self.vdsm_net.setupNetworks(networks, bondings,
                                                          {})
            self.assertEqual(status, SUCCESS, msg)
            for vlan_net in network_names:
                self.assertTrue(self.vdsm_net.networkExists(vlan_net, bridged))
                self.assertTrue(self.vdsm_net.bondExists(BONDING_NAME, nics))
                self.assertTrue(self.vdsm_net.vlanExists(BONDING_NAME + '.' +
                                networks[vlan_net]['vlan']))

            with self.vdsm_net.pinger():
                for vlan_net in network_names:
                    status, msg = self.vdsm_net.setupNetworks(
                        {vlan_net: {'remove': True}}, {}, {})
                    self.assertEqual(status, SUCCESS, msg)
                    self.assertFalse(self.vdsm_net.networkExists(vlan_net,
                                                                 bridged))
                    self.assertFalse(
                        self.vdsm_net.vlanExists(BONDING_NAME + '.' +
                                                 networks[vlan_net]['vlan']))

    @cleanupNet
    @permutations([[True], [False]])
    @RequireDummyMod
    @ValidateRunningAsRoot
    def testSetupNetworksAddDelBondedNetwork(self, bridged):
        with dummyIf(2) as nics:
            with self.vdsm_net.pinger():
                status, msg = self.vdsm_net.setupNetworks(
                    {NETWORK_NAME:
                        {'bonding': BONDING_NAME, 'bridged': bridged}},
                    {BONDING_NAME: {'nics': nics, 'options': 'mode=2'}}, {})
            self.assertEqual(status, SUCCESS, msg)
            self.assertTrue(self.vdsm_net.networkExists(NETWORK_NAME, bridged))
            self.assertTrue(self.vdsm_net.bondExists(BONDING_NAME, nics))

            with self.vdsm_net.pinger():
                status, msg = self.vdsm_net.setupNetworks(
                    {NETWORK_NAME: {'remove': True}}, {}, {})
            self.assertEqual(status, SUCCESS, msg)
            self.assertFalse(self.vdsm_net.networkExists(NETWORK_NAME))

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
                {'connectivityCheck': False})
            self.assertEqual(status, SUCCESS, msg)
            self.assertTrue(self.vdsm_net.bondExists(BONDING_NAME, nics))

            with nonChangingOperstate(BONDING_NAME):
                status, msg = self.vdsm_net.setupNetworks(
                    {NETWORK_NAME:
                        {'bonding': BONDING_NAME, 'bridged': bridged,
                         'vlan': VLAN_ID}},
                    {}, {'connectivityCheck': False})
            self.assertEqual(status, SUCCESS, msg)
            self.assertTrue(self.vdsm_net.networkExists(NETWORK_NAME, bridged))

            status, msg = self.vdsm_net.setupNetworks(
                {NETWORK_NAME: {'remove': True},
                 NETWORK_NAME + '0': {'remove': True}},
                {}, {'connectivityCheck': False})
            self.assertEqual(status, SUCCESS, msg)
            self.assertTrue(self.vdsm_net.bondExists(BONDING_NAME, nics))

            status, msg = self.vdsm_net.setupNetworks(
                {},
                {BONDING_NAME: {'remove': True}}, {'connectivityCheck': False})
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

            self.assertTrue(self.vdsm_net.networkExists(NETWORK_NAME, bridged))
            self.assertTrue(self.vdsm_net.bondExists(BONDING_NAME, nics))

            status, msg = self.vdsm_net.delNetwork(NETWORK_NAME)
            self.assertEqual(status, SUCCESS, msg)
            self.assertFalse(self.vdsm_net.networkExists(NETWORK_NAME))

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
            self.assertTrue(self.vdsm_net.networkExists(NETWORK_NAME))

            status, msg = self.vdsm_net.delNetwork(NETWORK_NAME,
                                                   nics=nics,
                                                   opts={'bridged': bridged})
            self.assertEqual(status, SUCCESS, msg)
            self.assertFalse(self.vdsm_net.networkExists(NETWORK_NAME))

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
                self.assertEqual(status, neterrors.ERR_BAD_BONDING, msg)

    @cleanupNet
    def testFailWithInvalidBridgeName(self):
        invalid_bridge_names = ('a' * 16, 'a b', 'a\tb', 'a.b', 'a:b')
        for bridge_name in invalid_bridge_names:
            status, msg = self.vdsm_net.addNetwork(bridge_name)
            self.assertEqual(status, neterrors.ERR_BAD_BRIDGE, msg)

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
            self.assertEqual(status, neterrors.ERR_BAD_ADDR, msg)

    @cleanupNet
    @permutations([[True], [False]])
    def testFailWithInvalidNic(self, bridged):
        status, msg = self.vdsm_net.addNetwork(NETWORK_NAME,
                                               nics=['nowaythisnicexists'],
                                               opts={'bridged': bridged})

        self.assertEqual(status, neterrors.ERR_BAD_NIC, msg)

    @cleanupNet
    @permutations([[True], [False]])
    def testFailWithInvalidParams(self, bridged):
        status, msg = self.vdsm_net.addNetwork(NETWORK_NAME, VLAN_ID,
                                               opts={'bridged': bridged})
        self.assertEqual(status, neterrors.ERR_BAD_PARAMS, msg)

        status, msg = self.vdsm_net.addNetwork(NETWORK_NAME,
                                               bond=BONDING_NAME,
                                               opts={'bridged': bridged})
        self.assertEqual(status, neterrors.ERR_BAD_PARAMS, msg)

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
                self.assertTrue(self.vdsm_net.networkExists(netVlan,
                                                            bridged=bridged))
                self.assertTrue(self.vdsm_net.vlanExists(nics[0] + '.' +
                                                         str(vlanId)))

                self.vdsm_net.delNetwork(netVlan)
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

            self.assertTrue(self.vdsm_net.networkExists(NETWORK_NAME,
                                                        bridged=bridged))
            self.assertTrue(self.vdsm_net.vlanExists(nics[0] + '.' + VLAN_ID))

            self.vdsm_net.delNetwork(NETWORK_NAME)
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
            with nonChangingOperstate(BONDING_NAME):
                for netVlan, vlanId in NET_VLANS[1:]:
                    status, msg = self.vdsm_net.addNetwork(netVlan,
                                                           vlan=vlanId,
                                                           bond=BONDING_NAME,
                                                           nics=nics,
                                                           opts=opts)
                    self.assertEquals(status, SUCCESS, msg)
                    self.assertTrue(
                        self.vdsm_net.networkExists(netVlan, bridged=bridged))
            for _, vlanId in NET_VLANS:
                msg = "vlan %s doesn't exist" % vlanId
                vlanName = '%s.%s' % (BONDING_NAME, vlanId)
                self.assertTrue(self.vdsm_net.vlanExists(vlanName), msg)

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
            self.assertTrue(self.vdsm_net.networkExists(NETWORK_NAME,
                                                        bridged=bridged))
            status, msg = self.vdsm_net.delNetwork(NETWORK_NAME, vlan=vlan_id,
                                                   bond=BONDING_NAME,
                                                   nics=nics)
            self.assertEqual(status, SUCCESS, msg)

    @cleanupNet
    @RequireDummyMod
    @ValidateRunningAsRoot
    def testQosNetwork(self):
        with dummyIf(1) as nics:
            qos = {'qosInbound': {'average': '1024', 'burst': '2048',
                                  'peak': '42'},
                   'qosOutbound': {'average': '2400', 'burst': '2048',
                                   'peak': '100'}}

            status, msg = self.vdsm_net.addNetwork(NETWORK_NAME,
                                                   nics=nics,
                                                   opts=qos)
            self.assertEqual(status, SUCCESS, msg)

            qosInbound, qosOutbound = self.vdsm_net.networkQos(NETWORK_NAME)
            self.assertEqual(qos['qosInbound'], qosInbound)
            self.assertEqual(qos['qosOutbound'], qosOutbound)

            status, msg = self.vdsm_net.delNetwork(NETWORK_NAME)

            self.assertEqual(status, SUCCESS, msg)

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
            self.assertEqual(status, neterrors.ERR_USED_BRIDGE, msg)

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
            self.assertEqual(status, neterrors.ERR_BAD_BRIDGE, msg)

    @cleanupNet
    @permutations([[True], [False]])
    @RequireDummyMod
    @ValidateRunningAsRoot
    def testEditWithoutAdd(self, bridged):
        with dummyIf(1) as nics:
            status, msg = self.vdsm_net.editNetwork(NETWORK_NAME, NETWORK_NAME,
                                                    nics=nics,
                                                    opts={'bridged': bridged})
            self.assertEqual(status, neterrors.ERR_BAD_BRIDGE, msg)

    @cleanupNet
    @permutations([[True], [False]])
    @RequireDummyMod
    @ValidateRunningAsRoot
    def testSetupNetworksAddVlan(self, bridged):
        with dummyIf(1) as nics:
            with self.vdsm_net.pinger():
                nic, = nics
                attrs = dict(vlan=VLAN_ID, nic=nic, bridged=bridged)
                status, msg = self.vdsm_net.setupNetworks({NETWORK_NAME:
                                                           attrs}, {}, {})

                self.assertEqual(status, SUCCESS, msg)
                self.assertTrue(self.vdsm_net.networkExists(NETWORK_NAME))
                self.assertTrue(self.vdsm_net.vlanExists('%s.%s' %
                                                         (nic, VLAN_ID)))

                status, msg = self.vdsm_net.setupNetworks({NETWORK_NAME:
                                                           dict(remove=True)},
                                                          {}, {})
                self.assertEqual(status, SUCCESS, msg)

    @cleanupNet
    @RequireDummyMod
    @ValidateRunningAsRoot
    def testSetupNetworksConvertVlanNetBridgeness(self):
        "Convert a bridged networks to a bridgeless one and viceversa"
        opts = {'connectivityCheck': False}

        def setupNetworkBridged(nic, bridged, opts=opts):
            networks = {NETWORK_NAME: dict(vlan=VLAN_ID,
                                           nic=nic, bridged=bridged)}
            status, msg = self.vdsm_net.setupNetworks(networks, {},
                                                      opts)
            self.assertEqual(status, SUCCESS, msg)
            self.assertTrue(self.vdsm_net.networkExists(NETWORK_NAME, bridged))

        with dummyIf(1) as nics:
            setupNetworkBridged(nics[0], True)
            setupNetworkBridged(nics[0], False)
            setupNetworkBridged(nics[0], True)

        status, msg = self.vdsm_net.setupNetworks({NETWORK_NAME:
                                                   dict(remove=True)},
                                                  {}, opts)

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

            with self.vdsm_net.pinger():
                status, msg = self.vdsm_net.setupNetworks(networks, {}, {})
                self.assertEqual(status, SUCCESS, msg)

                for vlan_net, tag in NET_VLANS:
                    self.assertTrue(self.vdsm_net.networkExists(vlan_net,
                                                                bridged))
                    self.assertTrue(self.vdsm_net.vlanExists(nic + '.' + tag))

                networks = dict((vlan_net, {'remove': True})
                                for vlan_net, _ in NET_VLANS)
                status, msg = self.vdsm_net.setupNetworks(networks, {}, {})

                self.assertEqual(status, SUCCESS, msg)

                for vlan_net, tag in NET_VLANS:
                    self.assertFalse(self.vdsm_net.networkExists(vlan_net,
                                                                 bridged))
                    self.assertFalse(
                        self.vdsm_net.vlanExists(nic + '.' + tag))

    @cleanupNet
    @RequireDummyMod
    @ValidateRunningAsRoot
    def testSetupNetworksNetCompatibilityBondSingleBridge(self):
        with dummyIf(1) as nics:
            with self.vdsm_net.pinger():
                # Only single non-VLANed bridged network allowed
                d = dict(bonding=BONDING_NAME, bridged=True)
                status, msg = self.vdsm_net.setupNetworks({NETWORK_NAME: d},
                                                          {BONDING_NAME:
                                                           dict(nics=nics)},
                                                          {})
                self.assertEqual(status, SUCCESS, msg)
                self.assertTrue(self.vdsm_net.networkExists(NETWORK_NAME,
                                                            bridged=True))

                # Try to add additional bridgeless network, should fail
                netNameBridgeless = NETWORK_NAME + '-2'
                d['bridged'] = False
                status, msg = self.vdsm_net.setupNetworks({netNameBridgeless:
                                                           d}, {}, {})
                self.assertTrue(status != SUCCESS, msg)

                # Try to add additional bridged network, should fail
                netNameBridged = NETWORK_NAME + '-3'
                d['bridged'] = True
                status, msg = self.vdsm_net.setupNetworks({netNameBridged: d},
                                                          {}, {})
                self.assertTrue(status != SUCCESS, msg)

                # Try to add additional VLANed bridgeless network, should fail
                netNameVlanBridgeless = NETWORK_NAME + '-4'
                networks = dict(netNameVlanBridgeless={'bonding': BONDING_NAME,
                                                       'vlan': '100',
                                                       'bridged': False})
                status, msg = self.vdsm_net.setupNetworks(networks, {}, {})
                self.assertTrue(status != SUCCESS, msg)

                # Try to add additional VLANed bridged network, should fail
                netNameVlanBridged = NETWORK_NAME + '-5'
                networks['vlan'] = '200'
                networks['bridged'] = True
                status, msg = self.vdsm_net.setupNetworks({netNameVlanBridged:
                                                           networks}, {}, {})
                self.assertTrue(status != SUCCESS, msg)

                self.assertFalse(self.vdsm_net.networkExists(
                                 netNameBridgeless))
                self.assertFalse(self.vdsm_net.networkExists(netNameBridged))
                self.assertFalse(self.vdsm_net.networkExists(
                                 netNameVlanBridgeless))
                self.assertFalse(self.vdsm_net.networkExists(
                                 netNameVlanBridged))

                # Clean all
                status, msg = self.vdsm_net.setupNetworks({NETWORK_NAME:
                                                           dict(remove=True)},
                                                          {BONDING_NAME:
                                                           dict(remove=True)},
                                                          {})
                self.assertEquals(status, SUCCESS, msg)

                self.assertFalse(self.vdsm_net.networkExists(NETWORK_NAME))
                self.assertFalse(self.vdsm_net.bondExists(
                                 BONDING_NAME, nics=nics))

    @cleanupNet
    @RequireDummyMod
    @ValidateRunningAsRoot
    def testSetupNetworksNetCompatibilityBondSingleBridgeless(self):
        with dummyIf(1) as nics:
            with self.vdsm_net.pinger():
                # Multiple VLANed networks (bridged/bridgeless) with only one
                # non-VLANed bridgeless network permited
                d = dict(bonding=BONDING_NAME, bridged=False)
                status, msg = self.vdsm_net.setupNetworks({NETWORK_NAME: d},
                                                          {BONDING_NAME:
                                                           dict(nics=nics)},
                                                          {})
                self.assertEqual(status, SUCCESS, msg)
                self.assertTrue(self.vdsm_net.networkExists(NETWORK_NAME,
                                                            bridged=False))

                # Try to add additional bridgeless network, should fail
                netNameBridgeless = NETWORK_NAME + '-2'
                status, msg = self.vdsm_net.setupNetworks({netNameBridgeless:
                                                           d}, {}, {})
                self.assertTrue(status != SUCCESS, msg)

                # Try to add additional bridged network, should fail
                netNameBridged = NETWORK_NAME + '-3'
                d['bridged'] = True
                status, msg = self.vdsm_net.setupNetworks({netNameBridged: d},
                                                          {}, {})
                self.assertTrue(status != SUCCESS, msg)

                # Try to add additional VLANed bridgeless network,
                # should succeed
                netNameVlanBridgeless = NETWORK_NAME + '-4'
                d['vlan'], d['bridged'] = '100', False
                networks = {netNameVlanBridgeless: d}
                status, msg = self.vdsm_net.setupNetworks(networks, {}, {})

                self.assertEqual(status, SUCCESS, msg)

                # Try to add additional VLANed bridged network, should succeed
                netNameVlanBridged = NETWORK_NAME + '-5'
                d['vlan'], d['bridged'] = '200', True
                status, msg = self.vdsm_net.setupNetworks({netNameVlanBridged:
                                                           d}, {}, {})
                self.assertEqual(status, SUCCESS, msg)

                self.assertFalse(self.vdsm_net.networkExists(
                                 netNameBridgeless))
                self.assertFalse(self.vdsm_net.networkExists(netNameBridged))

                self.assertTrue(self.vdsm_net.networkExists(
                                netNameVlanBridgeless))
                self.assertTrue(self.vdsm_net.networkExists(
                                netNameVlanBridged))

                # Clean all
                r = dict(remove=True)
                networks = {NETWORK_NAME: r,
                            netNameVlanBridgeless: r,
                            netNameVlanBridged: r}
                status, msg = self.vdsm_net.setupNetworks(networks,
                                                          {BONDING_NAME: r},
                                                          {})

                self.assertEqual(status, SUCCESS, msg)

                self.assertFalse(self.vdsm_net.networkExists(NETWORK_NAME))
                self.assertFalse(self.vdsm_net.networkExists(
                                 netNameVlanBridgeless))
                self.assertFalse(self.vdsm_net.networkExists(
                                 netNameVlanBridged))
                self.assertFalse(self.vdsm_net.bondExists(BONDING_NAME, nics))

    @cleanupNet
    @RequireDummyMod
    @ValidateRunningAsRoot
    def testSetupNetworksNetCompatibilityNicSingleBridge(self):
        with dummyIf(1) as nics:
            nic, = nics
            with self.vdsm_net.pinger():
                # Only single non-VLANed bridged network allowed
                networks = {NETWORK_NAME: dict(nic=nic, bridged=True)}
                status, msg = self.vdsm_net.setupNetworks(networks, {}, {})

                self.assertEquals(status, SUCCESS, msg)
                self.assertTrue(self.vdsm_net.networkExists(
                                NETWORK_NAME, bridged=True))

                # Try to add additional bridgeless network, should fail
                netNameBridgeless = NETWORK_NAME + '-2'
                networks = {netNameBridgeless: dict(nic=nic, bridged=False)}
                status, msg = self.vdsm_net.setupNetworks(networks, {}, {})

                self.assertTrue(status != SUCCESS, msg)

                # Try to add additional bridged network, should fail
                netNameBridged = NETWORK_NAME + '-3'
                networks = {netNameBridged: dict(nic=nic, bridged=True)}
                status, msg = self.vdsm_net.setupNetworks(networks, {}, {})

                self.assertTrue(status != SUCCESS, msg)

                # Try to add additional VLANed bridgeless network, should fail
                netNameVlanBridgeless = NETWORK_NAME + '-4'
                networks = {netNameVlanBridgeless: dict(nic=nic, vlan='100',
                                                        bridged=False)}
                status, msg = self.vdsm_net.setupNetworks(networks, {}, {})

                self.assertTrue(status != SUCCESS, msg)

                # Try to add additional VLANed bridged network, should fail
                netNameVlanBridged = NETWORK_NAME + '-5'
                networks = {netNameVlanBridged: dict(nic=nic, vlan='200',
                                                     bridged=True)}
                status, msg = self.vdsm_net.setupNetworks(networks, {}, {})

                self.assertTrue(status != SUCCESS, msg)

                self.assertFalse(self.vdsm_net.networkExists(
                                 netNameBridgeless))
                self.assertFalse(self.vdsm_net.networkExists(
                                 netNameBridged, bridged=True))
                self.assertFalse(self.vdsm_net.networkExists(
                                 netNameVlanBridgeless))
                self.assertFalse(self.vdsm_net.networkExists(
                                 netNameBridged, bridged=True))

                # Clean all
                status, msg = self.vdsm_net.setupNetworks({NETWORK_NAME:
                                                           dict(remove=True)},
                                                          {}, {})
                self.assertEquals(status, SUCCESS, msg)
                self.assertFalse(self.vdsm_net.networkExists(NETWORK_NAME))

    @cleanupNet
    @RequireDummyMod
    @ValidateRunningAsRoot
    def testSetupNetworksNetCompatibilityNicSingleBridgeless(self):
        with dummyIf(1) as nics:
            nic, = nics
            with self.vdsm_net.pinger():
                # Multiple VLANed networks (bridged/bridgeless) with only one
                # non-VLANed bridgeless network permited
                networks = {NETWORK_NAME: dict(nic=nic, bridged=False)}
                status, msg = self.vdsm_net.setupNetworks(networks, {}, {})

                self.assertEquals(status, SUCCESS, msg)
                self.assertTrue(self.vdsm_net.networkExists(NETWORK_NAME,
                                                            bridged=False))

                # Try to add additional bridgeless network, should fail
                netNameBridgeless = NETWORK_NAME + '-2'
                networks = {netNameBridgeless: dict(nic=nic, bridged=False)}
                status, msg = self.vdsm_net.setupNetworks(networks, {}, {})

                self.assertTrue(status != SUCCESS, msg)

                # Try to add additional bridged network, should fail
                netNameBridged = NETWORK_NAME + '-3'
                networks = {netNameBridged: dict(nic=nic, bridged=True)}
                status, msg = self.vdsm_net.setupNetworks(networks, {}, {})

                self.assertTrue(status != SUCCESS, msg)

                # Try to add additional VLANed bridgeless network,
                # should succeed
                netNameVlanBridgeless = NETWORK_NAME + '-4'
                networks = {netNameVlanBridgeless: dict(nic=nic, vlan='100',
                                                        bridged=False)}
                status, msg = self.vdsm_net.setupNetworks(networks, {}, {})

                self.assertEquals(status, SUCCESS, msg)

                # Try to add additional VLANed bridged network, should succeed
                netNameVlanBridged = NETWORK_NAME + '-5'
                networks = {netNameVlanBridged: dict(nic=nic, vlan='200',
                                                     bridged=True)}
                status, msg = self.vdsm_net.setupNetworks(networks, {}, {})

                self.assertEquals(status, SUCCESS, msg)

                self.assertFalse(self.vdsm_net.networkExists(
                                 netNameBridgeless))
                self.assertFalse(self.vdsm_net.networkExists(netNameBridged))
                self.assertTrue(self.vdsm_net.networkExists(
                                netNameVlanBridgeless))
                self.assertTrue(self.vdsm_net.networkExists(netNameVlanBridged,
                                                            bridged=True))

                # Clean all
                networks = {NETWORK_NAME: dict(remove=True),
                            netNameVlanBridgeless: dict(remove=True),
                            netNameVlanBridged: dict(remove=True)}
                status, msg = self.vdsm_net.setupNetworks(networks, {}, {})

                self.assertEqual(status, SUCCESS, msg)

                self.assertFalse(self.vdsm_net.networkExists(NETWORK_NAME))
                self.assertFalse(self.vdsm_net.networkExists(
                                 netNameVlanBridgeless))
                self.assertFalse(self.vdsm_net.networkExists(
                                 netNameVlanBridged, bridged=True))

    @cleanupNet
    @permutations([[True], [False]])
    @RequireDummyMod
    @ValidateRunningAsRoot
    def testSetupNetworksAddNetworkToNicAfterBondResizing(self, bridged):
        with dummyIf(3) as nics:
            with self.vdsm_net.pinger():
                networks = {NETWORK_NAME: dict(bonding=BONDING_NAME,
                                               bridged=bridged)}
                status, msg = self.vdsm_net.setupNetworks(networks,
                                                          {BONDING_NAME:
                                                           dict(nics=nics)},
                                                          {})

                self.assertEquals(status, SUCCESS, msg)

                self.assertTrue(self.vdsm_net.networkExists(
                                NETWORK_NAME, bridged=bridged))
                self.assertTrue(self.vdsm_net.bondExists(
                                BONDING_NAME, nics))

                # Reduce bond size and create Network on detached NIC
                with nonChangingOperstate(BONDING_NAME):
                    netName = NETWORK_NAME + '-2'
                    networks = {netName: dict(nic=nics[0],
                                              bridged=bridged)}
                    bondings = {BONDING_NAME: dict(nics=nics[1:3])}
                    status, msg = self.vdsm_net.setupNetworks(networks,
                                                              bondings, {})

                    self.assertEquals(status, SUCCESS, msg)

                    self.assertTrue(self.vdsm_net.networkExists(
                        NETWORK_NAME, bridged=bridged))
                    self.assertTrue(self.vdsm_net.networkExists(
                        netName, bridged=bridged))
                    self.assertTrue(self.vdsm_net.bondExists(
                        BONDING_NAME, nics[1:3]))

                # Clean up
                networks = {NETWORK_NAME: dict(remove=True),
                            netName: dict(remove=True)}
                bondings = {BONDING_NAME: dict(remove=True)}
                status, msg = self.vdsm_net.setupNetworks(networks,
                                                          bondings, {})
                self.assertEquals(status, SUCCESS, msg)

    @cleanupNet
    @permutations([[True], [False]])
    @RequireDummyMod
    @ValidateRunningAsRoot
    def testSetupNetworksMtus(self, bridged):
        JUMBO = '9000'
        MIDI = '4000'

        def assertMtu(mtu, *elems):
            for elem in elems:
                self.assertEquals(mtu, self.vdsm_net.getMtu(elem))

        with dummyIf(3) as nics:
            with self.vdsm_net.pinger():
                networks = {NETWORK_NAME + '1':
                            dict(bonding=BONDING_NAME, bridged=bridged,
                                 vlan='100'),
                            NETWORK_NAME + '2':
                            dict(bonding=BONDING_NAME, bridged=bridged,
                                 vlan='200', mtu=MIDI)
                            }
                bondings = {BONDING_NAME: dict(nics=nics[:2])}
                status, msg = self.vdsm_net.setupNetworks(networks, bondings,
                                                          {})

                self.assertEquals(status, SUCCESS, msg)

                assertMtu(MIDI, NETWORK_NAME + '2', BONDING_NAME, nics[0],
                          nics[1])

                network = {NETWORK_NAME + '3':
                           dict(bonding=BONDING_NAME, vlan='300', mtu=JUMBO,
                                bridged=bridged)}
                status, msg = self.vdsm_net.setupNetworks(network, {}, {})

                self.assertEquals(status, SUCCESS, msg)

                self.assertTrue(self.vdsm_net.networkExists(NETWORK_NAME + '3',
                                                            bridged=bridged))
                assertMtu(JUMBO, NETWORK_NAME + '3', BONDING_NAME, nics[0],
                          nics[1])

                status, msg = self.vdsm_net.setupNetworks({NETWORK_NAME + '3':
                                                           dict(remove=True)},
                                                          {}, {})

                self.assertEquals(status, SUCCESS, msg)

                assertMtu(MIDI, NETWORK_NAME + '2', BONDING_NAME, nics[0],
                          nics[1])

                # Keep last custom MTU on the interfaces
                status, msg = self.vdsm_net.setupNetworks({NETWORK_NAME + '2':
                                                           dict(remove=True)},
                                                          {}, {})

                self.assertEquals(status, SUCCESS, msg)

                assertMtu(MIDI, BONDING_NAME, nics[0], nics[1])

                # Add additional nic to the bond
                status, msg = self.vdsm_net.setupNetworks({}, {BONDING_NAME:
                                                          dict(nics=nics)}, {})

                self.assertEquals(status, SUCCESS, msg)

                assertMtu(MIDI, BONDING_NAME, nics[0], nics[1], nics[2])

                status, msg = self.vdsm_net.setupNetworks({NETWORK_NAME + '1':
                                                           dict(remove=True)},
                                                          {BONDING_NAME:
                                                           dict(remove=True)},
                                                          {})

                self.assertEquals(status, SUCCESS, msg)

    @cleanupNet
    @permutations([[True], [False]])
    @RequireDummyMod
    @ValidateRunningAsRoot
    def testSetupNetworksAddNetworkToNicAfterBondBreaking(self, bridged):
        with dummyIf(2) as nics:
            with self.vdsm_net.pinger():
                networks = {NETWORK_NAME: dict(bonding=BONDING_NAME,
                                               bridged=bridged)}
                status, msg = self.vdsm_net.setupNetworks(networks,
                                                          {BONDING_NAME:
                                                           dict(nics=nics)},
                                                          {})
                self.assertEquals(status, SUCCESS, msg)

                self.assertTrue(self.vdsm_net.networkExists(
                                NETWORK_NAME, bridged=bridged))
                self.assertTrue(self.vdsm_net.bondExists(
                                BONDING_NAME, nics))

                # Break the bond and create Network on detached NIC
                networks = {NETWORK_NAME: dict(nic=nics[0], bridged=bridged)}
                status, msg = self.vdsm_net.setupNetworks(networks,
                                                          {BONDING_NAME:
                                                           dict(remove=True)},
                                                          {})
                self.assertEquals(status, SUCCESS, msg)

                self.assertTrue(self.vdsm_net.networkExists(
                                NETWORK_NAME, bridged=bridged))
                self.assertFalse(self.vdsm_net.bondExists(
                                 BONDING_NAME, nics))

                status, msg = self.vdsm_net.setupNetworks({NETWORK_NAME:
                                                           dict(remove=True)},
                                                          {}, {})
                self.assertEquals(status, SUCCESS, msg)

    @cleanupNet
    @permutations([[True], [False]])
    @RequireDummyMod
    @ValidateRunningAsRoot
    def testSetupNetworksKeepNetworkOnBondAfterBondResizing(self, bridged):
        with dummyIf(3) as nics:
            with self.vdsm_net.pinger():
                networks = {NETWORK_NAME: dict(bonding=BONDING_NAME,
                                               bridged=bridged)}
                bondings = {BONDING_NAME: dict(nics=nics[:2])}
                status, msg = self.vdsm_net.setupNetworks(networks,
                                                          bondings, {})
                self.assertEquals(status, SUCCESS, msg)

                self.vdsm_net.networkExists(NETWORK_NAME, bridged=bridged)
                self.vdsm_net.bondExists(BONDING_NAME, nics[:2])

                # Increase bond size
                with nonChangingOperstate(BONDING_NAME):
                    status, msg = self.vdsm_net.setupNetworks(
                        {}, {BONDING_NAME: dict(nics=nics)}, {})

                    self.assertEquals(status, SUCCESS, msg)

                    self.vdsm_net.networkExists(NETWORK_NAME, bridged=bridged)
                    self.vdsm_net.bondExists(BONDING_NAME, nics)

                status, msg = self.vdsm_net.setupNetworks({NETWORK_NAME:
                                                           dict(remove=True)},
                                                          {BONDING_NAME:
                                                           dict(remove=True)},
                                                          {})
                self.assertEquals(status, SUCCESS, msg)

    @cleanupNet
    @permutations([[True], [False]])
    @RequireDummyMod
    @ValidateRunningAsRoot
    def testSetupNetworksStableBond(self, bridged):
        def createBondedNetAndCheck(netNum, bondDict):
            netName = NETWORK_NAME + str(netNum)
            networks = {netName: dict(bonding=BONDING_NAME, bridged=bridged,
                                      vlan=str(int(VLAN_ID) + netNum))}
            status, msg = self.vdsm_net.setupNetworks(networks,
                                                      {BONDING_NAME: bondDict},
                                                      {})
            self.assertEquals(status, SUCCESS, msg)
            self.vdsm_net.networkExists(netName, bridged=bridged)
            self.vdsm_net.bondExists(BONDING_NAME, bondDict['nics'])

        with dummyIf(3) as nics:
            with self.vdsm_net.pinger():
                # Add initial vlanned net over bond
                createBondedNetAndCheck(0, {'nics': nics[:2],
                                            'options': 'mode=3 miimon=250'})

                with nonChangingOperstate(BONDING_NAME):
                    # Add additional vlanned net over the bond
                    createBondedNetAndCheck(1,
                                            {'nics': nics[:2],
                                             'options': 'mode=3 miimon=250'})
                    # Add additional vlanned net over the increasing bond
                    createBondedNetAndCheck(2,
                                            {'nics': nics,
                                             'options': 'mode=3 miimon=250'})
                    # Add additional vlanned net over the changing bond
                    createBondedNetAndCheck(3,
                                            {'nics': nics[1:],
                                             'options': 'mode=3 miimon=250'})

                # Add a network changing bond options
                with self.assertRaises(OperStateChangedError):
                    with nonChangingOperstate(BONDING_NAME):
                        createBondedNetAndCheck(4,
                                                {'nics': nics[1:],
                                                 'options': 'mode=4 miimon=9'})

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

                self.assertTrue(self.vdsm_net.bondExists(bigBond, nics))

                status, msg = self.vdsm_net.delNetwork(NETWORK_NAME)

                self.assertEqual(status, SUCCESS, msg)

                self.assertFalse(self.vdsm_net.bondExists(bigBond, nics))

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

                self.vdsm_net.bondExists(BONDING_NAME, nics=nics[:1])

                # Increase bond size
                bondings[BONDING_NAME]['nics'] = nics
                status, msg = self.vdsm_net.setupNetworks({}, bondings, {})

                self.assertEquals(status, SUCCESS, msg)

                self.vdsm_net.bondExists(BONDING_NAME, nics)

                # Reduce bond size
                REQMODE_BROADCAST = '3'
                bondings[BONDING_NAME]['nics'] = nics[:2]
                bondings[BONDING_NAME]['options'] = ('mode=%s' %
                                                     REQMODE_BROADCAST)
                status, msg = self.vdsm_net.setupNetworks({}, bondings, {})

                self.assertEquals(status, SUCCESS, msg)

                self.vdsm_net.bondExists(BONDING_NAME, nics[:2])
                self.assertEquals(self.vdsm_net.getBondMode(BONDING_NAME),
                                  REQMODE_BROADCAST)

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

                status, msg = self.vdsm_net.delNetwork(NETWORK_NAME)
                self.assertEquals(status, SUCCESS, msg)

                return self.vdsm_net.netinfo.bondings[BONDING_NAME]['hwaddr']

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

            self.vdsm_net.networkExists(NETWORK_NAME, bridged=bridged)

            self.vdsm_net.save_config()

            self.vdsm_net.restoreNetConfig()

            self.vdsm_net.networkExists(NETWORK_NAME, bridged=bridged)

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

            self.vdsm_net.networkExists(NETWORK_NAME, bridged=bridged)

            self.vdsm_net.restoreNetConfig()

            self.assertFalse(self.vdsm_net.networkExists(NETWORK_NAME,
                                                         bridged=bridged))

    @cleanupRules
    @RequireDummyMod
    @ValidateRunningAsRoot
    def testRuleExists(self):
        with dummyIf(1) as nics:
            nic, = nics
            dummy.setIP(nic, IP_ADDRESS, IP_CIDR)
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

    @RequireDummyMod
    @ValidateRunningAsRoot
    def testRouteExists(self):
        with dummyIf(1) as nics:
            nic, = nics
            dummy.setIP(nic, IP_ADDRESS, IP_CIDR)
            dummy.setLinkUp(nic)

            routes = [Route(network='0.0.0.0/0', ipaddr=IP_GATEWAY,
                            device=nic, table=IP_TABLE),
                      Route(network=IP_NETWORK_AND_CIDR,
                            ipaddr=IP_ADDRESS, device=nic, table=IP_TABLE)]
            for route in routes:
                self.assertFalse(routeExists(route))
                routeAdd(route)
                self.assertTrue(routeExists(route))
                routeDel(route)
                self.assertFalse(routeExists(route))

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
                {}, {'connectivityCheck': False})
            self.assertEqual(status, SUCCESS, msg)
            self.assertTrue(self.vdsm_net.networkExists(NETWORK_NAME, bridged))

            deviceName = NETWORK_NAME if bridged else nics[0]

            # Assert that routes and rules exist
            routes = [Route(network='0.0.0.0/0', ipaddr=IP_GATEWAY,
                            device=deviceName, table=IP_TABLE),
                      Route(network=IP_NETWORK_AND_CIDR,
                            ipaddr=IP_ADDRESS, device=deviceName,
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
                {}, {'connectivityCheck': False})
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
                self.assertTrue(self.vdsm_net.networkExists(NETWORK_NAME))
                status, msg, info = self.vdsm_net.getVdsCapabilities()
                self.assertTrue('BOOTPROTO' in info['nics'][nic]['cfg'])
                bootproto = info['nics'][nic]['cfg']['BOOTPROTO']
                self.assertEqual(bootproto, 'none')

                status, msg = self.vdsm_net.setupNetworks(
                    {vlan_name: networks[vlan_name]}, {}, {})
                self.assertEqual(status, SUCCESS, msg)
                self.assertTrue(self.vdsm_net.networkExists(vlan_name))
                status, msg, info = self.vdsm_net.getVdsCapabilities()
                self.assertTrue('BOOTPROTO' in info['nics'][nic]['cfg'])
                bootproto = info['nics'][nic]['cfg']['BOOTPROTO']
                self.assertEqual(bootproto, 'none')

                # network should be fine even after second addition of vlan
                status, msg = self.vdsm_net.setupNetworks(
                    {vlan_name: networks[vlan_name]}, {}, {})
                self.assertEqual(status, SUCCESS, msg)
                status, msg, info = self.vdsm_net.getVdsCapabilities()
                self.assertTrue('BOOTPROTO' in info['nics'][nic]['cfg'])
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
                self.assertTrue(self.vdsm_net.networkExists(NETWORK_NAME))
                self.assertTrue(self.vdsm_net.networkExists(vlan_name))
                status, msg, info = self.vdsm_net.getVdsCapabilities()
                self.assertTrue('BOOTPROTO' in info['nics'][nic]['cfg'])
                bootproto = info['nics'][nic]['cfg']['BOOTPROTO']
                self.assertEqual(bootproto, 'none')

                delete_networks = {NETWORK_NAME: {'remove': True},
                                   vlan_name: {'remove': True}}
                status, msg = self.vdsm_net.setupNetworks(delete_networks,
                                                          {}, {})
                self.assertEqual(status, SUCCESS, msg)
