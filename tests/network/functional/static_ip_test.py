# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

import pytest

from vdsm.network import errors as ne

from . import netfunctestlib as nftestlib
from .netfunctestlib import NOCHK, SetupNetworksError
from network.nettestlib import dummy_device
from network.nettestlib import Interface
from network.nettestlib import IpFamily
from network.nettestlib import parametrize_ip_families
from network.nettestlib import preserve_default_route
from network.nettestlib import restore_resolv_conf

NETWORK_NAME = 'test-network'
NETWORK2_NAME = 'test-network2'
BOND_NAME = 'bond1'
VLAN = 10

IPv4_ADDRESS = '192.0.2.1'
IPv4_ADDRESS2 = '192.0.3.1'
IPv4_NETMASK = '255.255.255.0'
IPv4_NETMASK2 = '255.255.255.128'
IPv4_PREFIX_LEN = '24'
IPv4_GATEWAY = '192.0.2.100'
IPv4_GATEWAY2 = '192.0.3.100'
IPv6_ADDRESS = 'fdb3:84e5:4ff4:55e3::1010'
IPv6_PREFIX_LEN = '64'
IPv6_GATEWAY = 'fdb3:84e5:4ff4:55e3::1'
IPv6_GATEWAY2 = 'fdb3:84e5:4ff4:55e3::2'


@pytest.fixture
def nic0():
    with dummy_device() as nic:
        yield nic


@pytest.fixture
def nic1():
    with dummy_device() as nic:
        yield nic


@nftestlib.parametrize_switch
class TestNetworkStaticIpBasic(object):
    @nftestlib.parametrize_bridged
    @parametrize_ip_families
    def test_add_net_with_ip_based_on_nic(
        self, adapter, switch, bridged, families, nic0, nic1
    ):
        self._test_add_net_with_ip(
            adapter, families, switch, [nic0, nic1], bridged=bridged
        )

    @parametrize_ip_families
    def test_add_net_with_ip_based_on_bond(
        self, adapter, switch, families, nic0, nic1
    ):
        self._test_add_net_with_ip(
            adapter, families, switch, [nic0, nic1], bonded=True
        )

    @nftestlib.parametrize_bonded
    @parametrize_ip_families
    def test_add_net_with_ip_based_on_vlan(
        self, adapter, switch, families, bonded, nic0, nic1
    ):
        self._test_add_net_with_ip(
            adapter, families, switch, [nic0, nic1], bonded, vlaned=True
        )

    def test_add_net_with_prefix(self, adapter, switch, nic0):
        network_attrs = {
            'nic': nic0,
            'ipaddr': IPv4_ADDRESS,
            'prefix': IPv4_PREFIX_LEN,
            'switch': switch,
        }
        netcreate = {NETWORK_NAME: network_attrs}

        with adapter.setupNetworks(netcreate, {}, NOCHK):
            adapter.assertNetworkIp(NETWORK_NAME, netcreate[NETWORK_NAME])

    def test_static_ip_configuration_v4_to_v6_and_back(
        self, adapter, switch, nic0
    ):
        net_ipv4_atts = {
            'nic': nic0,
            'ipaddr': IPv4_ADDRESS,
            'netmask': IPv4_NETMASK,
            'switch': switch,
        }
        net_ipv6_atts = {
            'nic': nic0,
            'ipv6addr': IPv6_ADDRESS + '/' + IPv6_PREFIX_LEN,
            'switch': switch,
        }

        net_ipv4 = {NETWORK_NAME: net_ipv4_atts}
        net_ipv6 = {NETWORK_NAME: net_ipv6_atts}

        with adapter.setupNetworks(net_ipv4, {}, NOCHK):
            adapter.assertNetworkIp(NETWORK_NAME, net_ipv4_atts)
            adapter.setupNetworks(net_ipv6, {}, NOCHK)
            adapter.assertNetworkIp(NETWORK_NAME, net_ipv6_atts)
            adapter.setupNetworks(net_ipv4, {}, NOCHK)
            adapter.assertNetworkIp(NETWORK_NAME, net_ipv4_atts)

    def test_edit_ipv4_address_on_bonded_network(
        self, adapter, switch, nic0, nic1
    ):
        net_attrs_ip1 = {
            'bonding': BOND_NAME,
            'bridged': False,
            'ipaddr': IPv4_ADDRESS,
            'netmask': IPv4_NETMASK,
            'gateway': IPv4_GATEWAY,
            'switch': switch,
        }
        net_attrs_ip2 = {
            'bonding': BOND_NAME,
            'bridged': False,
            'ipaddr': IPv4_ADDRESS2,
            'netmask': IPv4_NETMASK2,
            'gateway': IPv4_GATEWAY2,
            'switch': switch,
        }

        bond = {BOND_NAME: {'nics': [nic0, nic1], 'switch': switch}}

        net1 = {NETWORK_NAME: net_attrs_ip1}
        net2 = {NETWORK_NAME: net_attrs_ip2}

        with adapter.setupNetworks(net1, bond, NOCHK):
            adapter.assertNetworkIp(NETWORK_NAME, net_attrs_ip1)
            adapter.setupNetworks(net2, bond, NOCHK)
            adapter.assertNetworkIp(NETWORK_NAME, net_attrs_ip2)

    def test_add_static_ip_to_the_existing_net_with_bond(
        self, adapter, switch, nic0, nic1
    ):
        network_attrs1 = {
            'bonding': BOND_NAME,
            'bridged': False,
            'ipaddr': IPv4_ADDRESS,
            'netmask': IPv4_NETMASK,
            'switch': switch,
        }

        network_attrs2 = {
            'bonding': BOND_NAME,
            'bridged': True,
            'vlan': VLAN,
            'switch': switch,
        }

        bond = {BOND_NAME: {'nics': [nic0, nic1], 'switch': switch}}
        netconf = {NETWORK_NAME: network_attrs1, NETWORK2_NAME: network_attrs2}

        net2 = {NETWORK2_NAME: netconf[NETWORK2_NAME]}

        with adapter.setupNetworks(netconf, bond, NOCHK):
            adapter.assertNetworkIp(NETWORK_NAME, network_attrs1)
            adapter.assertNetworkIp(NETWORK2_NAME, network_attrs2)
            network_attrs2['ipaddr'] = IPv4_ADDRESS2
            network_attrs2['netmask'] = IPv4_NETMASK
            adapter.setupNetworks(net2, bond, NOCHK)
            adapter.assertNetworkIp(NETWORK_NAME, network_attrs1)
            adapter.assertNetworkIp(NETWORK2_NAME, network_attrs2)

    def test_add_static_ipv6_expanded_notation(self, adapter, switch, nic0):
        exploded_ipv6_address = '2001:0db8:85a3:0000:0000:8a2e:0370:7331'
        network_attrs = {
            'nic': nic0,
            'ipv6addr': exploded_ipv6_address + '/' + IPv6_PREFIX_LEN,
            'switch': switch,
        }
        netcreate = {NETWORK_NAME: network_attrs}

        with adapter.setupNetworks(netcreate, {}, NOCHK):
            adapter.assertNetworkIp(NETWORK_NAME, netcreate[NETWORK_NAME])

    def _test_add_net_with_ip(
        self,
        adapter,
        families,
        switch,
        nics,
        bonded=False,
        vlaned=False,
        bridged=False,
    ):
        IPv6_ADDRESS_AND_PREFIX_LEN = IPv6_ADDRESS + '/' + IPv6_PREFIX_LEN

        network_attrs = {'bridged': bridged, 'switch': switch}

        if IpFamily.IPv4 in families:
            network_attrs['ipaddr'] = IPv4_ADDRESS
            network_attrs['netmask'] = IPv4_NETMASK
        if IpFamily.IPv6 in families:
            network_attrs['ipv6addr'] = IPv6_ADDRESS_AND_PREFIX_LEN

        if bonded:
            bondcreate = {BOND_NAME: {'nics': nics, 'switch': switch}}
            network_attrs['bonding'] = BOND_NAME
        else:
            bondcreate = {}
            network_attrs['nic'] = nics[0]

        if vlaned:
            network_attrs['vlan'] = VLAN

        netcreate = {NETWORK_NAME: network_attrs}

        with adapter.setupNetworks(netcreate, bondcreate, NOCHK):
            adapter.assertNetworkIp(NETWORK_NAME, netcreate[NETWORK_NAME])
            if vlaned:
                base = (
                    adapter.netinfo.bondings.get(BOND_NAME)
                    or adapter.netinfo.nics[nics[0]]
                )
                adapter.assertDisabledIPv4(base)
                adapter.assertDisabledIPv6(base)


@nftestlib.parametrize_switch
class TestNetworkIPDefaultGateway(object):
    @pytest.fixture(autouse=True)
    def preserve_conf(self):
        with restore_resolv_conf(), preserve_default_route():
            yield

    def test_add_net_with_ipv4_default_gateway(self, adapter, switch, nic0):
        network_attrs = {
            'nic': nic0,
            'ipaddr': IPv4_ADDRESS,
            'netmask': IPv4_NETMASK,
            'gateway': IPv4_GATEWAY,
            'defaultRoute': True,
            'switch': switch,
        }
        netcreate = {NETWORK_NAME: network_attrs}

        with adapter.setupNetworks(netcreate, {}, NOCHK):
            adapter.assertNetworkIp(NETWORK_NAME, network_attrs)

    def test_add_net_with_ipv6_default_gateway(self, adapter, switch, nic0):
        network_attrs = {
            'nic': nic0,
            #  In order to use def. route true we need to assign IPv4
            #  address, as defaultRoute reported by caps reports
            #  only presence of IPv4 gateway see
            #  https://bugzilla.redhat.com/791555
            'ipaddr': IPv4_ADDRESS,
            'netmask': IPv4_NETMASK,
            'gateway': IPv4_GATEWAY,
            'ipv6addr': IPv6_ADDRESS + '/' + IPv6_PREFIX_LEN,
            'ipv6gateway': IPv6_GATEWAY,
            'defaultRoute': True,
            'nameservers': [],
            'switch': switch,
        }
        netcreate = {NETWORK_NAME: network_attrs}

        with adapter.setupNetworks(netcreate, {}, NOCHK):
            adapter.assertNetworkIp(NETWORK_NAME, network_attrs)

    @parametrize_ip_families
    def test_edit_default_gateway(self, adapter, switch, families, nic0):
        network_attrs = {
            'nic': nic0,
            'ipaddr': IPv4_ADDRESS,
            'netmask': IPv4_NETMASK,
            'gateway': IPv4_GATEWAY,
            'ipv6addr': IPv6_ADDRESS + '/' + IPv6_PREFIX_LEN,
            'ipv6gateway': IPv6_GATEWAY,
            'defaultRoute': True,
            'nameservers': [],
            'switch': switch,
        }
        netcreate = {NETWORK_NAME: network_attrs}

        with adapter.setupNetworks(netcreate, {}, NOCHK):
            adapter.assertNetworkIp(NETWORK_NAME, network_attrs)
            if IpFamily.IPv6 in families:
                network_attrs['ipv6gateway'] = IPv6_GATEWAY2
            if IpFamily.IPv4 in families:
                network_attrs['gateway'] = IPv4_GATEWAY2
                network_attrs['ipaddr'] = IPv4_ADDRESS2
            adapter.setupNetworks(netcreate, {}, NOCHK)
            adapter.assertNetworkIp(NETWORK_NAME, network_attrs)

    def test_add_net_and_move_ipv4_default_gateway(
        self, adapter, switch, nic0, nic1
    ):
        net1_attrs = {
            'nic': nic0,
            'ipaddr': IPv4_ADDRESS,
            'netmask': IPv4_NETMASK,
            'gateway': IPv4_GATEWAY,
            'defaultRoute': True,
            'switch': switch,
        }
        net2_attrs = {
            'nic': nic1,
            'ipaddr': IPv4_ADDRESS2,
            'netmask': IPv4_NETMASK,
            'gateway': IPv4_GATEWAY2,
            'defaultRoute': True,
            'switch': switch,
        }
        net1create = {NETWORK_NAME: net1_attrs}
        net2create = {NETWORK2_NAME: net2_attrs}

        with adapter.setupNetworks(net1create, {}, NOCHK):
            with adapter.setupNetworks(net2create, {}, NOCHK):
                net1_attrs['defaultRoute'] = False
                adapter.assertNetworkIp(NETWORK_NAME, net1_attrs)
                adapter.assertNetworkIp(NETWORK2_NAME, net2_attrs)

    def test_add_net_without_default_route(self, adapter, switch, nic0, nic1):
        net1_attrs = {
            'nic': nic0,
            'ipaddr': IPv4_ADDRESS,
            'netmask': IPv4_NETMASK,
            'gateway': IPv4_GATEWAY,
            'defaultRoute': True,
            'switch': switch,
        }
        net2_attrs = {
            'nic': nic1,
            'ipaddr': IPv4_ADDRESS2,
            'netmask': IPv4_NETMASK,
            'gateway': IPv4_GATEWAY2,
            'defaultRoute': False,
            'switch': switch,
        }

        net1create = {NETWORK_NAME: net1_attrs}
        net2create = {NETWORK2_NAME: net2_attrs}

        with adapter.setupNetworks(net1create, {}, NOCHK):
            with adapter.setupNetworks(net2create, {}, NOCHK):
                adapter.assertNetworkIp(NETWORK_NAME, net1_attrs)
                adapter.assertNetworkIp(NETWORK2_NAME, net2_attrs)
            adapter.assertNetworkIp(NETWORK_NAME, net1_attrs)

    def test_add_net_without_gateway_and_default_route(
        self, adapter, switch, nic0, nic1
    ):
        net1_attrs = {
            'nic': nic0,
            'ipaddr': IPv4_ADDRESS,
            'netmask': IPv4_NETMASK,
            'gateway': IPv4_GATEWAY,
            'defaultRoute': True,
            'switch': switch,
        }
        net2_attrs = {
            'nic': nic1,
            'ipaddr': IPv4_ADDRESS2,
            'netmask': IPv4_NETMASK,
            'defaultRoute': False,
            'switch': switch,
        }

        net1create = {NETWORK_NAME: net1_attrs}
        net2create = {NETWORK2_NAME: net2_attrs}

        with adapter.setupNetworks(net1create, {}, NOCHK):
            with adapter.setupNetworks(net2create, {}, NOCHK):
                adapter.assertNetworkIp(NETWORK_NAME, net1_attrs)
                adapter.assertNetworkIp(NETWORK2_NAME, net2_attrs)

    def test_create_net_without_default_route(self, adapter, switch, nic0):
        net1_attrs = {
            'nic': nic0,
            'ipaddr': IPv4_ADDRESS,
            'netmask': IPv4_NETMASK,
            'gateway': IPv4_GATEWAY,
            'switch': switch,
        }
        net1create = {NETWORK_NAME: net1_attrs}

        with adapter.setupNetworks(net1create, {}, NOCHK):
            adapter.assertNetworkIp(NETWORK_NAME, net1_attrs)

    @pytest.mark.parametrize(
        'remove_gw', [True, False], ids=['removeGw', 'updateGw']
    )
    def test_edit_gateway_without_default_route(
        self, adapter, switch, remove_gw, nic0
    ):
        network_attrs = {
            'nic': nic0,
            'ipaddr': IPv4_ADDRESS,
            'netmask': IPv4_NETMASK,
            'gateway': IPv4_GATEWAY,
            'switch': switch,
        }
        netcreate = {NETWORK_NAME: network_attrs}

        with adapter.setupNetworks(netcreate, {}, NOCHK):
            adapter.assertNetworkIp(NETWORK_NAME, network_attrs)
            if remove_gw:
                network_attrs.pop('gateway')
            else:
                network_attrs['gateway'] = IPv4_GATEWAY2
                network_attrs['ipaddr'] = IPv4_ADDRESS2
            adapter.setupNetworks(netcreate, {}, NOCHK)
            adapter.assertNetworkIp(NETWORK_NAME, network_attrs)

    def test_remove_net_without_default_route(
        self, adapter, switch, nic0, nic1
    ):
        net1_attrs = {
            'nic': nic0,
            'ipaddr': IPv4_ADDRESS,
            'netmask': IPv4_NETMASK,
            'gateway': IPv4_GATEWAY,
            'defaultRoute': True,
            'switch': switch,
        }
        net2_attrs = {
            'nic': nic1,
            'ipaddr': IPv4_ADDRESS2,
            'netmask': IPv4_NETMASK,
            'gateway': IPv4_GATEWAY2,
            'defaultRoute': False,
            'switch': switch,
        }

        net1create = {NETWORK_NAME: net1_attrs}
        net2create = {NETWORK2_NAME: net2_attrs}

        with adapter.setupNetworks(net1create, {}, NOCHK):
            with adapter.setupNetworks(net2create, {}, NOCHK):
                adapter.assertNetworkIp(NETWORK2_NAME, net2_attrs)
            adapter.assertNetworkIp(NETWORK_NAME, net1_attrs)

    def test_remove_net_with_default_route_and_gateway(
        self, adapter, switch, nic0, nic1
    ):
        net1_attrs = {
            'nic': nic0,
            'ipaddr': IPv4_ADDRESS,
            'netmask': IPv4_NETMASK,
            'gateway': IPv4_GATEWAY,
            'defaultRoute': True,
            'switch': switch,
        }
        net2_attrs = {
            'nic': nic1,
            'ipaddr': IPv4_ADDRESS2,
            'netmask': IPv4_NETMASK,
            'switch': switch,
        }

        net1create = {NETWORK_NAME: net1_attrs}
        net2create = {NETWORK2_NAME: net2_attrs}

        with adapter.setupNetworks(net2create, {}, NOCHK):
            with adapter.setupNetworks(net1create, {}, NOCHK):
                adapter.assertNetworkIp(NETWORK_NAME, net1_attrs)
            adapter.assertNetworkIp(NETWORK2_NAME, net2_attrs)


@nftestlib.parametrize_switch
class TestAcquireNicsWithStaticIP(object):
    def test_attach_nic_with_ip_to_ipless_network(self, adapter, switch, nic0):
        nic0_interface = Interface.from_existing_dev_name(nic0)
        nic0_interface.add_ip(IPv4_ADDRESS, IPv4_PREFIX_LEN, IpFamily.IPv4)
        nic0_interface.add_ip(IPv6_ADDRESS, IPv6_PREFIX_LEN, IpFamily.IPv6)

        NETCREATE = {NETWORK_NAME: {'nic': nic0, 'switch': switch}}
        with adapter.setupNetworks(NETCREATE, {}, NOCHK):
            nic_netinfo = adapter.netinfo.nics[nic0]
            adapter.assertDisabledIPv4(nic_netinfo)
            adapter.assertDisabledIPv6(nic_netinfo)

    def test_attach_nic_with_ip_to_ip_network(self, adapter, switch, nic0):
        Interface.from_existing_dev_name(nic0).add_ip(
            IPv4_ADDRESS, IPv4_PREFIX_LEN, IpFamily.IPv4
        )

        NETCREATE = {
            NETWORK_NAME: {
                'nic': nic0,
                'ipaddr': IPv4_ADDRESS,
                'netmask': IPv4_NETMASK,
                'switch': switch,
            }
        }
        with adapter.setupNetworks(NETCREATE, {}, NOCHK):
            nic_netinfo = adapter.netinfo.nics[nic0]
            adapter.assertDisabledIPv4(nic_netinfo)
            adapter.assertNetworkIp(NETWORK_NAME, NETCREATE[NETWORK_NAME])

    def test_attach_nic_with_ip_as_a_slave_to_ipless_network(
        self, adapter, switch, nic0, nic1
    ):
        nic0_interface = Interface.from_existing_dev_name(nic0)
        nic0_interface.add_ip(IPv4_ADDRESS, IPv4_PREFIX_LEN, IpFamily.IPv4)
        nic0_interface.add_ip(IPv6_ADDRESS, IPv6_PREFIX_LEN, IpFamily.IPv6)

        NETCREATE = {NETWORK_NAME: {'bonding': BOND_NAME, 'switch': switch}}
        BONDCREATE = {BOND_NAME: {'nics': [nic0, nic1], 'switch': switch}}
        with adapter.setupNetworks(NETCREATE, BONDCREATE, NOCHK):
            nic_netinfo = adapter.netinfo.nics[nic0]
            adapter.assertDisabledIPv4(nic_netinfo)
            adapter.assertDisabledIPv6(nic_netinfo)

    def test_attach_nic_with_ip_as_a_slave_to_ip_network(
        self, adapter, switch, nic0, nic1
    ):
        Interface.from_existing_dev_name(nic0).add_ip(
            IPv4_ADDRESS, IPv4_PREFIX_LEN, IpFamily.IPv4
        )

        NETCREATE = {
            NETWORK_NAME: {
                'bonding': BOND_NAME,
                'ipaddr': IPv4_ADDRESS,
                'netmask': IPv4_NETMASK,
                'switch': switch,
            }
        }
        BONDCREATE = {BOND_NAME: {'nics': [nic0, nic1], 'switch': switch}}
        with adapter.setupNetworks(NETCREATE, BONDCREATE, NOCHK):
            nic_netinfo = adapter.netinfo.nics[nic0]
            adapter.assertDisabledIPv4(nic_netinfo)
            adapter.assertNetworkIp(NETWORK_NAME, NETCREATE[NETWORK_NAME])


@pytest.mark.legacy_switch
class TestIfacesWithMultiplesUsers(object):
    @nftestlib.parametrize_bonded
    def test_remove_ip_from_an_iface_used_by_a_vlan_network(
        self, adapter, bonded, nic0
    ):
        netcreate = {
            NETWORK_NAME: {
                'bridged': False,
                'ipaddr': IPv4_ADDRESS,
                'netmask': IPv4_NETMASK,
            },
            NETWORK2_NAME: {'bridged': False, 'vlan': VLAN},
        }

        bondcreate = {}
        if bonded:
            bondcreate[BOND_NAME] = {'nics': [nic0]}
            netcreate[NETWORK_NAME]['bonding'] = BOND_NAME
            netcreate[NETWORK2_NAME]['bonding'] = BOND_NAME
        else:
            netcreate[NETWORK_NAME]['nic'] = nic0
            netcreate[NETWORK2_NAME]['nic'] = nic0

        with adapter.setupNetworks(netcreate, bondcreate, NOCHK):
            netremove = {NETWORK_NAME: {'remove': True}}
            adapter.setupNetworks(netremove, {}, NOCHK)
            if bonded:
                adapter.assertDisabledIPv4(adapter.netinfo.bondings[BOND_NAME])
            adapter.assertDisabledIPv4(adapter.netinfo.nics[nic0])


@nftestlib.parametrize_switch
class TestIPValidation(object):
    def test_add_net_ip_missing_addresses_fails(self, adapter, switch, nic0):
        self._test_invalid_ip_config_fails(
            adapter, switch, nic0, ipaddr='1.2.3.4'
        )
        self._test_invalid_ip_config_fails(
            adapter, switch, nic0, gateway='1.2.3.4'
        )
        self._test_invalid_ip_config_fails(
            adapter, switch, nic0, netmask='255.255.255.0'
        )

    def test_add_net_out_of_range_addresses_fails(self, adapter, switch, nic0):
        self._test_invalid_ip_config_fails(
            adapter, switch, nic0, ipaddr='1.2.3.256', netmask='255.255.0.0'
        )
        self._test_invalid_ip_config_fails(
            adapter, switch, nic0, ipaddr='1.2.3.4', netmask='256.255.0.0'
        )
        self._test_invalid_ip_config_fails(
            adapter,
            switch,
            nic0,
            ipaddr='1.2.3.4',
            netmask='255.255.0.0',
            gateway='1.2.3.256',
        )

    def test_add_net_bad_format_addresses_fails(self, adapter, switch, nic0):
        self._test_invalid_ip_config_fails(
            adapter, switch, nic0, ipaddr='1.2.3.4.5', netmask='255.255.0.0'
        )
        self._test_invalid_ip_config_fails(
            adapter, switch, nic0, ipaddr='1.2.3', netmask='255.255.0.0'
        )

    def _test_invalid_ip_config_fails(self, adapter, switch, nic, **ip_config):
        ip_config.update(switch=switch, nic=nic)
        with pytest.raises(SetupNetworksError) as err:
            with adapter.setupNetworks({NETWORK_NAME: ip_config}, {}, NOCHK):
                pass
        assert err.value.status == ne.ERR_BAD_ADDR
