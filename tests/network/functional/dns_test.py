# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from vdsm.network.errors import ERR_BAD_PARAMS

import pytest

from network.nettestlib import dummy_device
from network.nettestlib import preserve_default_route
from network.nettestlib import restore_resolv_conf

from . import netfunctestlib as nftestlib

NETWORK_NAME = 'test-network'
NAMESERVERS = ['1.2.3.4', '2.3.4.5', '6.7.8.9']
IPv4_ADDRESS = '192.0.2.1'
IPv4_GATEWAY = '192.0.2.254'
IPv4_NETMASK = '255.255.255.0'


@pytest.fixture(autouse=True)
def refresh_cache(adapter):
    adapter.refresh_netinfo()


@pytest.fixture
def nic0():
    with dummy_device() as nic:
        yield nic


@nftestlib.parametrize_switch
class TestNetworkDNS(object):
    def test_set_host_nameservers(self, adapter, switch, nic0):
        original_nameservers = adapter.netinfo.nameservers
        assert (
            original_nameservers != NAMESERVERS
        ), 'Current nameservers must differ from tested ones'
        NETCREATE = {
            NETWORK_NAME: {
                'nic': nic0,
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

    def test_preserve_host_nameservers(self, adapter, switch, nic0):
        original_nameservers = adapter.netinfo.nameservers
        NETCREATE = {
            NETWORK_NAME: {
                'nic': nic0,
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

    def test_set_nameservers_on_non_default_network(
        self, adapter, switch, nic0
    ):
        NETCREATE = {
            NETWORK_NAME: {
                'nic': nic0,
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
