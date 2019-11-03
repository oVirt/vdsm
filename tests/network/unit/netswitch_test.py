# Copyright 2016-2019 Red Hat, Inc.
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
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA
#
# Refer to the README and COPYING files for full details of the license
#

from __future__ import absolute_import
from __future__ import division

from vdsm.network import errors
from vdsm.network import netswitch
from vdsm.network.netinfo.cache import NetInfo

from testlib import VdsmTestCase as TestCaseBase


BOND_NAME = 'bond1'
NETWORK1_NAME = 'test-network1'


class TestSplitSetupActions(TestCaseBase):
    def test_split_nets(self):
        net_query = {
            'net2add': {'nic': 'eth0'},
            'net2edit': {'nic': 'eth1'},
            'net2remove': {'remove': True},
        }
        running_nets = {'net2edit': {'foo': 'bar'}}

        nets = netswitch.configurator._split_setup_actions(
            net_query, running_nets
        )
        nets2add, nets2edit, nets2remove = nets

        self.assertEqual(nets2add, {'net2add': {'nic': 'eth0'}})
        self.assertEqual(nets2edit, {'net2edit': {'nic': 'eth1'}})
        self.assertEqual(nets2remove, {'net2remove': {'remove': True}})

    def test_split_bonds(self):
        bond_query = {
            'bond2add': {'nics': ['eth0', 'eth1']},
            'bond2edit': {'nics': ['eth2', 'eth3']},
            'bond2remove': {'remove': True},
        }
        running_bonds = {'bond2edit': {'foo': 'bar'}}

        nets = netswitch.configurator._split_setup_actions(
            bond_query, running_bonds
        )
        bonds2add, bonds2edit, bonds2remove = nets

        self.assertEqual(bonds2add, {'bond2add': {'nics': ['eth0', 'eth1']}})
        self.assertEqual(bonds2edit, {'bond2edit': {'nics': ['eth2', 'eth3']}})
        self.assertEqual(bonds2remove, {'bond2remove': {'remove': True}})


class TestSouthboundValidation(TestCaseBase):
    def test_two_bridgless_ovs_nets_with_used_nic_fails(self):
        self._assert_net_setup_fails_bad_params(
            'fakebrnet2', 'ovs', {'nic': 'eth0'}
        )

    def test_two_bridgless_legacy_nets_with_used_nic_fails(self):
        self._assert_net_setup_fails_bad_params(
            'fakebrnet2', 'legacy', {'nic': 'eth0'}
        )

    def test_two_ovs_nets_with_used_bond_fails(self):
        self._assert_net_setup_fails_bad_params(
            'fakevlannet2', 'ovs', {'nic': 'eth1'}, vlan=1
        )

    def test_two_legacy_nets_with_used_bond_fails(self):
        self._assert_net_setup_fails_bad_params(
            'fakevlannet2', 'legacy', {'nic': 'eth1'}, vlan=1
        )

    def test_two_ovs_nets_with_same_vlans_fails(self):
        self._assert_net_setup_fails_bad_params(
            'fakebondnet2', 'ovs', {'bonding': 'bond0'}
        )

    def test_two_legacy_nets_with_same_vlans_fails(self):
        self._assert_net_setup_fails_bad_params(
            'fakebondnet2', 'legacy', {'bonding': 'bond0'}
        )

    def test_replacing_ovs_net_on_nic(self):
        self._test_replacing_net_on_nic('ovs')

    def test_replacing_legacy_net_on_nic(self):
        self._test_replacing_net_on_nic('legacy')

    def _test_replacing_net_on_nic(self, switch):
        NETSETUP = {
            'fakebrnet2': {'nic': 'eth0', 'switch': switch},
            'fakebrnet1': {'remove': True},
        }

        netswitch.validator.validate_southbound_devices_usages(
            NETSETUP, _create_fake_netinfo(switch)
        )

    def _assert_net_setup_fails_bad_params(
        self, net_name, switch, sb_device, vlan=None
    ):
        bridged = False
        net_setup = {net_name: {'switch': switch, 'bridged': bridged}}
        net_setup[net_name].update(sb_device)
        if vlan is not None:
            net_setup[net_name]['vlan'] = vlan

            with self.assertRaises(errors.ConfigNetworkError) as cne_context:
                netswitch.validator.validate_southbound_devices_usages(
                    net_setup, _create_fake_netinfo(switch)
                )
            self.assertEqual(
                cne_context.exception.errCode, errors.ERR_BAD_PARAMS
            )


def _create_fake_netinfo(switch):
    common_net_attrs = {
        'ipv6addrs': [],
        'gateway': '',
        'dhcpv4': False,
        'netmask': '',
        'ipv4defaultroute': False,
        'stp': 'off',
        'ipv4addrs': [],
        'mtu': 1500,
        'ipv6gateway': '::',
        'dhcpv6': False,
        'ipv6autoconf': False,
        'addr': '',
        'ports': [],
        'switch': switch,
    }

    common_bond_attrs = {'opts': {'mode': '0'}, 'switch': switch}

    fake_netinfo = {
        'networks': {
            'fakebrnet1': dict(
                iface='eth0',
                bridged=False,
                southbound='eth0',
                **common_net_attrs
            ),
            'fakevlannet1': dict(
                iface='eth1.1',
                bridged=False,
                southbound='eth1',
                vlanid=1,
                **common_net_attrs
            ),
            'fakebondnet1': dict(
                iface='bond0',
                bridged=False,
                southbound='bond0',
                **common_net_attrs
            ),
        },
        'vlans': {
            'eth1.1': {
                'iface': 'eth1',
                'addr': '10.10.10.10',
                'netmask': '255.255.0.0',
                'mtu': 1500,
                'vlanid': 1,
            }
        },
        'nics': ['eth0', 'eth1', 'eth2', 'eth3'],
        'bridges': {},
        'bondings': {
            'bond0': dict(slaves=['eth2', 'eth3'], **common_bond_attrs)
        },
        'nameservers': [],
    }
    return NetInfo(fake_netinfo)
