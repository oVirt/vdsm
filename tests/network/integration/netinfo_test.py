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

from __future__ import absolute_import
from __future__ import division

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

# FIXME: Remove when the tests will use a pytest format (and not a nose one).
is_bonding_available = False


@pytest.fixture(scope='module', autouse=True)
def bonding_available(bond_module):
    global is_bonding_available
    is_bonding_available = bond_module


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
    def test_get_bonding_options(self):
        INTERVAL = '12345'
        bondName = random_iface_name()

        if not is_bonding_available:
            pytest.skip('Bonding is not available')

        with open(BONDING_MASTERS, 'w') as bonds:
            bonds.write('+' + bondName)
            bonds.flush()

            try:  # no error is anticipated but let's make sure we can clean up
                assert self._bond_opts_without_mode(bondName) == {}, (
                    'This test fails when a new bonding option is added to '
                    'the kernel. Please run vdsm-tool dump-bonding-options` '
                    'and retest.'
                )

                with open(
                    bonding.BONDING_OPT % (bondName, 'miimon'), 'w'
                ) as opt:
                    opt.write(INTERVAL)

                assert self._bond_opts_without_mode(bondName) == {
                    'miimon': INTERVAL
                }

            finally:
                bonds.write('-' + bondName)

    @staticmethod
    def _bond_opts_without_mode(bond_name):
        opts = Bond(bond_name).options
        opts.pop('mode')
        return opts

    @ipv6_broken_on_travis_ci
    def test_ip_info(self):
        with dummy_device() as device:
            with waitfor.waitfor_ipv4_addr(device, address=IPV4_ADDR1_CIDR):
                ipwrapper.addrAdd(device, IPV4_ADDR1, IPV4_PREFIX_LENGTH)
            with waitfor.waitfor_ipv4_addr(device, address=IPV4_ADDR2_CIDR):
                ipwrapper.addrAdd(device, IPV4_ADDR2, IPV4_PREFIX_LENGTH)
            with waitfor.waitfor_ipv6_addr(device, address=IPV6_ADDR_CIDR):
                ipwrapper.addrAdd(
                    device, IPV6_ADDR, IPV6_PREFIX_LENGTH, family=6
                )

            # 32 bit addresses are reported slashless by netlink
            with waitfor.waitfor_ipv4_addr(device, address=IPV4_ADDR3):
                ipwrapper.addrAdd(device, IPV4_ADDR3, 32)

            assert addresses.getIpInfo(device) == (
                IPV4_ADDR1,
                IPV4_NETMASK,
                [IPV4_ADDR1_CIDR, IPV4_ADDR2_CIDR, IPV4_ADDR3_CIDR],
                [IPV6_ADDR_CIDR],
            )
            assert addresses.getIpInfo(device, ipv4_gateway=IPV4_GATEWAY1) == (
                IPV4_ADDR1,
                IPV4_NETMASK,
                [IPV4_ADDR1_CIDR, IPV4_ADDR2_CIDR, IPV4_ADDR3_CIDR],
                [IPV6_ADDR_CIDR],
            )
            assert addresses.getIpInfo(device, ipv4_gateway=IPV4_GATEWAY2) == (
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
    def test_routes_device_to(self, ip_addr, ip_netmask):
        with dummy_device() as nic:
            addr_in_net = ipaddress.ip_address(ip_addr) + 1
            ip_version = addr_in_net.version

            ipwrapper.addrAdd(nic, ip_addr, ip_netmask, family=ip_version)
            try:
                ipwrapper.linkSet(nic, ['up'])
                assert routes.getRouteDeviceTo(str(addr_in_net)) == nic
            finally:
                ipwrapper.addrFlush(nic, ip_version)


class TestIPv6Addresses(object):
    def test_local_auto_when_ipv6_is_disabled(self):
        with dummy_device() as dev:
            sysctl.disable_ipv6(dev)
            assert not addresses.is_ipv6_local_auto(dev)

    @ipv6_broken_on_travis_ci
    def test_local_auto_without_router_advertisement_server(self):
        with dummy_device() as dev:
            assert addresses.is_ipv6_local_auto(dev)

    @ipv6_broken_on_travis_ci
    def test_local_auto_with_static_address_without_ra_server(self):
        with dummy_device() as dev:
            ipwrapper.addrAdd(dev, '2001::88', '64', family=6)
            ip_addrs = addresses.getIpAddrs()[dev]
            assert addresses.is_ipv6_local_auto(dev)
            assert 2 == len(ip_addrs), ip_addrs
            assert addresses.is_ipv6(ip_addrs[0])
            assert not addresses.is_dynamic(ip_addrs[0])

    @pytest.mark.skipif(
        running_on_ovirt_ci,
        reason='Using dnsmasq for ipv6 RA is unstable on CI',
    )
    @ipv6_broken_on_travis_ci
    def test_local_auto_with_dynamic_address_from_ra(self):
        IPV6_NETADDRESS = '2001:1:1:1'
        IPV6_NETPREFIX_LEN = '64'
        with veth_pair() as (server, client):
            ipwrapper.addrAdd(
                server, IPV6_NETADDRESS + '::1', IPV6_NETPREFIX_LEN, family=6
            )
            ipwrapper.linkSet(server, ['up'])
            with dnsmasq_run(server, ipv6_slaac_prefix=IPV6_NETADDRESS + '::'):
                with wait_for_ipv6(client):
                    ipwrapper.linkSet(client, ['up'])

                # Expecting link and global addresses on client iface
                # The addresses are given randomly, so we sort them
                ip_addrs = sorted(
                    addresses.getIpAddrs()[client],
                    key=lambda ip: ip['address'],
                )
                assert 2, len(ip_addrs) == ip_addrs

                assert addresses.is_dynamic(ip_addrs[0])
                assert 'global' == ip_addrs[0]['scope']
                ip_address = ip_addrs[0]['address'][: len(IPV6_NETADDRESS)]
                assert IPV6_NETADDRESS == ip_address

                assert 'link' == ip_addrs[1]['scope']
