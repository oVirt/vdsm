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

from utils import cleanupNet, dummyIf, restoreNetConfig, SUCCESS, VdsProxy

from vdsm.netinfo import operstate


NETWORK_NAME = 'test-network'
VLAN_ID = '27'
BONDING_NAME = 'bond0'


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
                {}, {BONDING_NAME: {'nics': nics}},
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
                {NETWORK_NAME: {'remove': True}},
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

    def testFailWithInvalidBridgeName(self):
        invalid_bridge_names = ('a' * 16, 'a b', 'a\tb', 'a.b', 'a:b')
        for bridge_name in invalid_bridge_names:
            status, msg = self.vdsm_net.addNetwork(bridge_name)
            self.assertEqual(status, neterrors.ERR_BAD_BRIDGE, msg)

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

    @permutations([[True], [False]])
    def testFailWithInvalidNic(self, bridged):
        status, msg = self.vdsm_net.addNetwork(NETWORK_NAME,
                                               nics=['nowaythisnicexists'],
                                               opts={'bridged': bridged})

        self.assertEqual(status, neterrors.ERR_BAD_NIC, msg)

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

    @permutations([[True], [False]])
    @RequireDummyMod
    @ValidateRunningAsRoot
    def testDelWithoutAdd(self, bridged):
        with dummyIf(1) as nics:
            status, msg = self.vdsm_net.delNetwork(NETWORK_NAME, nics=nics,
                                                   opts={'bridged': bridged})
            self.assertEqual(status, neterrors.ERR_BAD_BRIDGE, msg)

    @permutations([[True], [False]])
    @RequireDummyMod
    @ValidateRunningAsRoot
    def testEditWithoutAdd(self, bridged):
        with dummyIf(1) as nics:
            status, msg = self.vdsm_net.editNetwork(NETWORK_NAME, NETWORK_NAME,
                                                    nics=nics,
                                                    opts={'bridged': bridged})
            self.assertEqual(status, neterrors.ERR_BAD_BRIDGE, msg)

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
