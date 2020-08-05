#
# Copyright 2012-2020 Red Hat, Inc.
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
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA
# 02110-1301  USA
#
# Refer to the README and COPYING files for full details of the license
#

import ipaddress
import os

import pytest

from vdsm.network import ipwrapper
from vdsm.network import sysctl
from vdsm.network.link import nic
from vdsm.network.link.bond import Bond
from vdsm.network.link.bond.sysfs_driver import BONDING_MASTERS
from vdsm.network.link.iface import random_iface_name
from vdsm.network.netinfo import addresses, bonding, nics, routes
from vdsm.network.netlink import waitfor

from network.compat import mock

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


running_on_ovirt_ci = 'OVIRT_CI' in os.environ
running_on_travis_ci = 'TRAVIS_CI' in os.environ
ipv6_broken_on_travis_ci = pytest.mark.skipif(
    running_on_travis_ci, reason='IPv6 not supported on travis'
)


@pytest.fixture
def nic0():
    with dummy_device() as nic:
        yield nic


@pytest.fixture
def dynamic_ipv6_iface():
    if running_on_ovirt_ci:
        pytest.skip('Using dnsmasq for ipv6 RA is unstable on CI')

    with veth_pair() as (server, client):
        ipwrapper.addrAdd(server, IPV6_ADDR1, IPV6_PREFIX_LENGTH, family=6)
        ipwrapper.linkSet(server, ['up'])
        with dnsmasq_run(server, ipv6_slaac_prefix=IPV6_NET_ADDR):
            with wait_for_ipv6(client):
                ipwrapper.linkSet(client, ['up'])
            yield client


class TestNetinfo(object):
    def test_speed_on_an_iface_that_does_not_support_speed(self):
        assert nic.speed('lo') == 0

    def test_speed_in_range(self):
        for d in nics.nics():
            s = nic.speed(d)
            assert not s < 0
            assert s in ETHTOOL_SPEEDS or s == 0

    @mock.patch.object(ipwrapper.Link, '_fakeNics', ['veth_*', 'dummy_*'])
    def test_fake_nics(self):
        with veth_pair() as (v1a, v1b):
            with dummy_device() as d1:
                fakes = set([d1, v1a, v1b])
                _nics = nics.nics()
            errmsg = 'Fake devices {} are not listed in nics {}'
            assert fakes.issubset(_nics), errmsg.format(fakes, _nics)

        with veth_pair(prefix='mehv_') as (v2a, v2b):
            with dummy_device(prefix='mehd_') as d2:
                hiddens = set([d2, v2a, v2b])
                _nics = nics.nics()
            errmsg = 'Some of hidden devices {} is shown in nics {}'
            assert not hiddens.intersection(_nics), errmsg.format(
                hiddens, _nics
            )

    @pytest.mark.xfail(
        condition=running_on_ovirt_ci,
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
                    bonding.BONDING_OPT % (bond_name, 'miimon'), 'w'
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

    @ipv6_broken_on_travis_ci
    def test_ip_info(self, nic0):
        with waitfor.waitfor_ipv4_addr(nic0, address=IPV4_ADDR1_CIDR):
            ipwrapper.addrAdd(nic0, IPV4_ADDR1, IPV4_PREFIX_LENGTH)
        with waitfor.waitfor_ipv4_addr(nic0, address=IPV4_ADDR2_CIDR):
            ipwrapper.addrAdd(nic0, IPV4_ADDR2, IPV4_PREFIX_LENGTH)
        with waitfor.waitfor_ipv6_addr(nic0, address=IPV6_ADDR_CIDR):
            ipwrapper.addrAdd(nic0, IPV6_ADDR, IPV6_PREFIX_LENGTH, family=6)

        # 32 bit addresses are reported slashless by netlink
        with waitfor.waitfor_ipv4_addr(nic0, address=IPV4_ADDR3):
            ipwrapper.addrAdd(nic0, IPV4_ADDR3, 32)

        assert addresses.getIpInfo(nic0) == (
            IPV4_ADDR1,
            IPV4_NETMASK,
            [IPV4_ADDR1_CIDR, IPV4_ADDR2_CIDR, IPV4_ADDR3_CIDR],
            [IPV6_ADDR_CIDR],
        )
        assert addresses.getIpInfo(nic0, ipv4_gateway=IPV4_GATEWAY1) == (
            IPV4_ADDR1,
            IPV4_NETMASK,
            [IPV4_ADDR1_CIDR, IPV4_ADDR2_CIDR, IPV4_ADDR3_CIDR],
            [IPV6_ADDR_CIDR],
        )
        assert addresses.getIpInfo(nic0, ipv4_gateway=IPV4_GATEWAY2) == (
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
                marks=ipv6_broken_on_travis_ci,
            ),
        ],
    )
    def test_routes_device_to(self, ip_addr, ip_netmask, nic0):
        addr_in_net = ipaddress.ip_address(ip_addr) + 1
        ip_version = addr_in_net.version

        ipwrapper.addrAdd(nic0, ip_addr, ip_netmask, family=ip_version)
        try:
            ipwrapper.linkSet(nic0, ['up'])
            assert routes.getRouteDeviceTo(str(addr_in_net)) == nic0
        finally:
            ipwrapper.addrFlush(nic0, ip_version)


class TestIPv6Addresses(object):
    def test_local_auto_when_ipv6_is_disabled(self, nic0):
        sysctl.disable_ipv6(nic0)
        assert not addresses.is_ipv6_local_auto(nic0)

    @ipv6_broken_on_travis_ci
    def test_local_auto_without_router_advertisement_server(self, nic0):
        assert addresses.is_ipv6_local_auto(nic0)

    @ipv6_broken_on_travis_ci
    def test_local_auto_with_static_address_without_ra_server(self, nic0):
        ipwrapper.addrAdd(nic0, '2001::88', IPV6_PREFIX_LENGTH, family=6)
        ip_addrs = addresses.getIpAddrs()[nic0]
        assert addresses.is_ipv6_local_auto(nic0)
        assert 2 == len(ip_addrs), ip_addrs
        assert addresses.is_ipv6(ip_addrs[0])
        assert not addresses.is_dynamic(ip_addrs[0])

    @ipv6_broken_on_travis_ci
    def test_local_auto_with_dynamic_address_from_ra(self, dynamic_ipv6_iface):
        # Expecting link and global addresses on client iface
        # The addresses are given randomly, so we sort them
        ip_addrs = sorted(
            addresses.getIpAddrs()[dynamic_ipv6_iface],
            key=lambda ip: ip['address'],
        )
        assert len(ip_addrs) == 2

        assert addresses.is_dynamic(ip_addrs[0])
        assert ip_addrs[0]['scope'] == 'global'
        assert ip_addrs[0]['address'].startswith(IPV6_ADDR_BASE)

        assert ip_addrs[1]['scope'] == 'link'
