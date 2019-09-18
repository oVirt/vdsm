#
# Copyright 2016-2018 Red Hat, Inc.
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

from __future__ import absolute_import
from __future__ import division

from vdsm.network.errors import ERR_BAD_PARAMS

import pytest

from network.nettestlib import dummy_device
from network.nettestlib import preserve_default_route
from network.nettestlib import restore_resolv_conf

from . import netfunctestlib as nftestlib

NETWORK_NAME = 'test-network'
# FIXME: Add third dns when nmstate starts to support it
# see https://nmstate.atlassian.net/browse/NMSTATE-220
NAMESERVERS = ['1.2.3.4', '2.3.4.5']
IPv4_ADDRESS = '192.0.2.1'
IPv4_GATEWAY = '192.0.2.254'
IPv4_NETMASK = '255.255.255.0'


adapter = None


@pytest.fixture(scope='module', autouse=True)
def create_adapter(target):
    global adapter
    adapter = nftestlib.NetFuncTestAdapter(target)


@pytest.fixture(autouse=True)
def refresh_cache():
    adapter.refresh_netinfo()


@pytest.mark.nmstate
@nftestlib.parametrize_switch
class TestNetworkDNS(object):
    def test_set_host_nameservers(self, switch):
        original_nameservers = adapter.netinfo.nameservers
        assert (
            original_nameservers != NAMESERVERS
        ), 'Current nameservers must differ from tested ones'
        with dummy_device() as nic:
            NETCREATE = {
                NETWORK_NAME: {
                    'nic': nic,
                    'switch': switch,
                    'nameservers': NAMESERVERS,
                    'defaultRoute': True,
                    'ipaddr': IPv4_ADDRESS,
                    'netmask': IPv4_NETMASK,
                    'gateway': IPv4_GATEWAY,
                }
            }
            with restore_resolv_conf(), preserve_default_route():
                with adapter.setupNetworks(NETCREATE, {}, nftestlib.NOCHK):
                    adapter.assertNameservers(NAMESERVERS)

    def test_preserve_host_nameservers(self, switch):
        original_nameservers = adapter.netinfo.nameservers
        with dummy_device() as nic:
            NETCREATE = {
                NETWORK_NAME: {
                    'nic': nic,
                    'switch': switch,
                    'defaultRoute': True,
                    'ipaddr': IPv4_ADDRESS,
                    'netmask': IPv4_NETMASK,
                    'gateway': IPv4_GATEWAY,
                }
            }
            with restore_resolv_conf(), preserve_default_route():
                with adapter.setupNetworks(NETCREATE, {}, nftestlib.NOCHK):
                    adapter.assertNameservers(original_nameservers)

    def test_set_nameservers_on_non_default_network(self, switch):
        with dummy_device() as nic:
            NETCREATE = {
                NETWORK_NAME: {
                    'nic': nic,
                    'switch': switch,
                    'nameservers': NAMESERVERS,
                    'defaultRoute': False,
                    'ipaddr': IPv4_ADDRESS,
                    'netmask': IPv4_NETMASK,
                    'gateway': IPv4_GATEWAY,
                }
            }
            with pytest.raises(nftestlib.SetupNetworksError) as err:
                adapter.setupNetworks(NETCREATE, {}, nftestlib.NOCHK)
            assert err.value.status == ERR_BAD_PARAMS
