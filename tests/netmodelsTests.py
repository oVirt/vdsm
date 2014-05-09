#
# Copyright (C) 2013, IBM Corporation
# Copyright (C) 2013-2014, Red Hat, Inc.
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
import os

from vdsm import netinfo

from network import errors
from network.models import Bond, Bridge, IPv4, Nic, Vlan
from network.models import _nicSort

from testrunner import VdsmTestCase as TestCaseBase
from testValidation import ValidateRunningAsRoot
from nose.plugins.skip import SkipTest

from monkeypatch import MonkeyPatch


class TestNetmodels(TestCaseBase):

    def testIsVlanIdValid(self):
        vlanIds = ('badValue', Vlan.MAX_ID + 1)

        for vlanId in vlanIds:
            with self.assertRaises(errors.ConfigNetworkError) as cneContext:
                Vlan.validateTag(vlanId)
            self.assertEqual(cneContext.exception.errCode,
                             errors.ERR_BAD_VLAN)

        self.assertEqual(Vlan.validateTag(0), None)
        self.assertEqual(Vlan.validateTag(Vlan.MAX_ID), None)

    def testIsBridgeNameValid(self):
        invalidBrName = ('', '-abc', 'abcdefghijklmnop', 'a:b', 'a.b')
        for i in invalidBrName:
            with self.assertRaises(errors.ConfigNetworkError) as cneContext:
                Bridge.validateName(i)
            self.assertEqual(cneContext.exception.errCode,
                             errors.ERR_BAD_BRIDGE)

    def testIsNicValid(self):
        invalidNicName = ('toni', 'livnat', 'dan')

        class FakeNetInfo(object):
            def __init__(self):
                self.nics = ['eth0', 'eth1']

        for nic in invalidNicName:
            with self.assertRaises(errors.ConfigNetworkError) as cneContext:
                Nic(nic, None, _netinfo=FakeNetInfo())
            self.assertEqual(cneContext.exception.errCode, errors.ERR_BAD_NIC)

    def testIsBondingNameValid(self):
        bondNames = ('badValue', ' bond14', 'bond14 ', 'bond14a', 'bond0 0')

        for bondName in bondNames:
            with self.assertRaises(errors.ConfigNetworkError) as cneContext:
                Bond.validateName(bondName)
            self.assertEqual(cneContext.exception.errCode,
                             errors.ERR_BAD_BONDING)

        self.assertEqual(Bond.validateName('bond11'), None)
        self.assertEqual(Bond.validateName('bond11128421982'), None)

    @ValidateRunningAsRoot
    def testValidateBondingOptions(self):
        if not os.path.exists(netinfo.BONDING_MASTERS):
            raise SkipTest("bonding kernel module could not be found.")

        opts = 'mode=802.3ad miimon=150'
        badOpts = 'foo=bar badopt=one'

        with self.assertRaises(errors.ConfigNetworkError) as cne:
            Bond.validateOptions('bond0', badOpts)
        self.assertEqual(cne.exception.errCode, errors.ERR_BAD_BONDING)
        self.assertEqual(Bond.validateOptions('bond0', opts), None)

    def testIsIpValid(self):
        addresses = ('10.18.1.254', '10.50.25.177', '250.0.0.1',
                     '20.20.25.25')
        badAddresses = ('192.168.1.256', '10.50.25.1777', '256.0.0.1',
                        '20.20.25.25.25')

        for address in badAddresses:
            with self.assertRaises(errors.ConfigNetworkError) as cneContext:
                IPv4.validateAddress(address)
            self.assertEqual(cneContext.exception.errCode, errors.ERR_BAD_ADDR)

        for address in addresses:
            self.assertEqual(IPv4.validateAddress(address), None)

    def testIsNetmaskValid(self):
        masks = ('254.0.0.0', '255.255.255.0', '255.255.255.128',
                 '255.255.255.224')
        badMasks = ('192.168.1.0', '10.50.25.17', '255.0.255.0',
                    '253.0.0.0')

        for mask in badMasks:
            with self.assertRaises(errors.ConfigNetworkError) as cneContext:
                IPv4.validateNetmask(mask)
            self.assertEqual(cneContext.exception.errCode, errors.ERR_BAD_ADDR)

        for mask in masks:
            self.assertEqual(IPv4.validateNetmask(mask), None)

    @MonkeyPatch(netinfo, 'getMtu', lambda *x: 1500)
    @MonkeyPatch(Bond, 'validateOptions', lambda *x: 0)
    def testTextualRepr(self):
        _netinfo = {'networks': {}, 'vlans': {},
                    'nics': ['testnic1', 'testnic2'],
                    'bondings': {}, 'bridges': {}}
        fakeInfo = netinfo.NetInfo(_netinfo)
        nic1 = Nic('testnic1', None, _netinfo=fakeInfo)
        nic2 = Nic('testnic2', None, _netinfo=fakeInfo)
        bond1 = Bond('bond42', None, slaves=(nic1, nic2))
        vlan1 = Vlan(bond1, '4', None)
        bridge1 = Bridge('testbridge', None, port=vlan1)
        self.assertEqual('%r' % bridge1, 'Bridge(testbridge: Vlan(bond42.4: '
                         'Bond(bond42: (Nic(testnic1), Nic(testnic2)))))')

    def testNicSort(self):
        nics = {'nics_init': ('p33p1', 'eth1', 'lan0', 'em0', 'p331',
                              'Lan1', 'eth0', 'em1', 'p33p2', 'p33p10'),
                'nics_expected': ('Lan1', 'em0', 'em1', 'eth0', 'eth1',
                                  'lan0', 'p33p1', 'p33p10', 'p33p2', 'p331')}

        nics_res = _nicSort(nics['nics_init'])
        self.assertEqual(nics['nics_expected'], tuple(nics_res))

    def testBondReorderOptions(self):
        empty = Bond._reorderOptions('')
        self.assertEqual(empty, '')

        modeless = Bond._reorderOptions('miimon=250')
        self.assertEqual(modeless, 'miimon=250')

        ordered = Bond._reorderOptions('mode=4 miimon=250')
        self.assertEqual(ordered, 'mode=4 miimon=250')

        inverted = Bond._reorderOptions('miimon=250 mode=4')
        self.assertEqual(inverted, 'mode=4 miimon=250')
