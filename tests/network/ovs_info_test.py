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
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA
#
# Refer to the README and COPYING files for full details of the license
#
from __future__ import absolute_import

from contextlib import contextmanager
from copy import deepcopy

from nose.plugins.attrib import attr

from .nettestlib import dummy_device, bond_device
from .ovsnettestlib import OvsService, TEST_BRIDGE
from monkeypatch import MonkeyPatch
from testValidation import ValidateRunningAsRoot
from testlib import VdsmTestCase

from vdsm.network.ovs import info
from vdsm.network.ovs.driver import create


TEST_NETWORK = 'test-network'
TEST_ADDRESS = '192.168.1.10'
TEST_NETMASK = '255.255.255.0'
TEST_ADDRESS_WITH_PREFIX = '192.168.1.10/24'
TEST_NIC = 'eth0'
TEST_VLAN = 10
TEST_VLANED_NIC = '%s.%s' % (TEST_NIC, TEST_VLAN)
TEST_VLANED_NETWORK = 'test-network' + str(TEST_VLAN)


@contextmanager
def _setup_ovs_network(ovsdb, sb_iface):

    def _bridge():
        return ovsdb.add_br(TEST_BRIDGE)

    def _attach_southbound():
        commands = []
        commands.append(ovsdb.add_port(TEST_BRIDGE, sb_iface))
        commands.append(ovsdb.set_port_attr(
            sb_iface, 'other_config:vdsm_level', info.SOUTHBOUND))
        return commands

    def _northbound_port():
        commands = []
        commands.append(ovsdb.add_port(TEST_BRIDGE, TEST_VLANED_NETWORK))
        commands.append(ovsdb.set_port_attr(
            TEST_VLANED_NETWORK, 'tag', TEST_VLAN))
        commands.append(ovsdb.set_port_attr(
            TEST_VLANED_NETWORK, 'other_config:vdsm_level', info.NORTHBOUND))
        commands.append(ovsdb.set_interface_attr(
            TEST_VLANED_NETWORK, 'type', 'internal'))
        return commands

    with ovsdb.transaction() as t:
        t.add(_bridge())
        t.add(*_attach_southbound())
        t.add(*_northbound_port())

    try:
        yield
    finally:
        ovsdb.del_br(TEST_BRIDGE).execute()


@attr(type='integration')
class TestOvsInfo(VdsmTestCase):

    @ValidateRunningAsRoot
    def setUp(self):
        self.ovs_service = OvsService()
        self.ovs_service.setup()
        self.ovsdb = create()

    def tearDown(self):
        self.ovs_service.teardown()

    def test_ovs_info_with_sb_nic(self):
        with dummy_device() as nic:
            with _setup_ovs_network(self.ovsdb, nic):
                expected_bridges = {
                    TEST_BRIDGE: {
                        'stp': False,
                        'dpdk_enabled': False,
                        'ports': {
                            nic: {
                                'level': info.SOUTHBOUND,
                                'tag': None
                            },
                            TEST_VLANED_NETWORK: {
                                'level': info.NORTHBOUND,
                                'tag': TEST_VLAN
                            },
                            TEST_BRIDGE: {
                                'level': None,
                                'tag': None
                            }
                        }
                    }
                }

                ovs_info = info.OvsInfo()

                obtained_bridges = ovs_info.bridges
                self.assertEqual(obtained_bridges, expected_bridges)

                obtained_bridges_by_sb = ovs_info.bridges_by_sb
                self.assertEqual(obtained_bridges_by_sb, {nic: TEST_BRIDGE})

    def test_ovs_info_with_sb_bond(self):
        with dummy_device() as nic:
            with bond_device([nic]) as bond:
                with _setup_ovs_network(self.ovsdb, bond):
                    expected_bridges = {
                        TEST_BRIDGE: {
                            'stp': False,
                            'dpdk_enabled': False,
                            'ports': {
                                TEST_VLANED_NETWORK: {
                                    'level': info.NORTHBOUND,
                                    'tag': TEST_VLAN
                                },
                                TEST_BRIDGE: {
                                    'level': None,
                                    'tag': None
                                },
                                bond: {
                                    'level': info.SOUTHBOUND,
                                    'tag': None
                                }
                            }
                        }
                    }

                    ovs_info = info.OvsInfo()

                    obtained_bridges = ovs_info.bridges
                    self.assertEqual(obtained_bridges, expected_bridges)

                    obtained_bridges_by_sb = ovs_info.bridges_by_sb
                    self.assertEqual(obtained_bridges_by_sb,
                                     {bond: TEST_BRIDGE})


class MockedOvsInfo(info.OvsInfo):
    def __init__(self):
        self._bridges = {
            TEST_BRIDGE: {
                'stp': False,
                'ports': {
                    TEST_NETWORK: {
                        'level': info.NORTHBOUND,
                        'tag': None
                    },
                    TEST_VLANED_NETWORK: {
                        'level': info.NORTHBOUND,
                        'tag': TEST_VLAN
                    },
                    TEST_BRIDGE: {
                        'level': None,
                        'tag': None
                    },
                    TEST_NIC: {
                        'level': info.SOUTHBOUND,
                        'tag': None
                    }
                }
            }
        }


@attr(type='unit')
class TestOvsNetInfo(VdsmTestCase):

    TEST_OVS_NETINFO = {
        'networks': {
            TEST_NETWORK: {
                'addr': TEST_ADDRESS,
                'bond': '',
                'bridged': True,
                'dhcpv4': False,
                'dhcpv6': False,
                'gateway': '',
                'iface': TEST_NETWORK,
                'ipv4addrs': [TEST_ADDRESS_WITH_PREFIX],
                'ipv4defaultroute': False,
                'ipv6addrs': [],
                'ipv6autoconf': True,
                'ipv6gateway': '::',
                'mtu': 1500,
                'netmask': TEST_NETMASK,
                'nics': [TEST_NIC],
                'ports': [TEST_NIC],
                'stp': False,
                'switch': 'ovs'
            },
            TEST_VLANED_NETWORK: {
                'addr': TEST_ADDRESS,
                'bond': '',
                'bridged': True,
                'dhcpv4': False,
                'dhcpv6': False,
                'gateway': '',
                'iface': TEST_VLANED_NETWORK,
                'ipv4addrs': [TEST_ADDRESS_WITH_PREFIX],
                'ipv4defaultroute': False,
                'ipv6addrs': [],
                'ipv6autoconf': True,
                'ipv6gateway': '::',
                'mtu': 1500,
                'netmask': TEST_NETMASK,
                'nics': [TEST_NIC],
                'ports': [TEST_VLANED_NIC],
                'stp': False,
                'switch': 'ovs',
                'vlanid': TEST_VLAN
            }
        },
        'bridges': {
            TEST_NETWORK: {
                'addr': TEST_ADDRESS,
                'dhcpv4': False,
                'dhcpv6': False,
                'gateway': '',
                'ipv4addrs': [TEST_ADDRESS_WITH_PREFIX],
                'ipv4defaultroute': False,
                'ipv6addrs': [],
                'ipv6autoconf': True,
                'ipv6gateway': '::',
                'mtu': 1500,
                'netmask': TEST_NETMASK,
                'ports': [TEST_NIC],
                'stp': False
            },
            TEST_VLANED_NETWORK: {
                'addr': TEST_ADDRESS,
                'dhcpv4': False,
                'dhcpv6': False,
                'gateway': '',
                'ipv4addrs': [TEST_ADDRESS_WITH_PREFIX],
                'ipv4defaultroute': False,
                'ipv6addrs': [],
                'ipv6autoconf': True,
                'ipv6gateway': '::',
                'mtu': 1500,
                'netmask': TEST_NETMASK,
                'ports': [TEST_VLANED_NIC],
                'stp': False
            }
        },
        'vlans': {
            TEST_VLANED_NIC: {
                'addr': '',
                'dhcpv4': False,
                'dhcpv6': False,
                'gateway': '',
                'iface': TEST_NIC,
                'ipv4addrs': [],
                'ipv4defaultroute': False,
                'ipv6addrs': [],
                'ipv6autoconf': False,
                'ipv6gateway': '',
                'mtu': 1500,
                'netmask': '',
                'vlanid': TEST_VLAN
            }
        }
    }

    TEST_BRIDGELESS_OVS_NETINFO = {
        'networks': {
            TEST_NETWORK: {
                'addr': TEST_ADDRESS,
                'bond': '',
                'bridged': False,
                'dhcpv4': False,
                'dhcpv6': False,
                'gateway': '',
                'iface': TEST_NIC,
                'ipv4addrs': [TEST_ADDRESS_WITH_PREFIX],
                'ipv4defaultroute': False,
                'ipv6addrs': [],
                'ipv6autoconf': True,
                'ipv6gateway': '::',
                'mtu': 1500,
                'netmask': TEST_NETMASK,
                'nics': [TEST_NIC],
                'ports': [TEST_NIC],
                'stp': False,
                'switch': 'ovs'
            },
            TEST_VLANED_NETWORK: {
                'addr': TEST_ADDRESS,
                'bond': '',
                'bridged': False,
                'dhcpv4': False,
                'dhcpv6': False,
                'gateway': '',
                'iface': TEST_VLANED_NIC,
                'ipv4addrs': [TEST_ADDRESS_WITH_PREFIX],
                'ipv4defaultroute': False,
                'ipv6addrs': [],
                'ipv6autoconf': True,
                'ipv6gateway': '::',
                'mtu': 1500,
                'netmask': TEST_NETMASK,
                'nics': [TEST_NIC],
                'ports': [TEST_VLANED_NIC],
                'stp': False,
                'switch': 'ovs',
                'vlanid': TEST_VLAN
            }
        },
        'bridges': {},
        'vlans': {
            TEST_VLANED_NIC: {
                'addr': TEST_ADDRESS,
                'dhcpv4': False,
                'dhcpv6': False,
                'gateway': '',
                'iface': TEST_NIC,
                'ipv4addrs': [TEST_ADDRESS_WITH_PREFIX],
                'ipv4defaultroute': False,
                'ipv6addrs': [],
                'ipv6autoconf': True,
                'ipv6gateway': '::',
                'mtu': 1500,
                'netmask': TEST_NETMASK,
                'vlanid': TEST_VLAN
            }
        }
    }

    TEST_KERNEL_NETINFO = {
        'bondings': {},
        'nics': {
            TEST_NIC: {
                'addr': '',
                'dhcpv4': False,
                'dhcpv6': False,
                'gateway': '',
                'ipv4addrs': [],
                'ipv4defaultroute': False,
                'ipv6addrs': [],
                'ipv6autoconf': True,
                'ipv6gateway': '::',
                'mtu': 1500,
                'netmask': ''
            }
        }
    }

    TEST_BRIDGELESS_KERNEL_NETINFO = {
        'bondings': {},
        'nics': {
            TEST_NIC: {
                'addr': TEST_ADDRESS,
                'dhcpv4': False,
                'dhcpv6': False,
                'gateway': '',
                'ipv4addrs': [TEST_ADDRESS_WITH_PREFIX],
                'ipv4defaultroute': False,
                'ipv6addrs': [],
                'ipv6autoconf': True,
                'ipv6gateway': '::',
                'mtu': 1500,
                'netmask': TEST_NETMASK
            }
        }
    }

    @MonkeyPatch(info, 'get_mtu', lambda *args: 1500)
    @MonkeyPatch(info, 'is_ipv6_local_auto', lambda *args: True)
    @MonkeyPatch(info, 'get_gateway',
                 lambda *args, **kwargs: ('' if kwargs.get('family') == 4
                                          else '::'))
    @MonkeyPatch(info, 'getIpInfo',
                 lambda *args: (TEST_ADDRESS, TEST_NETMASK,
                                [TEST_ADDRESS_WITH_PREFIX], []))
    @MonkeyPatch(info, 'OvsInfo', MockedOvsInfo)
    def test_ovs_netinfo(self):
        obtained_netinfo = info.get_netinfo()
        self.assertEqual(obtained_netinfo, self.TEST_OVS_NETINFO)

    def test_fake_bridgeless(self):
        fake_running_bridgeless_ovs_networks = {
            TEST_NETWORK, TEST_VLANED_NETWORK}
        test_ovs_netinfo = deepcopy(self.TEST_OVS_NETINFO)
        test_kernel_netinfo = deepcopy(self.TEST_KERNEL_NETINFO)

        info.fake_bridgeless(test_ovs_netinfo, test_kernel_netinfo,
                             fake_running_bridgeless_ovs_networks)

        self.assertEqual(test_ovs_netinfo, self.TEST_BRIDGELESS_OVS_NETINFO)
        self.assertEqual(test_kernel_netinfo,
                         self.TEST_BRIDGELESS_KERNEL_NETINFO)
