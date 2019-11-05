#
# Copyright 2012 IBM, Inc.
# Copyright 2012-2019 Red Hat, Inc.
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

from __future__ import absolute_import
from __future__ import division

from vdsm.network import netinfo
from vdsm.network.link.iface import DEFAULT_MTU

from testlib import VdsmTestCase as TestCaseBase
from network.compat import mock

from vdsm.network import errors
from vdsm.network.configurators import ifcfg
from vdsm.network.canonicalize import canonicalize_networks
from vdsm.network import legacy_switch
from vdsm.network.models import Bond, Bridge, Nic, Vlan
from vdsm.network.netswitch import validator


def _raiseInvalidOpException(*args, **kwargs):
    return RuntimeError(
        'Attempted to apply network configuration during unit ' 'testing.'
    )


class TestConfigNetwork(TestCaseBase):
    def _addNetworkWithExc(self, netName, opts, errCode):
        fakeInfo = netinfo.cache.CachingNetInfo(FAKE_NETINFO)
        configurator = ifcfg.Ifcfg(fakeInfo)

        with self.assertRaises(errors.ConfigNetworkError) as cneContext:
            canonicalize_networks({netName: opts})
            legacy_switch._add_network(
                netName, configurator, fakeInfo, None, **opts
            )
        self.assertEqual(cneContext.exception.errCode, errCode)

    # Monkey patch the real network detection from the netinfo module.
    @mock.patch.object(ifcfg, 'ifdown', _raiseInvalidOpException)
    @mock.patch.object(ifcfg, '_exec_ifup', _raiseInvalidOpException)
    @mock.patch.object(Bond, 'configure', _raiseInvalidOpException)
    @mock.patch.object(Bridge, 'configure', _raiseInvalidOpException)
    @mock.patch.object(Nic, 'configure', _raiseInvalidOpException)
    @mock.patch.object(Vlan, 'configure', _raiseInvalidOpException)
    def testAddNetworkValidation(self):

        # Test for already existing bridge.
        self._addNetworkWithExc(
            'fakebrnet',
            dict(nic='eth2', mtu=DEFAULT_MTU),
            errors.ERR_USED_BRIDGE,
        )

        # Test for already existing network.
        self._addNetworkWithExc(
            'fakent', dict(nic='eth2', mtu=DEFAULT_MTU), errors.ERR_USED_BRIDGE
        )

        # Test for non existing nic.
        self._addNetworkWithExc(
            'test', dict(nic='eth11', mtu=DEFAULT_MTU), errors.ERR_BAD_NIC
        )

        # Test for nic already in a bond.
        self._addNetworkWithExc(
            'test', dict(nic='eth6', mtu=DEFAULT_MTU), errors.ERR_USED_NIC
        )

    @mock.patch.object(validator, 'nics', lambda: [])
    @mock.patch.object(validator, 'Bond')
    def testValidateNetSetupRemoveParamValidation(self, bond_mock):
        bond_mock.return_value.bonds.return_value = []
        networks = {
            'test-network': {'nic': 'dummy', 'remove': True, 'bridged': True}
        }
        with self.assertRaises(errors.ConfigNetworkError) as cneContext:
            validator.validate_network_setup(networks, {}, {'networks': {}})
        self.assertEqual(cneContext.exception.errCode, errors.ERR_BAD_PARAMS)


FAKE_NETINFO = {
    'networks': {
        'fakent': {'iface': 'fakeint', 'bridged': False},
        'fakebrnet': {
            'iface': 'fakebr',
            'bridged': True,
            'ports': ['eth0', 'eth1'],
        },
        'fakebrnet1': {
            'iface': 'fakebr1',
            'bridged': True,
            'ports': ['bond00'],
        },
        'fakebrnet2': {
            'iface': 'fakebr2',
            'bridged': True,
            'ports': ['eth7.1'],
        },
        'fakebrnet3': {'iface': 'eth8', 'bridged': False},
    },
    'vlans': {
        'eth3.2': {
            'iface': 'eth3',
            'addr': '10.10.10.10',
            'netmask': '255.255.0.0',
            'mtu': 1500,
        },
        'eth7.1': {
            'iface': 'eth7',
            'addr': '192.168.100.1',
            'netmask': '255.255.255.0',
            'mtu': 1500,
        },
    },
    'nics': [
        'eth0',
        'eth1',
        'eth2',
        'eth3',
        'eth4',
        'eth5',
        'eth6',
        'eth7',
        'eth8',
        'eth9',
        'eth10',
    ],
    'bridges': {
        'fakebr': {'ports': ['eth0', 'eth1']},
        'fakebr1': {'ports': ['bond00']},
        'fakebr2': {'ports': ['eth7.1']},
    },
    'bondings': {'bond00': {'slaves': ['eth5', 'eth6']}},
    'nameservers': [],
}
