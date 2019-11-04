#
# Copyright (C) 2013, IBM Corporation
# Copyright (C) 2013-2019, Red Hat, Inc.
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

import pytest

from vdsm.network.netinfo.cache import CachingNetInfo
from vdsm.network import errors
from vdsm.network.models import Bond, Bridge, IPv4, IPv6, Nic, Vlan
from vdsm.network.models import hierarchy_backing_device, hierarchy_vlan_tag
from vdsm.network.models import _nicSort


class TestNetmodels(object):
    def testIsVlanIdValid(self):
        vlanIds = ('badValue', Vlan.MAX_ID + 1)

        for vlanId in vlanIds:
            with pytest.raises(errors.ConfigNetworkError) as cneContext:
                Vlan.validateTag(vlanId)
            assert cneContext.value.errCode == errors.ERR_BAD_VLAN

        assert Vlan.validateTag(0) is None
        assert Vlan.validateTag(Vlan.MAX_ID) is None

    def testIsNicValid(self):
        invalidNicName = ('toni', 'livnat', 'dan')

        class FakeNetInfo(object):
            def __init__(self):
                self.nics = ['eth0', 'eth1']

        for nic in invalidNicName:
            with pytest.raises(errors.ConfigNetworkError) as cneContext:
                Nic(nic, None, _netinfo=FakeNetInfo())
            assert cneContext.value.errCode == errors.ERR_BAD_NIC

    def testValidateBondingOptions(self):
        opts = 'mode=802.3ad miimon=150'
        badOpts = 'foo=bar badopt=one'

        with pytest.raises(errors.ConfigNetworkError) as cne:
            Bond.validateOptions(badOpts)
        assert cne.value.errCode == errors.ERR_BAD_BONDING
        assert Bond.validateOptions(opts) is None

    def testIsIpValid(self):
        addresses = ('10.18.1.254', '10.50.25.177', '250.0.0.1', '20.20.25.25')
        badAddresses = (
            '192.168.1.256',
            '10.50.25.1777',
            '256.0.0.1',
            '20.20.25.25.25',
        )

        for address in badAddresses:
            with pytest.raises(errors.ConfigNetworkError) as cneContext:
                IPv4.validateAddress(address)
            assert cneContext.value.errCode == errors.ERR_BAD_ADDR

        for address in addresses:
            assert IPv4.validateAddress(address) is None

    def testIsNetmaskValid(self):
        masks = (
            '254.0.0.0',
            '255.255.255.0',
            '255.255.255.128',
            '255.255.255.224',
        )
        badMasks = ('192.168.1.0', '10.50.25.17', '255.0.255.0', '253.0.0.0')

        for mask in badMasks:
            with pytest.raises(errors.ConfigNetworkError) as cneContext:
                IPv4.validateNetmask(mask)
            assert cneContext.value.errCode == errors.ERR_BAD_ADDR

        for mask in masks:
            assert IPv4.validateNetmask(mask) is None

    def testIsIpv6Valid(self):
        addresses = ('::', '::1', 'fe80::83b1:447f:fe2a:3dbd', 'fe80::/16')
        badAddresses = ('::abcd::', 'ff:abcde::1', 'fe80::/132')

        for address in badAddresses:
            with pytest.raises(errors.ConfigNetworkError) as cneContext:
                IPv6.validateAddress(address)
            assert cneContext.value.errCode == errors.ERR_BAD_ADDR

        for address in addresses:
            assert IPv6.validateAddress(address) is None

    def testTextualRepr(self):
        _netinfo = {
            'networks': {},
            'vlans': {},
            'nics': ['testnic1', 'testnic2'],
            'bondings': {},
            'bridges': {},
            'nameservers': [],
        }
        fakeInfo = CachingNetInfo(_netinfo)
        nic1 = Nic('testnic1', None, _netinfo=fakeInfo)
        nic2 = Nic('testnic2', None, _netinfo=fakeInfo)
        bond1 = Bond('bond42', None, slaves=(nic1, nic2))
        vlan1 = Vlan(bond1, '4', None)
        bridge1 = Bridge('testbridge', None, port=vlan1)
        expected = (
            'Bridge(testbridge: Vlan(bond42.4: '
            'Bond(bond42: (Nic(testnic1), Nic(testnic2)))))'
        )
        assert '%r' % bridge1 == expected

    def testNicSort(self):
        nics = {
            'nics_init': (
                'p33p1',
                'eth1',
                'lan0',
                'em0',
                'p331',
                'Lan1',
                'eth0',
                'em1',
                'p33p2',
                'p33p10',
            ),
            'nics_expected': (
                'Lan1',
                'em0',
                'em1',
                'eth0',
                'eth1',
                'lan0',
                'p33p1',
                'p33p10',
                'p33p2',
                'p331',
            ),
        }

        nics_res = _nicSort(nics['nics_init'])
        assert nics['nics_expected'] == tuple(nics_res)

    def testBondReorderOptions(self):
        empty = Bond._reorderOptions('')
        assert empty == ''

        modeless = Bond._reorderOptions('miimon=250')
        assert modeless == 'miimon=250'

        ordered = Bond._reorderOptions('mode=4 miimon=250')
        assert ordered == 'mode=4 miimon=250'

        inverted = Bond._reorderOptions('miimon=250 mode=4')
        assert inverted == 'mode=4 miimon=250'

    def testIterNetworkHierarchy(self):
        _netinfo = {
            'networks': {},
            'vlans': {},
            'nics': ['testnic1', 'testnic2'],
            'bondings': {},
            'bridges': {},
            'nameservers': [],
        }
        fakeInfo = CachingNetInfo(_netinfo)
        # Vlanned and bonded VM network
        nic1 = Nic('testnic1', configurator=None, _netinfo=fakeInfo)
        nic2 = Nic('testnic2', configurator=None, _netinfo=fakeInfo)
        bond1 = Bond('bond42', configurator=None, slaves=(nic1, nic2))
        vlan1 = Vlan(bond1, 4, configurator=None)
        bridge1 = Bridge('testbridge', configurator=None, port=vlan1)

        assert [dev for dev in bridge1] == [bridge1, vlan1, bond1, nic1, nic2]
        assert bond1 == hierarchy_backing_device(bridge1)
        assert 4 == hierarchy_vlan_tag(bridge1)

        # Nic-less VM net
        bridge2 = Bridge('testbridge', configurator=None, port=None)
        assert [dev for dev in bridge2] == [bridge2]
        assert hierarchy_backing_device(bridge2) is None
        assert hierarchy_vlan_tag(bridge2) is None

        # vlan-less VM net
        bridge3 = Bridge('testbridge', configurator=None, port=bond1)
        assert [dev for dev in bridge3] == [bridge3, bond1, nic1, nic2]
        assert bond1 == hierarchy_backing_device(bridge3)
        assert hierarchy_vlan_tag(bridge3) is None

        # Bond-less VM net
        bridge4 = Bridge('testbridge', configurator=None, port=nic1)
        assert [dev for dev in bridge4] == [bridge4, nic1]
        assert nic1 == hierarchy_backing_device(bridge4)
        assert hierarchy_vlan_tag(bridge4) is None

        # vlanned and bonded non-VM net
        assert [dev for dev in vlan1] == [vlan1, bond1, nic1, nic2]
        assert bond1 == hierarchy_backing_device(vlan1)
        assert 4 == hierarchy_vlan_tag(vlan1)

        # vlanned, bond-less non-VM net
        vlan2 = Vlan(nic1, 5, configurator=None)
        assert [dev for dev in vlan2] == [vlan2, nic1]
        assert nic1 == hierarchy_backing_device(vlan2)
        assert 5 == hierarchy_vlan_tag(vlan2)

        # non-vlanned and bonded non-VM net
        assert [dev for dev in bond1] == [bond1, nic1, nic2]
        assert bond1 == hierarchy_backing_device(bond1)
        assert hierarchy_vlan_tag(bond1) is None

        # non-vlanned and bond-less non-VM net
        assert [dev for dev in nic2] == [nic2]
        assert nic2 == hierarchy_backing_device(nic2)
        assert hierarchy_vlan_tag(nic2) is None
