#
# Copyright 2016 Red Hat, Inc.
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

from nose.plugins.attrib import attr

from .netfunctestlib import NetFuncTestCase, NOCHK, SetupNetworksError
from .nettestlib import dummy_device, restore_resolv_conf

NETWORK_NAME = 'test-network'
NAMESERVERS = ['1.2.3.4', '2.3.4.5']
IPv4_ADDRESS = '192.0.2.1'
IPv4_GATEWAY = '192.0.2.254'
IPv4_NETMASK = '255.255.255.0'


class NetworkDNSTemplate(NetFuncTestCase):
    __test__ = False

    def test_set_host_nameservers(self):
        self.update_netinfo()
        original_nameservers = self.netinfo.nameservers
        self.assertNotEqual(original_nameservers, NAMESERVERS,
                            'Current nameservers must differ from tested ones')
        with dummy_device() as nic:
            NETCREATE = {NETWORK_NAME: {'nic': nic, 'switch': self.switch,
                                        'nameservers': NAMESERVERS,
                                        'defaultRoute': True,
                                        'ipaddr': IPv4_ADDRESS,
                                        'netmask': IPv4_NETMASK,
                                        'gateway': IPv4_GATEWAY,
                                        }}
            with restore_resolv_conf():
                with self.setupNetworks(NETCREATE, {}, NOCHK):
                    self.assertNameservers(NAMESERVERS)

    def test_preserve_host_nameservers(self):
        self.update_netinfo()
        original_nameservers = self.netinfo.nameservers
        with dummy_device() as nic:
            NETCREATE = {NETWORK_NAME: {'nic': nic, 'switch': self.switch,
                                        'defaultRoute': True,
                                        'ipaddr': IPv4_ADDRESS,
                                        'netmask': IPv4_NETMASK,
                                        'gateway': IPv4_GATEWAY,
                                        }}
            with restore_resolv_conf():
                with self.setupNetworks(NETCREATE, {}, NOCHK):
                    self.assertNameservers(original_nameservers)

    def test_set_nameservers_on_non_default_network(self):
        with dummy_device() as nic:
            NETCREATE = {NETWORK_NAME: {'nic': nic, 'switch': self.switch,
                                        'nameservers': NAMESERVERS,
                                        'defaultRoute': False,
                                        'ipaddr': IPv4_ADDRESS,
                                        'netmask': IPv4_NETMASK,
                                        'gateway': IPv4_GATEWAY,
                                        }}
            with self.assertRaises(SetupNetworksError) as err:
                self.setupNetworks(NETCREATE, {}, NOCHK)
            self.assertEqual(err.exception.status, ERR_BAD_PARAMS)


@attr(type='functional', switch='legacy')
class NetworkDNSLegacyTest(NetworkDNSTemplate):
    __test__ = True
    switch = 'legacy'


@attr(type='functional', switch='ovs')
class NetworkDNSOvsTest(NetworkDNSTemplate):
    # TODO: Implement 'nameservers' for OVS switch setups.
    __test__ = False
    switch = 'ovs'
