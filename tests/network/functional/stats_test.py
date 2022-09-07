# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

import pytest

from network.nettestlib import veth_pair

from .netfunctestlib import NOCHK
from .netfunctestlib import parametrize_switch


NETWORK_NAME1 = 'test-network-1'
NETWORK_NAME2 = 'test-network-2'


@pytest.fixture
def veth_nics():
    with veth_pair() as nics:
        yield nics


@parametrize_switch
def test_interfaces_stats(adapter, switch, veth_nics):
    NETSETUP1 = {
        NETWORK_NAME1: {
            'bridged': False,
            'nic': veth_nics[0],
            'switch': switch,
        }
    }
    NETSETUP2 = {
        NETWORK_NAME2: {
            'bridged': False,
            'nic': veth_nics[1],
            'switch': switch,
        }
    }

    with adapter.setupNetworks(NETSETUP1, {}, NOCHK):
        with adapter.setupNetworks(NETSETUP2, {}, NOCHK):
            stats = adapter.getNetworkStatistics()
            netstats = stats.get('network')
            assert netstats
            for nic in veth_nics:
                assert nic in netstats
                assert int(netstats[nic]['tx']) >= 0
                assert int(netstats[nic]['rx']) >= 0
