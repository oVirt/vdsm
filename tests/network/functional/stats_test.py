#
# Copyright 2019 Red Hat, Inc.
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
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA  02110-1301 USA
#
# Refer to the README and COPYING files for full details of the license
#

import pytest

from network.nettestlib import veth_pair

from .netfunctestlib import NetFuncTestAdapter, NOCHK
from .netfunctestlib import parametrize_switch


NETWORK_NAME1 = 'test-network-1'
NETWORK_NAME2 = 'test-network-2'


adapter = None


@pytest.fixture(scope='module', autouse=True)
def create_adapter(target):
    global adapter
    adapter = NetFuncTestAdapter(target)


@pytest.mark.nmstate
@parametrize_switch
def test_interfaces_stats(switch):
    with veth_pair() as (nic1, nic2):
        NETSETUP1 = {
            NETWORK_NAME1: {'bridged': False, 'nic': nic1, 'switch': switch}
        }
        NETSETUP2 = {
            NETWORK_NAME2: {'bridged': False, 'nic': nic2, 'switch': switch}
        }

        with adapter.setupNetworks(NETSETUP1, {}, NOCHK):
            with adapter.setupNetworks(NETSETUP2, {}, NOCHK):
                stats = adapter.getNetworkStatistics()
                netstats = stats.get('network')
                assert netstats
                assert nic1 in netstats
                assert nic2 in netstats
                tx1 = int(netstats[nic1]['tx'])
                tx2 = int(netstats[nic2]['tx'])
                rx1 = int(netstats[nic1]['rx'])
                rx2 = int(netstats[nic2]['rx'])
                assert tx1 >= 0
                assert tx2 >= 0
                assert rx1 >= 0
                assert rx2 >= 0
