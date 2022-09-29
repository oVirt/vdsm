# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

import ipaddress
from unittest import mock

import pytest

from vdsm.network import ipwrapper
from vdsm.network import netinfo
from vdsm.network import sysctl
from vdsm.network.link import nic
from vdsm.network.link.bond import Bond
from vdsm.network.link.bond.sysfs_driver import BONDING_MASTERS
from vdsm.network.link.iface import random_iface_name
from vdsm.network.netlink import waitfor

from network.nettestlib import Interface
from network.nettestlib import IpFamily
from network.nettestlib import running_on_ovirt_ci

from ..nettestlib import dnsmasq_run, dummy_device, veth_pair, wait_for_ipv6

IPV4_ADDR1 = '192.168.99.2'
IPV4_GATEWAY1 = '192.168.99.1'
IPV4_ADDR2 = '192.168.199.2'
IPV4_GATEWAY2 = '192.168.199.1'
IPV4_ADDR3 = '192.168.200.2'
IPV4_NETMASK = '255.255.255.0'
IPV4_PREFIX_LENGTH = 24
IPV6_ADDR = '2607:f0d0:1002:51::4'
IPV6_ADDR_BASE = '2001:1:1:1'
IPV6_ADDR1 = f'{IPV6_ADDR_BASE}::1'
IPV6_NET_ADDR = f'{IPV6_ADDR_BASE}::'
IPV6_PREFIX_LENGTH = 64

IPV4_ADDR1_CIDR = f'{IPV4_ADDR1}/{IPV4_PREFIX_LENGTH}'
IPV4_ADDR2_CIDR = f'{IPV4_ADDR2}/{IPV4_PREFIX_LENGTH}'
IPV4_ADDR3_CIDR = f'{IPV4_ADDR3}/{32}'
IPV6_ADDR_CIDR = f'{IPV6_ADDR}/{IPV6_PREFIX_LENGTH}'

# speeds defined in ethtool
ETHTOOL_SPEEDS = set([10, 100, 1000, 2500, 10000])


@pytest.fixture
def nic0():
    with dummy_device() as nic:
        yield nic


@pytest.fixture
def dynamic_ipv6_iface():
    if running_on_ovirt_ci():
        pytest.skip('Using dnsmasq for ipv6 RA is unstable on CI')

    with veth_pair() as (server, client):
        with wait_for_ipv6(server, IPV6_ADDR1, IPV6_PREFIX_LENGTH):
            Interface.from_existing_dev_name(server).add_ip(
                IPV6_ADDR1, IPV6_PREFIX_LENGTH, IpFamily.IPv6
            )
        client_interface = Interface.from_existing_dev_name(client)
        client_interface.down()
        with dnsmasq_run(server, ipv6_slaac_prefix=IPV6_NET_ADDR):
            with wait_for_ipv6(client):
                client_interface.up()
            yield client


class TestNetinfo(object):
    def test_speed_on_an_iface_that_does_not_support_speed(self):
        assert nic.speed('lo') == 0

    def test_speed_in_range(self):
        for d in ipwrapper.visible_nics():
            s = nic.speed(d)
            assert not s < 0
            assert s in ETHTOOL_SPEEDS or s == 0

    @mock.patch.object(ipwrapper.Link, '_fakeNics', ['veth_*', 'dummy_*'])
    def test_fake_nics(self):
        with veth_pair() as (v1a, v1b):
            with dummy_device() as d1:
                fakes = set([d1, v1a, v1b])
                _nics = ipwrapper.visible_nics()
            errmsg = 'Fake devices {} are not listed in nics {}'
            assert fakes.issubset(_nics), errmsg.format(fakes, _nics)

        with veth_pair(prefix='mehv_') as (v2a, v2b):
            with dummy_device(prefix='mehd_') as d2:
                hiddens = set([d2, v2a, v2b])
                _nics = ipwrapper.visible_nics()
            errmsg = 'Some of hidden devices {} is shown in nics {}'
            assert not hiddens.intersection(_nics), errmsg.format(
                hiddens, _nics
            )

    @pytest.mark.xfail(
        condition=running_on_ovirt_ci(),
        raises=AssertionError,
        reason='Bond options scanning is fragile on CI',
        strict=False,
    )
    def test_get_bonding_options(self, bond_module):
        INTERVAL = '12345'
        bond_name = random_iface_name()

        if not bond_module:
            pytest.skip('Bonding is not available')

        with open(BONDING_MASTERS, 'w') as bonds:
            bonds.write('+' + bond_name)
            bonds.flush()

            try:  # no error is anticipated but let's make sure we can clean up
                assert self._bond_opts_without_mode(bond_name) == {}, (
                    'This test fails when a new bonding option is added to '
                    'the kernel. Please run vdsm-tool dump-bonding-options` '
                    'and retest.'
                )

                with open(
                    netinfo.bonding.BONDING_OPT % (bond_name, 'miimon'), 'w'
                ) as opt:
                    opt.write(INTERVAL)

                assert self._bond_opts_without_mode(bond_name) == {
                    'miimon': INTERVAL
                }

            finally:
                bonds.write('-' + bond_name)

    @staticmethod
    def _bond_opts_without_mode(bond_name):
        opts = Bond(bond_name).options
        opts.pop('mode')
        return opts

    def test_ip_info(self, nic0):
        nic0_interface = Interface.from_existing_dev_name(nic0)
        with waitfor.waitfor_ipv4_addr(nic0, address=IPV4_ADDR1_CIDR):
            nic0_interface.add_ip(
                IPV4_ADDR1, IPV4_PREFIX_LENGTH, IpFamily.IPv4
            )
        with waitfor.waitfor_ipv4_addr(nic0, address=IPV4_ADDR2_CIDR):
            nic0_interface.add_ip(
                IPV4_ADDR2, IPV4_PREFIX_LENGTH, IpFamily.IPv4
            )
        with waitfor.waitfor_ipv6_addr(nic0, address=IPV6_ADDR_CIDR):
            nic0_interface.add_ip(IPV6_ADDR, IPV6_PREFIX_LENGTH, IpFamily.IPv6)

        # 32 bit addresses are reported slashless by netlink
        with waitfor.waitfor_ipv4_addr(nic0, address=IPV4_ADDR3):
            nic0_interface.add_ip(IPV4_ADDR3, 32, IpFamily.IPv4)

        assert netinfo.addresses.getIpInfo(nic0) == (
            IPV4_ADDR1,
            IPV4_NETMASK,
            [IPV4_ADDR1_CIDR, IPV4_ADDR2_CIDR, IPV4_ADDR3_CIDR],
            [IPV6_ADDR_CIDR],
        )
        assert netinfo.addresses.getIpInfo(
            nic0, ipv4_gateway=IPV4_GATEWAY1
        ) == (
            IPV4_ADDR1,
            IPV4_NETMASK,
            [IPV4_ADDR1_CIDR, IPV4_ADDR2_CIDR, IPV4_ADDR3_CIDR],
            [IPV6_ADDR_CIDR],
        )
        assert netinfo.addresses.getIpInfo(
            nic0, ipv4_gateway=IPV4_GATEWAY2
        ) == (
            IPV4_ADDR2,
            IPV4_NETMASK,
            [IPV4_ADDR1_CIDR, IPV4_ADDR2_CIDR, IPV4_ADDR3_CIDR],
            [IPV6_ADDR_CIDR],
        )

    @pytest.mark.parametrize(
        "ip_addr, ip_netmask",
        [
            pytest.param(IPV4_ADDR1, IPV4_NETMASK, id="IPV4"),
            pytest.param(
                IPV6_ADDR,
                IPV6_PREFIX_LENGTH,
                id="IPV6",
            ),
        ],
    )
    def test_routes_device_to(self, ip_addr, ip_netmask, nic0):
        addr_in_net = ipaddress.ip_address(ip_addr) + 1
        ip_version = addr_in_net.version

        Interface.from_existing_dev_name(nic0).add_ip(
            ip_addr, ip_netmask, family=ip_version
        )
        assert netinfo.routes.getRouteDeviceTo(str(addr_in_net)) == nic0


class TestIPv6Addresses(object):
    def test_local_auto_when_ipv6_is_disabled(self, nic0):
        sysctl.disable_ipv6(nic0)
        assert not netinfo.addresses.is_ipv6_local_auto(nic0)

    def test_local_auto_without_router_advertisement_server(self, nic0):
        assert netinfo.addresses.is_ipv6_local_auto(nic0)

    def test_local_auto_with_static_address_without_ra_server(self, nic0):
        Interface.from_existing_dev_name(nic0).add_ip(
            '2001::88', IPV6_PREFIX_LENGTH, IpFamily.IPv6
        )
        ip_addrs = netinfo.addresses.getIpAddrs()[nic0]
        assert netinfo.addresses.is_ipv6_local_auto(nic0)
        assert 2 == len(ip_addrs), ip_addrs
        assert netinfo.addresses.is_ipv6(ip_addrs[0])
        assert not netinfo.addresses.is_dynamic(ip_addrs[0])

    def test_local_auto_with_dynamic_address_from_ra(self, dynamic_ipv6_iface):
        # Expecting link and global addresses on client iface
        # The addresses are given randomly, so we sort them
        ip_addrs = sorted(
            netinfo.addresses.getIpAddrs()[dynamic_ipv6_iface],
            key=lambda ip: ip['address'],
        )
        assert len(ip_addrs) == 2

        assert netinfo.addresses.is_dynamic(ip_addrs[0])
        assert ip_addrs[0]['scope'] == 'global'
        assert ip_addrs[0]['address'].startswith(IPV6_ADDR_BASE)

        assert ip_addrs[1]['scope'] == 'link'
