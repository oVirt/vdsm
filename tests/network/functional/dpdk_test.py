# Copyright 2017-2020 Red Hat, Inc.
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

import pytest

from .netfunctestlib import NetFuncTestAdapter, NOCHK


NETWORK_NAME = 'test-network'


adapter = None


@pytest.fixture(scope='module', autouse=True)
def create_adapter():
    global adapter
    adapter = NetFuncTestAdapter()


@pytest.mark.ovsdpdk_switch
class TestOvsDpdk(object):
    def test_dpdk0_device_exists(self):
        adapter.update_netinfo()
        assert 'dpdk0' in adapter.netinfo.nics

    def test_setup_ovs_dpdk(self):
        NETCREATE = {NETWORK_NAME: {'nic': 'dpdk0', 'switch': 'ovs'}}
        with adapter.setupNetworks(NETCREATE, {}, NOCHK):
            adapter.assertNetwork(NETWORK_NAME, NETCREATE[NETWORK_NAME])
