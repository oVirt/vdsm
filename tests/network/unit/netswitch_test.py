# Copyright 2016-2020 Red Hat, Inc.
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

import pytest

from vdsm.network import errors
from vdsm.network import netswitch

from .testlib import NetInfo as NetInfoLib


BOND_NAME = 'bond1'
NETWORK1_NAME = 'test-network1'


class TestSouthboundValidation(object):
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
        validator = netswitch.validator.Validator(
            NETSETUP, {}, _create_fake_netinfo(switch)
        )
        validator.validate_southbound_devices_usages()

    def _assert_net_setup_fails_bad_params(
        self, net_name, switch, sb_device, vlan=None
    ):
        bridged = False
        net_setup = {net_name: {'switch': switch, 'bridged': bridged}}
        net_setup[net_name].update(sb_device)
        if vlan is not None:
            net_setup[net_name]['vlan'] = vlan

            with pytest.raises(errors.ConfigNetworkError) as cne_context:
                validator = netswitch.validator.Validator(
                    net_setup, {}, _create_fake_netinfo(switch)
                )
                validator.validate_southbound_devices_usages()
            assert cne_context.value.errCode == errors.ERR_BAD_PARAMS


def _create_fake_netinfo(switch):
    fake_netinfo = NetInfoLib.create(
        networks={
            'fakebrnet1': NetInfoLib.create_network(
                iface='eth0', bridged=False, southbound='eth0', switch=switch
            ),
            'fakevlannet1': NetInfoLib.create_network(
                iface='eth1.1',
                bridged=False,
                southbound='eth1.1',
                vlanid=1,
                switch=switch,
            ),
            'fakebondnet1': NetInfoLib.create_network(
                iface='bond0', bridged=False, southbound='bond0', switch=switch
            ),
        },
        vlans={
            'eth1.1': NetInfoLib.create_vlan(
                iface='eth1',
                addr='10.10.10.10',
                netmask='255.255.0.0',
                mtu=1500,
                vlanid=1,
            )
        },
        nics=['eth0', 'eth1', 'eth2', 'eth3'],
        bondings={
            'bond0': NetInfoLib.create_bond(
                slaves=['eth2', 'eth3'], switch=switch
            )
        },
    )
    return fake_netinfo
