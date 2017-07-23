#
# Copyright 2016-2017 Red Hat, Inc.
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

from vdsm.network.errors import ERR_BAD_PARAMS

import pytest

from .netfunctestlib import NetFuncTestCase, NOCHK, SetupNetworksError
from network.nettestlib import dummy_device, restore_resolv_conf

NETWORK_NAME = 'test-network'
NAMESERVERS = ['1.2.3.4', '2.3.4.5']
IPv4_ADDRESS = '192.0.2.1'
IPv4_GATEWAY = '192.0.2.254'
IPv4_NETMASK = '255.255.255.0'


@pytest.mark.parametrize('switch', [pytest.mark.legacy_switch('legacy')])
class TestNetworkDNS(NetFuncTestCase):

    def test_set_host_nameservers(self, switch):
        self.update_netinfo()
        original_nameservers = self.netinfo.nameservers
        assert original_nameservers != NAMESERVERS, (
            'Current nameservers must differ from tested ones')
        with dummy_device() as nic:
            NETCREATE = {NETWORK_NAME: {'nic': nic, 'switch': switch,
                                        'nameservers': NAMESERVERS,
                                        'defaultRoute': True,
                                        'ipaddr': IPv4_ADDRESS,
                                        'netmask': IPv4_NETMASK,
                                        'gateway': IPv4_GATEWAY,
                                        }}
            with restore_resolv_conf():
                with self.setupNetworks(NETCREATE, {}, NOCHK):
                    self.assertNameservers(NAMESERVERS)

    def test_preserve_host_nameservers(self, switch):
        self.update_netinfo()
        original_nameservers = self.netinfo.nameservers
        with dummy_device() as nic:
            NETCREATE = {NETWORK_NAME: {'nic': nic, 'switch': switch,
                                        'defaultRoute': True,
                                        'ipaddr': IPv4_ADDRESS,
                                        'netmask': IPv4_NETMASK,
                                        'gateway': IPv4_GATEWAY,
                                        }}
            with restore_resolv_conf():
                with self.setupNetworks(NETCREATE, {}, NOCHK):
                    self.assertNameservers(original_nameservers)

    def test_set_nameservers_on_non_default_network(self, switch):
        with dummy_device() as nic:
            NETCREATE = {NETWORK_NAME: {'nic': nic, 'switch': switch,
                                        'nameservers': NAMESERVERS,
                                        'defaultRoute': False,
                                        'ipaddr': IPv4_ADDRESS,
                                        'netmask': IPv4_NETMASK,
                                        'gateway': IPv4_GATEWAY,
                                        }}
            with pytest.raises(SetupNetworksError) as err:
                self.setupNetworks(NETCREATE, {}, NOCHK)
            assert err.value.status == ERR_BAD_PARAMS
