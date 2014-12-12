#
# Copyright 2012 IBM, Inc.
# Copyright 2012-2014 Red Hat, Inc.
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

from vdsm import netinfo

from testlib import VdsmTestCase as TestCaseBase
from monkeypatch import MonkeyPatch

from network import api, configurators
from network import errors
from network.models import Bond, Bridge, Nic, Vlan


def _fakeNetworks():
    return {'fakebridgenet': {'iface': 'fakebridge', 'bridged': True},
            'fakenet': {'iface': 'fakeint', 'bridged': False}}


def _raiseInvalidOpException(*args, **kwargs):
    return RuntimeError('Attempted to apply network configuration during unit '
                        'testing.')


class TestConfigNetwork(TestCaseBase):

    def _addNetworkWithExc(self, netName, opts, errCode):
        with self.assertRaises(errors.ConfigNetworkError) as cneContext:
            api._addNetwork(netName, **opts)
        self.assertEqual(cneContext.exception.errCode, errCode)

    # Monkey patch the real network detection from the netinfo module.
    @MonkeyPatch(netinfo, 'networks', _fakeNetworks)
    @MonkeyPatch(netinfo, 'getMaxMtu', lambda *x: 1500)
    @MonkeyPatch(netinfo, 'getMtu', lambda *x: 1500)
    @MonkeyPatch(configurators.ifcfg, 'ifdown', lambda *x:
                 _raiseInvalidOpException())
    @MonkeyPatch(configurators.ifcfg, 'ifup',
                 lambda *x: _raiseInvalidOpException())
    @MonkeyPatch(Bond, 'configure', lambda *x: _raiseInvalidOpException())
    @MonkeyPatch(Bridge, 'configure', lambda *x: _raiseInvalidOpException())
    @MonkeyPatch(Nic, 'configure', lambda *x: _raiseInvalidOpException())
    @MonkeyPatch(Vlan, 'configure', lambda *x: _raiseInvalidOpException())
    def testAddNetworkValidation(self):
        _netinfo = {
            'networks': {
                'fakent': {'iface': 'fakeint', 'bridged': False},
                'fakebrnet': {'iface': 'fakebr', 'bridged': True,
                              'ports': ['eth0', 'eth1']},
                'fakebrnet1': {'iface': 'fakebr1', 'bridged': True,
                               'ports': ['bond00']},
                'fakebrnet2': {'iface': 'fakebr2', 'bridged': True,
                               'ports': ['eth7.1']},
                'fakebrnet3': {'iface': 'eth8', 'bridged': False}
            },
            'vlans': {
                'eth3.2': {'iface': 'eth3',
                           'addr': '10.10.10.10',
                           'netmask': '255.255.0.0',
                           'mtu': 1500
                           },
                'eth7.1': {'iface': 'eth7',
                           'addr': '192.168.100.1',
                           'netmask': '255.255.255.0',
                           'mtu': 1500
                           }
            },
            'nics': ['eth0', 'eth1', 'eth2', 'eth3', 'eth4', 'eth5', 'eth6',
                     'eth7', 'eth8', 'eth9', 'eth10'],
            'bridges': {
                'fakebr': {'ports': ['eth0', 'eth1']},
                'fakebr1': {'ports': ['bond00']},
                'fakebr2': {'ports': ['eth7.1']}
            },
            'bondings': {'bond00': {'slaves': ['eth5', 'eth6']}}
        }

        fakeInfo = netinfo.NetInfo(_netinfo)
        nics = ['eth2']

        # Test for already existing bridge.
        self._addNetworkWithExc('fakebrnet', dict(nics=nics,
                                _netinfo=fakeInfo), errors.ERR_USED_BRIDGE)

        # Test for already existing network.
        self._addNetworkWithExc('fakent', dict(nics=nics, _netinfo=fakeInfo),
                                errors.ERR_USED_BRIDGE)

        # Test for bonding opts passed without bonding specified.
        self._addNetworkWithExc('test', dict(nics=nics,
                                bondingOptions='mode=802.3ad',
                                _netinfo=fakeInfo), errors.ERR_BAD_BONDING)

        # Test IP without netmask.
        self._addNetworkWithExc('test', dict(nics=nics, ipaddr='10.10.10.10',
                                _netinfo=fakeInfo), errors.ERR_BAD_ADDR)

        # Test netmask without IP.
        self._addNetworkWithExc('test', dict(nics=nics,
                                netmask='255.255.255.0', _netinfo=fakeInfo),
                                errors.ERR_BAD_ADDR)

        # Test gateway without IP.
        self._addNetworkWithExc('test', dict(nics=nics, gateway='10.10.0.1',
                                _netinfo=fakeInfo), errors.ERR_BAD_ADDR)

        # Test for non existing nic.
        self._addNetworkWithExc('test', dict(nics=['eth11'],
                                _netinfo=fakeInfo), errors.ERR_BAD_NIC)

        # Test for nic already bound to a different network.
        self._addNetworkWithExc('test', dict(bonding='bond0', nics=['eth0',
                                'eth1'], _netinfo=fakeInfo),
                                errors.ERR_USED_NIC)

        # Test for bond already member of a network.
        self._addNetworkWithExc('test', dict(bonding='bond00', nics=['eth5',
                                'eth6'], _netinfo=fakeInfo),
                                errors.ERR_BAD_PARAMS)

        # Test for multiple nics without bonding device.
        self._addNetworkWithExc('test', dict(nics=['eth3', 'eth4'],
                                _netinfo=fakeInfo), errors.ERR_BAD_BONDING)

        # Test for nic already in a bond.
        self._addNetworkWithExc('test', dict(nics=['eth6'], _netinfo=fakeInfo),
                                errors.ERR_USED_NIC)

        # Test for adding a new non-VLANed bridgeless network when a non-VLANed
        # bridgeless network exists
        self._addNetworkWithExc('test', dict(nics=['eth8'], bridged=False,
                                _netinfo=fakeInfo), errors.ERR_BAD_PARAMS)

    def testBuildBondOptionsBadParams(self):
        class FakeNetInfo(object):
            def __init__(self):
                self.bondings = ['god', 'bless', 'potatoes']
        with self.assertRaises(errors.ConfigNetworkError) as cne:
            api._buildBondOptions('jamesbond', {}, _netinfo=FakeNetInfo())
        self.assertEquals(cne.exception.errCode, errors.ERR_BAD_PARAMS)

    @MonkeyPatch(netinfo, 'NetInfo', lambda: None)
    def testValidateNetSetupRemoveParamValidation(self):
        attrs = dict(nic='dummy', remove=True,
                     bridged=True)
        networks = {'test-netowrk': attrs}
        with self.assertRaises(errors.ConfigNetworkError) as cneContext:
            api._validateNetworkSetup(networks, {})
        self.assertEqual(cneContext.exception.errCode,
                         errors.ERR_BAD_PARAMS)
