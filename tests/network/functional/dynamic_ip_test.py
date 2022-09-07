# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from contextlib import contextmanager

import pytest

from vdsm.network import api as net_api
from vdsm.network.initializer import init_unpriviliged_dhcp_monitor_ctx

from . import netfunctestlib as nftestlib
from .netfunctestlib import NOCHK
from .netfunctestlib import parametrize_def_route
from network.nettestlib import Interface
from network.nettestlib import IpFamily
from network.nettestlib import veth_pair
from network.nettestlib import dnsmasq_run
from network.nettestlib import parametrize_ip_families
from network.nettestlib import running_on_ovirt_ci
from network.nettestlib import vlan_device
from network.nettestlib import wait_for_ipv4
from network.nettestlib import wait_for_ipv6

NETWORK_NAME = 'test-network'
VLAN = 10

IPv4_ADDRESS = '192.0.3.1'
IPv4_ADDRESS2 = '192.0.15.1'
IPv4_PREFIX_LEN = '24'
IPv6_ADDRESS = 'fdb3:84e5:4ff4:55e3::1'
IPv6_ADDRESS2 = 'fdb3:84e5:4ff4:77e3::1'
IPv6_CIDR = '64'
IPv4_NETMASK = '255.255.255.0'
IPv6_ADDRESS_AND_PREFIX_LEN = IPv6_ADDRESS2 + '/' + IPv6_CIDR

DHCPv4_RANGE_FROM = '192.0.3.2'
DHCPv4_RANGE_TO = '192.0.3.253'
DHCPv6_RANGE_FROM = 'fdb3:84e5:4ff4:55e3::a'
DHCPv6_RANGE_TO = 'fdb3:84e5:4ff4:55e3::64'

IPv4_DNS = ['1.1.1.1', '2.2.2.2']


unstable_dhcpv6_on_ovirt_ci = pytest.mark.xfail(
    reason='Unstable DHCPv6 response on oVirt CI',
    raises=nftestlib.MissingDynamicIPv6Address,
    strict=False,
    condition=running_on_ovirt_ci(),
)


class NetworkIPConfig(object):
    def __init__(
        self,
        name,
        ipv4_address=None,
        ipv4_prefix_length=None,
        ipv6_address=None,
        ipv6_prefix_length=None,
    ):
        self.name = name
        self.ipv4_address = ipv4_address
        self.ipv4_prefix_length = ipv4_prefix_length
        self.ipv6_address = ipv6_address
        self.ipv6_prefix_length = ipv6_prefix_length


class DhcpConfig(object):
    def __init__(
        self,
        ipv4_range_from,
        ipv4_range_to,
        ipv6_range_from=None,
        ipv6_range_to=None,
    ):
        self.ipv4_range_from = ipv4_range_from
        self.ipv4_range_to = ipv4_range_to
        self.ipv6_range_from = ipv6_range_from
        self.ipv6_range_to = ipv6_range_to


@pytest.fixture(scope='module', autouse=True)
def dhcp_monitor():
    event_sink = FakeNotifier()
    with init_unpriviliged_dhcp_monitor_ctx(event_sink, net_api):
        yield


@pytest.fixture(scope='module', autouse=True)
def network_configuration1():
    yield NetworkIPConfig(
        NETWORK_NAME,
        ipv4_address=IPv4_ADDRESS,
        ipv4_prefix_length=IPv4_PREFIX_LEN,
    )


@pytest.fixture(scope='module', autouse=True)
def network_configuration2():
    yield NetworkIPConfig(
        'test-network-2',
        ipv4_address=IPv4_ADDRESS2,
        ipv4_prefix_length=IPv4_PREFIX_LEN,
    )


@pytest.fixture(scope='module', autouse=True)
def network_configuration_ipv6():
    yield NetworkIPConfig(
        NETWORK_NAME, ipv6_address=IPv6_ADDRESS, ipv6_prefix_length=IPv6_CIDR
    )


@pytest.fixture(scope='module', autouse=True)
def network_configuration_ipv4_and_ipv6():
    yield NetworkIPConfig(
        NETWORK_NAME,
        ipv4_address=IPv4_ADDRESS,
        ipv4_prefix_length=IPv4_PREFIX_LEN,
        ipv6_address=IPv6_ADDRESS,
        ipv6_prefix_length=IPv6_CIDR,
    )


@pytest.fixture
def dynamic_ipv4_iface1(network_configuration1):
    dhcp_config = DhcpConfig(DHCPv4_RANGE_FROM, DHCPv4_RANGE_TO)
    with _create_configured_dhcp_client_iface(
        network_configuration1, dhcp_config
    ) as configured_client:
        yield configured_client


@pytest.fixture
def dynamic_ipv4_iface2(network_configuration2):
    dhcp_config = DhcpConfig('192.0.15.2', '192.0.15.253')
    with _create_configured_dhcp_client_iface(
        network_configuration2, dhcp_config
    ) as configured_client:
        yield configured_client


@pytest.fixture
def dynamic_ipv6_iface(network_configuration_ipv6):
    dhcp_config = DhcpConfig(None, None, DHCPv6_RANGE_FROM, DHCPv6_RANGE_TO)
    with _create_configured_dhcp_client_iface(
        network_configuration_ipv6, dhcp_config
    ) as configured_client:
        yield configured_client


@pytest.fixture
def dynamic_vlaned_ipv4_iface_with_dhcp_server(network_configuration1):
    dhcp_config = DhcpConfig(DHCPv4_RANGE_FROM, DHCPv4_RANGE_TO)
    with _create_configured_dhcp_client_iface(
        network_configuration1, dhcp_config, vlan_id=VLAN
    ) as configured_client:
        yield configured_client


@pytest.fixture
def dynamic_vlaned_ipv4_iface_without_dhcp_server(network_configuration1):
    dhcp_config = DhcpConfig(DHCPv4_RANGE_FROM, DHCPv4_RANGE_TO)
    with _create_configured_dhcp_client_iface(
        network_configuration1, dhcp_config, vlan_id=VLAN, start_dhcp=False
    ) as configured_client:
        yield configured_client


@pytest.fixture
def dynamic_ipv4_ipv6_iface_with_dhcp_server(
    network_configuration_ipv4_and_ipv6,
):
    dhcp_config = DhcpConfig(
        DHCPv4_RANGE_FROM, DHCPv4_RANGE_TO, DHCPv6_RANGE_FROM, DHCPv6_RANGE_TO
    )
    with _create_configured_dhcp_client_iface(
        network_configuration_ipv4_and_ipv6, dhcp_config
    ) as configured_client:
        yield configured_client


@pytest.fixture
def dynamic_ipv4_ipv6_iface_without_dhcp_server(
    network_configuration_ipv4_and_ipv6,
):
    dhcp_config = DhcpConfig(
        DHCPv4_RANGE_FROM, DHCPv4_RANGE_TO, DHCPv6_RANGE_FROM, DHCPv6_RANGE_TO
    )
    with _create_configured_dhcp_client_iface(
        network_configuration_ipv4_and_ipv6, dhcp_config, start_dhcp=False
    ) as configured_client:
        yield configured_client


class FakeNotifier:
    def notify(self, event_id, params=None):
        pass


@nftestlib.parametrize_switch
class TestNetworkDhcpBasic(object):
    @parametrize_ip_families
    @nftestlib.parametrize_bridged
    @parametrize_def_route
    @unstable_dhcpv6_on_ovirt_ci
    def test_add_net_with_dhcp(
        self,
        adapter,
        switch,
        families,
        bridged,
        def_route,
        dynamic_ipv4_ipv6_iface_with_dhcp_server,
    ):
        if families == (IpFamily.IPv6,) and def_route:
            pytest.skip(
                'Skipping default route + dynamic with IPv6 '
                'see https://bugzilla.redhat.com/1467332'
            )
        client = dynamic_ipv4_ipv6_iface_with_dhcp_server
        network_attrs = {
            'bridged': bridged,
            'nic': client,
            'blockingdhcp': True,
            'switch': switch,
            'defaultRoute': def_route,
        }

        if IpFamily.IPv4 in families:
            network_attrs['bootproto'] = 'dhcp'
        if IpFamily.IPv6 in families:
            network_attrs['dhcpv6'] = True

        netcreate = {NETWORK_NAME: network_attrs}

        with adapter.setupNetworks(netcreate, {}, NOCHK):
            adapter.assertNetworkIp(NETWORK_NAME, netcreate[NETWORK_NAME])

    @parametrize_ip_families
    @unstable_dhcpv6_on_ovirt_ci
    def test_move_nic_between_bridgeless_and_bridged_keep_ip(
        self,
        adapter,
        switch,
        families,
        dynamic_ipv4_ipv6_iface_with_dhcp_server,
    ):
        client = dynamic_ipv4_ipv6_iface_with_dhcp_server
        network_attrs = {
            'bridged': False,
            'nic': client,
            'blockingdhcp': True,
            'switch': switch,
        }

        if IpFamily.IPv4 in families:
            network_attrs['bootproto'] = 'dhcp'
        if IpFamily.IPv6 in families:
            network_attrs['dhcpv6'] = True

        netcreate = {NETWORK_NAME: network_attrs}

        with adapter.setupNetworks(netcreate, {}, NOCHK):
            adapter.assertNetworkIp(NETWORK_NAME, netcreate[NETWORK_NAME])
            client_info = adapter.netinfo.nics[client]
            ipv4_addr = client_info['ipv4addrs']
            ipv6_addr = client_info['ipv6addrs']

            network_attrs['bridged'] = True
            adapter.setupNetworks(netcreate, {}, NOCHK)
            adapter.assertNetworkIp(NETWORK_NAME, netcreate[NETWORK_NAME])
            network_info = adapter.netinfo.networks[NETWORK_NAME]
            assert ipv4_addr == network_info['ipv4addrs']
            assert ipv6_addr == network_info['ipv6addrs']


@nftestlib.parametrize_switch
def test_default_route_of_two_dynamic_ip_networks(
    adapter,
    switch,
    network_configuration1,
    network_configuration2,
    dynamic_ipv4_iface1,
    dynamic_ipv4_iface2,
):

    net1 = {
        'bridged': True,
        'nic': dynamic_ipv4_iface1,
        'bootproto': 'dhcp',
        'switch': switch,
        'blockingdhcp': True,
        'defaultRoute': True,
    }
    net2 = {
        'bridged': False,
        'nic': dynamic_ipv4_iface2,
        'bootproto': 'dhcp',
        'switch': switch,
        'blockingdhcp': True,
        'defaultRoute': False,
    }

    with adapter.setupNetworks({network_configuration1.name: net1}, {}, NOCHK):
        read_network1 = adapter.netinfo.networks[network_configuration1.name]
        adapter.assertNetworkIp(network_configuration1.name, net1)

        with adapter.setupNetworks(
            {network_configuration2.name: net2}, {}, NOCHK
        ):
            assert not adapter.netinfo.networks[network_configuration2.name][
                'ipv4defaultroute'
            ]
            assert read_network1['ipv4defaultroute']


@parametrize_ip_families
@nftestlib.parametrize_bridged
@nftestlib.parametrize_switch
@unstable_dhcpv6_on_ovirt_ci
def test_dynamic_ip_switch_to_static_without_running_dhcp_server(
    adapter,
    switch,
    families,
    bridged,
    dynamic_ipv4_ipv6_iface_without_dhcp_server,
):
    _test_dynamic_ip_switch_to_static(
        adapter,
        switch,
        families,
        bridged,
        is_dhcp_server_enabled=False,
        nic=dynamic_ipv4_ipv6_iface_without_dhcp_server,
    )


@parametrize_ip_families
@nftestlib.parametrize_bridged
@nftestlib.parametrize_switch
@unstable_dhcpv6_on_ovirt_ci
def test_dynamic_ip_switch_to_static_with_running_dhcp_server(
    adapter,
    switch,
    families,
    bridged,
    dynamic_ipv4_ipv6_iface_with_dhcp_server,
):
    _test_dynamic_ip_switch_to_static(
        adapter,
        switch,
        families,
        bridged,
        is_dhcp_server_enabled=True,
        nic=dynamic_ipv4_ipv6_iface_with_dhcp_server,
    )


@nftestlib.parametrize_bridged
@nftestlib.parametrize_switch
def test_dynamic_ipv4_vlan_net_switch_to_static_without_running_dhcp_server(
    adapter, switch, bridged, dynamic_vlaned_ipv4_iface_without_dhcp_server
):
    families = (IpFamily.IPv4,)
    _test_dynamic_ip_switch_to_static(
        adapter,
        switch,
        families,
        bridged,
        is_dhcp_server_enabled=False,
        nic=dynamic_vlaned_ipv4_iface_without_dhcp_server,
        vlan=VLAN,
    )


@nftestlib.parametrize_bridged
@nftestlib.parametrize_switch
def test_dynamic_ipv4_vlan_net_switch_to_static_with_running_dhcp_server(
    adapter, switch, bridged, dynamic_vlaned_ipv4_iface_with_dhcp_server
):
    families = (IpFamily.IPv4,)
    _test_dynamic_ip_switch_to_static(
        adapter,
        switch,
        families,
        bridged,
        is_dhcp_server_enabled=True,
        nic=dynamic_vlaned_ipv4_iface_with_dhcp_server,
        vlan=VLAN,
    )


@nftestlib.parametrize_bridged
@nftestlib.parametrize_legacy_switch
def test_add_static_dns_with_dhcp(
    adapter, dynamic_ipv4_iface1, switch, bridged
):
    network_attrs = {
        'bridged': bridged,
        'nic': dynamic_ipv4_iface1,
        'blockingdhcp': True,
        'switch': switch,
        'defaultRoute': True,
        'bootproto': 'dhcp',
        'nameservers': IPv4_DNS,
    }
    netcreate = {NETWORK_NAME: network_attrs}

    with adapter.setupNetworks(netcreate, {}, NOCHK):
        adapter.assertNetworkIp(NETWORK_NAME, netcreate[NETWORK_NAME])


def _test_dynamic_ip_switch_to_static(
    adapter, switch, families, bridged, is_dhcp_server_enabled, nic, vlan=None
):
    network_attrs = {
        'bridged': bridged,
        'nic': nic,
        'blockingdhcp': is_dhcp_server_enabled,
        'switch': switch,
    }
    if vlan is not None:
        network_attrs['vlan'] = vlan
    has_ipv4 = IpFamily.IPv4 in families
    has_ipv6 = IpFamily.IPv6 in families
    if has_ipv4:
        network_attrs['bootproto'] = 'dhcp'
    if has_ipv6:
        network_attrs['dhcpv6'] = True
    netcreate = {NETWORK_NAME: network_attrs}
    with adapter.setupNetworks(netcreate, {}, NOCHK):
        adapter.assertNetworkIp(
            NETWORK_NAME,
            netcreate[NETWORK_NAME],
            ignore_ip=not is_dhcp_server_enabled,
        )
        if has_ipv4:
            network_attrs['bootproto'] = 'none'
            network_attrs['ipaddr'] = IPv4_ADDRESS2
            network_attrs['netmask'] = IPv4_NETMASK
        if has_ipv6:
            network_attrs['dhcpv6'] = False
            network_attrs['ipv6autoconf'] = False
            network_attrs['ipv6addr'] = IPv6_ADDRESS_AND_PREFIX_LEN
        adapter.setupNetworks(netcreate, {}, NOCHK)
        adapter.assertNetworkIp(NETWORK_NAME, netcreate[NETWORK_NAME])


@nftestlib.parametrize_switch
def test_dynamic_ip_bonded_vlanned_network(
    adapter, switch, dynamic_vlaned_ipv4_iface_with_dhcp_server
):
    bond_name = 'bond0'
    network_attrs = {
        'bridged': True,
        'bonding': bond_name,
        'bootproto': 'dhcp',
        'blockingdhcp': True,
        'switch': switch,
        'vlan': VLAN,
    }
    bondcreate = {
        bond_name: {
            'nics': [dynamic_vlaned_ipv4_iface_with_dhcp_server],
            'switch': switch,
        }
    }
    netcreate = {NETWORK_NAME: network_attrs}
    with adapter.setupNetworks(netcreate, bondcreate, NOCHK):
        adapter.assertNetworkIp(NETWORK_NAME, netcreate[NETWORK_NAME])


@nftestlib.parametrize_switch
@unstable_dhcpv6_on_ovirt_ci
def test_dynamic_ip_bonded_network(
    adapter, switch, dynamic_ipv4_ipv6_iface_with_dhcp_server
):
    bond_name = 'bond0'
    network_attrs = {
        'bridged': False,
        'bonding': bond_name,
        'bootproto': 'dhcp',
        'blockingdhcp': True,
        'dhcpv6': True,
        'switch': switch,
    }
    bondcreate = {
        bond_name: {
            'nics': [dynamic_ipv4_ipv6_iface_with_dhcp_server],
            'switch': switch,
        }
    }
    netcreate = {NETWORK_NAME: network_attrs}
    with adapter.setupNetworks(netcreate, bondcreate, NOCHK):
        adapter.assertNetworkIp(NETWORK_NAME, netcreate[NETWORK_NAME])


@contextmanager
def _create_configured_dhcp_client_iface(
    network_config, dhcp_config, vlan_id=None, start_dhcp=True
):
    with _create_dhcp_client_server_peers(network_config, vlan_id) as (
        server,
        client,
    ):
        if start_dhcp:
            dhcp_server = dnsmasq_run(
                server,
                dhcp_config.ipv4_range_from,
                dhcp_config.ipv4_range_to,
                dhcp_config.ipv6_range_from,
                dhcp_config.ipv6_range_to,
                router=network_config.ipv4_address,
            )
            with dhcp_server:
                yield client
        else:
            yield client


@contextmanager
def _create_dhcp_client_server_peers(network_config, vlan_id):
    with veth_pair(max_length=10) as (server, client):
        if vlan_id:
            with vlan_device(server, vlan_id) as vlan_iface:
                _configure_iface_ip(vlan_iface, network_config)
                yield vlan_iface, client
        else:
            _configure_iface_ip(server, network_config)
            yield server, client


def _configure_iface_ip(iface_name, network_config):
    iface = Interface.from_existing_dev_name(iface_name)
    if network_config.ipv4_address:
        with wait_for_ipv4(
            iface_name,
            network_config.ipv4_address,
            network_config.ipv4_prefix_length,
        ):
            iface.add_ip(
                network_config.ipv4_address,
                network_config.ipv4_prefix_length,
                IpFamily.IPv4,
            )
    if network_config.ipv6_address:
        with wait_for_ipv6(
            iface_name,
            network_config.ipv6_address,
            network_config.ipv6_prefix_length,
        ):
            iface.add_ip(
                network_config.ipv6_address,
                network_config.ipv6_prefix_length,
                IpFamily.IPv6,
            )
