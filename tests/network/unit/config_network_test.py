# SPDX-FileCopyrightText: 2012 IBM, Inc.
# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import
from __future__ import division

import pytest

from vdsm.network.link.iface import DEFAULT_MTU

from vdsm.network import errors
from vdsm.network.canonicalize import canonicalize_networks
from vdsm.network.netswitch import validator

from .testlib import NetInfo as NetInfoLib

NICS = [f'eth{i}' for i in range(11)]

BOND0 = 'bond00'
BOND0_SLAVES = ['eth5', 'eth6']


class TestConfigNetwork(object):
    def _validate_network_with_err(self, netName, opts, errCode):
        with pytest.raises(errors.ConfigNetworkError) as cneContext:
            canonicalize_networks({netName: opts})
            validator.validate_network_setup({netName: opts}, {}, FAKE_NETINFO)
        assert cneContext.value.errCode == errCode

    def testAddNetworkValidation(self):

        # Test for already existing bridge.
        self._validate_network_with_err(
            'fakebrnet',
            dict(nic='eth2', mtu=DEFAULT_MTU),
            errors.ERR_BAD_PARAMS,
        )

        # Test for non existing nic.
        self._validate_network_with_err(
            'test', dict(nic='eth11', mtu=DEFAULT_MTU), errors.ERR_BAD_PARAMS
        )

        # Test for nic already in a bond.
        self._validate_network_with_err(
            'test', dict(nic='eth6', mtu=DEFAULT_MTU), errors.ERR_BAD_PARAMS
        )

    def testValidateNetSetupRemoveParamValidation(self):
        networks = {
            'test-network': {'nic': 'dummy', 'remove': True, 'bridged': True}
        }
        with pytest.raises(errors.ConfigNetworkError) as cneContext:
            validator.validate_network_setup(networks, {}, FAKE_NETINFO)
        assert cneContext.value.errCode == errors.ERR_BAD_PARAMS


FAKE_NETINFO = NetInfoLib.create(
    networks={
        'fakent': NetInfoLib.create_network(
            iface='fakeint', southbound='fakeint', bridged=False
        ),
        'fakebrnet': NetInfoLib.create_network(
            iface='fakebr', southbound='eth0', bridged=True, ports=['eth0']
        ),
        'fakebrnet1': NetInfoLib.create_network(
            iface='fakebr1', southbound=BOND0, bridged=True, ports=[BOND0]
        ),
        'fakebrnet2': NetInfoLib.create_network(
            iface='fakebr2',
            southbound='eth7.1',
            bridged=True,
            ports=['eth7.1'],
        ),
        'fakebrnet3': NetInfoLib.create_network(
            iface='eth8', southbound='eth8', bridged=False
        ),
    },
    vlans={
        'eth3.2': NetInfoLib.create_vlan(
            iface='eth3',
            vlanid=2,
            addr='10.10.10.10',
            netmask='255.255.0.0',
            mtu=1500,
        ),
        'eth7.1': NetInfoLib.create_vlan(
            iface='eth7',
            vlanid=1,
            addr='192.168.100.1',
            netmask='255.255.255.0',
            mtu=1500,
        ),
    },
    nics=NICS,
    bridges={
        'fakebr': NetInfoLib.create_bridge(ports=['eth0', 'eth1']),
        'fakebr1': NetInfoLib.create_bridge(ports=['bond00']),
        'fakebr2': NetInfoLib.create_bridge(ports=['eth7.1']),
    },
    bondings={BOND0: NetInfoLib.create_bond(slaves=BOND0_SLAVES)},
)
