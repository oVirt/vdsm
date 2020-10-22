#
# Copyright 2012-2020 Red Hat, Inc.
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
import os
import io

import pytest
import six

from vdsm.network import ipwrapper
from vdsm.network.ip.address import prefix2netmask
from vdsm.network.link import nic
from vdsm.network.link.bond import Bond, bond_speed
from vdsm.network.netinfo import addresses, bonding, misc, nics, routes
from vdsm.network.netinfo.cache import get

from vdsm.network import nmstate

from network.compat import mock
from network.nettestlib import running_on_ovirt_ci
from network.nettestlib import running_on_travis_ci


@pytest.fixture
def current_state_mock():
    with mock.patch.object(nmstate, 'state_show') as state:
        state.return_value = {nmstate.Interface.KEY: [], nmstate.DNS.KEY: {}}
        yield state.return_value


class TestNetinfo(object):
    def test_netmask_conversions(self):
        path = os.path.join(os.path.dirname(__file__), "netmaskconversions")
        with open(path) as netmaskFile:
            for line in netmaskFile:
                if line.startswith('#'):
                    continue
                bitmask, address = [value.strip() for value in line.split()]
                assert prefix2netmask(int(bitmask)) == address
        pytest.raises(ValueError, prefix2netmask, -1)
        pytest.raises(ValueError, prefix2netmask, 33)

    @mock.patch.object(nic, 'speed')
    @mock.patch.object(bond_speed, 'properties')
    def test_bond_speed(self, mock_properties, speed_mock):
        values = (
            ('bond1', [1000], 1000),
            ('bond2', [1000, 2000], 3000),
            ('bond3', [1000, 2000], 1000),
            ('bond4', [1000, 1000], 0),
            ('bond5', [1000, 2000], 0),
        )
        bonds_opts = {
            'bond1': {
                'mode': ['active-backup', '1'],
                'slaves': ('dummy1', 'dummy2'),
                'active_slave': 'dummy1',
            },
            'bond2': {
                'mode': ['balance-xor', '2'],
                'slaves': ('dummy1', 'dummy2'),
            },
            'bond3': {
                'mode': ['broadcast', '3'],
                'slaves': ('dummy1', 'dummy2'),
            },
            'bond4': {'mode': ['802.3ad', '4']},
            'bond5': {
                'mode': ['active-backup', '1'],
                'slaves': ('dummy1', 'dummy2'),
            },
        }

        for bond_name, nics_speeds, expected_speed in values:
            mock_properties.return_value = bonds_opts[bond_name]
            speed_mock.side_effect = nics_speeds

            assert bond_speed.speed(bond_name) == expected_speed

    @mock.patch.object(nic, 'iface')
    @mock.patch.object(nics.io, 'open')
    def test_valid_nic_speed(self, mock_io_open, mock_iface):
        IS_UP = True
        values = (
            (b'0', IS_UP, 0),
            (b'-10', IS_UP, 0),
            (six.b(str(2 ** 16 - 1)), IS_UP, 0),
            (six.b(str(2 ** 32 - 1)), IS_UP, 0),
            (b'123', IS_UP, 123),
            (b'', IS_UP, 0),
            (b'', not IS_UP, 0),
            (b'123', not IS_UP, 0),
        )

        for passed, is_nic_up, expected in values:
            mock_io_open.return_value = io.BytesIO(passed)
            mock_iface.return_value.is_oper_up.return_value = is_nic_up

            assert nic.speed('fake_nic') == expected

    def test_dpdk_device_speed(self):
        assert nic.speed('dpdk0') == 0

    def test_dpdk_operstate_always_up(self):
        assert nics.operstate('dpdk0') == nics.OPERSTATE_UP

    @pytest.mark.xfail(
        condition=running_on_ovirt_ci() or running_on_travis_ci(),
        raises=KeyError,
        reason='does not work on CI with nmstate',
        strict=False,
    )
    @mock.patch.object(bonding, 'permanent_address', lambda: {})
    @mock.patch('vdsm.network.netinfo.cache.RunningConfig')
    def test_get_non_existing_bridge_info(
        self, mock_runningconfig, current_state_mock
    ):
        # Getting info of non existing bridge should not raise an exception,
        # just log a traceback. If it raises an exception the test will fail as
        # it should.
        mock_runningconfig.return_value.networks = {'fake': {'bridged': True}}
        get()

    @mock.patch.object(bonding, 'permanent_address', lambda: {})
    @mock.patch('vdsm.network.netinfo.cache.getLinks')
    @mock.patch('vdsm.network.netinfo.cache.RunningConfig')
    def test_get_empty(self, mock_networks, mock_getLinks, current_state_mock):
        result = {}
        result.update(get())
        assert result['networks'] == {}
        assert result['bridges'] == {}
        assert result['nics'] == {}
        assert result['bondings'] == {}
        assert result['vlans'] == {}

    def test_ipv4_to_mapped(self):
        assert '::ffff:127.0.0.1' == addresses.IPv4toMapped('127.0.0.1')

    def test_get_device_by_ip(self):
        NL_ADDRESS4 = {
            'label': 'iface0',
            'address': '127.0.0.1/32',
            'family': 'inet',
        }
        NL_ADDRESS6 = {
            'label': 'iface1',
            'address': '2001::1:1:1/48',
            'family': 'inet6',
        }
        NL_ADDRESSES = [NL_ADDRESS4, NL_ADDRESS6]

        with mock.patch.object(
            addresses.nl_addr, 'iter_addrs', lambda: NL_ADDRESSES
        ):
            for nl_addr in NL_ADDRESSES:
                lbl = addresses.getDeviceByIP(nl_addr['address'].split('/')[0])
                assert nl_addr['label'] == lbl

    @mock.patch.object(ipwrapper.Link, '_hiddenNics', ['hid*'])
    @mock.patch.object(ipwrapper.Link, '_hiddenBonds', ['jb*'])
    @mock.patch.object(ipwrapper.Link, '_fakeNics', ['fake*'])
    @mock.patch.object(ipwrapper.Link, '_detectType', lambda x: None)
    @mock.patch.object(ipwrapper, '_bondExists', lambda x: x == 'jbond')
    @mock.patch.object(misc, 'getLinks')
    def test_nics(self, mock_getLinks):
        """
        managed by vdsm: em, me, fake0, fake1
        not managed due to hidden bond (jbond) enslavement: me0, me1
        not managed due to being hidden nics: hid0, hideous
        """
        mock_getLinks.return_value = self._LINKS_REPORT

        assert set(nics.nics()) == set(['em', 'me', 'fake', 'fake0'])

    # Creates a test fixture so that nics() reports:
    # physical nics: em, me, me0, me1, hid0 and hideous
    # dummies: fake and fake0
    # bonds: jbond (over me0 and me1)
    _LINKS_REPORT = [
        ipwrapper.Link(
            address='f0:de:f1:da:aa:e7',
            index=2,
            linkType=ipwrapper.LinkType.NIC,
            mtu=1500,
            name='em',
            qdisc='pfifo_fast',
            state='up',
        ),
        ipwrapper.Link(
            address='ff:de:f1:da:aa:e7',
            index=3,
            linkType=ipwrapper.LinkType.NIC,
            mtu=1500,
            name='me',
            qdisc='pfifo_fast',
            state='up',
        ),
        ipwrapper.Link(
            address='ff:de:fa:da:aa:e7',
            index=4,
            linkType=ipwrapper.LinkType.NIC,
            mtu=1500,
            name='hid0',
            qdisc='pfifo_fast',
            state='up',
        ),
        ipwrapper.Link(
            address='ff:de:11:da:aa:e7',
            index=5,
            linkType=ipwrapper.LinkType.NIC,
            mtu=1500,
            name='hideous',
            qdisc='pfifo_fast',
            state='up',
        ),
        ipwrapper.Link(
            address='66:de:f1:da:aa:e7',
            index=6,
            linkType=ipwrapper.LinkType.NIC,
            mtu=1500,
            name='me0',
            qdisc='pfifo_fast',
            state='up',
            master='jbond',
        ),
        ipwrapper.Link(
            address='66:de:f1:da:aa:e7',
            index=7,
            linkType=ipwrapper.LinkType.NIC,
            mtu=1500,
            name='me1',
            qdisc='pfifo_fast',
            state='up',
            master='jbond',
        ),
        ipwrapper.Link(
            address='ff:aa:f1:da:aa:e7',
            index=34,
            linkType=ipwrapper.LinkType.DUMMY,
            mtu=1500,
            name='fake0',
            qdisc='pfifo_fast',
            state='up',
        ),
        ipwrapper.Link(
            address='ff:aa:f1:da:bb:e7',
            index=35,
            linkType=ipwrapper.LinkType.DUMMY,
            mtu=1500,
            name='fake',
            qdisc='pfifo_fast',
            state='up',
        ),
        ipwrapper.Link(
            address='66:de:f1:da:aa:e7',
            index=419,
            linkType=ipwrapper.LinkType.BOND,
            mtu=1500,
            name='jbond',
            qdisc='pfifo_fast',
            state='up',
        ),
    ]

    @mock.patch.object(misc, 'open', create=True)
    def test_get_ifcfg(self, mock_open):
        gateway = '1.1.1.1'
        netmask = '255.255.0.0'

        ifcfg = "GATEWAY0={}\nNETMASK={}\n".format(gateway, netmask)
        ifcfg_stream = six.StringIO(ifcfg)
        mock_open.return_value.__enter__.return_value = ifcfg_stream

        resulted_ifcfg = misc.getIfaceCfg('eth0')

        assert resulted_ifcfg['GATEWAY'] == gateway
        assert resulted_ifcfg['NETMASK'] == netmask

    @mock.patch.object(misc, 'open', create=True)
    def test_missing_ifcfg_file(self, mock_open):
        mock_open.return_value.__enter__.side_effect = IOError()

        ifcfg = misc.getIfaceCfg('eth0')

        assert ifcfg == {}

    @staticmethod
    def _bond_opts_without_mode(bond_name):
        opts = Bond(bond_name).options
        opts.pop('mode')
        return opts

    def test_get_gateway(self):
        TEST_IFACE = 'test_iface'
        # different tables but the gateway is the same so it should be reported
        DUPLICATED_GATEWAY = {
            TEST_IFACE: [
                {
                    'destination': 'none',
                    'family': 'inet',
                    'gateway': '12.34.56.1',
                    'oif': TEST_IFACE,
                    'oif_index': 8,
                    'scope': 'global',
                    'source': None,
                    'table': 203569230,  # we got the address 12.34.56.78
                },
                {
                    'destination': 'none',
                    'family': 'inet',
                    'gateway': '12.34.56.1',
                    'oif': TEST_IFACE,
                    'oif_index': 8,
                    'scope': 'global',
                    'source': None,
                    'table': 254,
                },
            ]
        }
        SINGLE_GATEWAY = {TEST_IFACE: [DUPLICATED_GATEWAY[TEST_IFACE][0]]}

        gateway = routes.get_gateway(SINGLE_GATEWAY, TEST_IFACE)
        assert gateway == '12.34.56.1'
        gateway = routes.get_gateway(DUPLICATED_GATEWAY, TEST_IFACE)
        assert gateway == '12.34.56.1'

    def test_netinfo_ignoring_link_scope_ip(self):
        v4_link = {
            'family': 'inet',
            'address': '169.254.0.0/16',
            'scope': 'link',
            'prefixlen': 16,
            'flags': ['permanent'],
        }
        v4_global = {
            'family': 'inet',
            'address': '192.0.2.2/24',
            'scope': 'global',
            'prefixlen': 24,
            'flags': ['permanent'],
        }
        v6_link = {
            'family': 'inet6',
            'address': 'fe80::5054:ff:fea3:f9f3/64',
            'scope': 'link',
            'prefixlen': 64,
            'flags': ['permanent'],
        }
        v6_global = {
            'family': 'inet6',
            'address': 'ee80::5054:ff:fea3:f9f3/64',
            'scope': 'global',
            'prefixlen': 64,
            'flags': ['permanent'],
        }
        ipaddrs = {'eth0': (v4_link, v4_global, v6_link, v6_global)}
        ipv4addr, ipv4netmask, ipv4addrs, ipv6addrs = addresses.getIpInfo(
            'eth0', ipaddrs=ipaddrs
        )
        assert ipv4addrs == ['192.0.2.2/24']
        assert ipv6addrs == ['ee80::5054:ff:fea3:f9f3/64']

    def test_parse_bond_options(self):
        expected = {'mode': '4', 'miimon': '100'}
        assert expected == bonding.parse_bond_options('mode=4 miimon=100')
