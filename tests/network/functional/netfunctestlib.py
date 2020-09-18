#
# Copyright 2016-2020 Red Hat, Inc.
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

from contextlib import contextmanager
from copy import deepcopy
import ipaddress
import time

import pytest

from vdsm.common import fileutils
from vdsm.network import api
from vdsm.network import errors
from vdsm.network import kernelconfig
from vdsm.network import nmstate
from vdsm.network.canonicalize import bridge_opts_dict_to_sorted_str
from vdsm.network.canonicalize import bridge_opts_str_to_dict
from vdsm.network.cmd import exec_sync
from vdsm.network.dhcp_monitor import MonitoredItemPool
from vdsm.network.ip import dhclient
from vdsm.network.ip.address import ipv6_supported, prefix2netmask
from vdsm.network.ifacetracking import is_tracked as iface_is_tracked
from vdsm.network.link.iface import iface
from vdsm.network.link.bond import sysfs_options as bond_options
from vdsm.network.link.bond import sysfs_options_mapper as bond_opts_mapper
from vdsm.network.link.bond.sysfs_options import getDefaultBondingOptions
from vdsm.network.netconfpersistence import RunningConfig
from vdsm.network.netinfo import bridges
from vdsm.network.netinfo.cache import CachingNetInfo
from vdsm.network.netlink import monitor
from vdsm.network.netlink import waitfor
from vdsm.network.restore_net_config import restore

try:
    from functional.utils import getProxy, SUCCESS
except ImportError:
    # When running without VDSM installed, some dependencies are missing.
    # When running against the lib, there is no need for the full VDSM install.
    getProxy = None
    SUCCESS = 0

NOCHK = {'connectivityCheck': False}
TIMEOUT_CHK = {'connectivityCheck': True, 'connectivityTimeout': 0.1}

IFCFG_DIR = '/etc/sysconfig/network-scripts/'
IFCFG_PREFIX = IFCFG_DIR + 'ifcfg-'


class IpFamily(object):
    IPv4 = 4
    IPv6 = 6


parametrize_switch = pytest.mark.parametrize(
    'switch',
    [
        pytest.param('legacy', marks=pytest.mark.legacy_switch()),
        pytest.param('ovs', marks=pytest.mark.ovs_switch()),
    ],
)

parametrize_legacy_switch = pytest.mark.parametrize(
    'switch', [pytest.param('legacy', marks=pytest.mark.legacy_switch())]
)

parametrize_bridged = pytest.mark.parametrize(
    'bridged', [False, True], ids=['bridgeless', 'bridged']
)

parametrize_bonded = pytest.mark.parametrize(
    'bonded', [False, True], ids=['unbonded', 'bonded']
)

parametrize_ip_families = pytest.mark.parametrize(
    'families',
    [(IpFamily.IPv4,), (IpFamily.IPv6,), (IpFamily.IPv4, IpFamily.IPv6)],
    ids=['IPv4', 'IPv6', 'IPv4&6'],
)

parametrize_def_route = pytest.mark.parametrize(
    'def_route', [True, False], ids=['withDefRoute', 'withoutDefRoute']
)


def is_nmstate_backend():
    return nmstate.is_nmstate_backend()


def retry_assert(func):
    def retry(*args, **kwargs):
        for i in range(4):
            try:
                func(*args, **kwargs)
            except AssertionError:
                time.sleep(1)
            else:
                return
        func(*args, **kwargs)

    return retry


class Target(object):
    SERVICE = 1
    LIB = 0


class LibProxy(object):
    def __init__(self):
        self.netinfo = None
        self.config = None

    def setSafeNetworkConfig(self):
        api.setSafeNetworkConfig()

    def setupNetworks(self, networks, bonds, options):
        try:
            api.setupNetworks(networks, bonds, options)
        except errors.ConfigNetworkError as e:
            status = e.errCode
            msg = e.msg
        else:
            status = SUCCESS
            msg = ''
        finally:
            caps = api.network_caps()
            self.netinfo = CachingNetInfo(caps)
            self.config = RunningConfig()

        return status, msg

    def refreshNetworkCapabilities(self):
        caps = api.network_caps()
        self.netinfo = CachingNetInfo(caps)

    def getNetworkStatistics(self):
        net_stats = api.network_stats()
        return SUCCESS, '', net_stats

    getVdsStats = getNetworkStatistics


class TargetNotDefinedError(Exception):
    pass


class NetFuncTestAdapter(object):
    def __init__(self, target=Target.SERVICE):
        self.netinfo = None
        self.running_config = None
        if target == Target.SERVICE:
            self._vdsm_proxy = getProxy()
        elif target == Target.LIB:
            self._vdsm_proxy = LibProxy()
        else:
            raise TargetNotDefinedError()

    def update_netinfo(self):
        self.netinfo = self._vdsm_proxy.netinfo
        if self.netinfo is None:
            self._vdsm_proxy.refreshNetworkCapabilities()
            self.netinfo = self._vdsm_proxy.netinfo

    def refresh_netinfo(self):
        self._vdsm_proxy.refreshNetworkCapabilities()
        self.netinfo = self._vdsm_proxy.netinfo

    def update_running_config(self):
        self.running_config = self._vdsm_proxy.config

    def refresh_running_config(self):
        self.running_config = RunningConfig()

    def setSafeNetworkConfig(self):
        self._vdsm_proxy.setSafeNetworkConfig()

    @property
    def setupNetworks(self):
        return SetupNetworks(
            self._vdsm_proxy,
            self._update_running_and_kernel_config,
            self._assert_kernel_vs_running_config,
        )

    def restore_nets(self):
        restore(force=True)
        self.refresh_netinfo()
        self.refresh_running_config()

    def getNetworkStatistics(self):
        status, msg, result = self._vdsm_proxy.getVdsStats()
        if status != SUCCESS:
            raise RuntimeError(status, msg)
        return result

    def _update_running_and_kernel_config(self):
        self.update_netinfo()
        self.update_running_config()

    def assertNetwork(self, netname, netattrs):
        """
        Aggregates multiple network checks to ease usage.
        The checks are between the requested setup (input) and current reported
        state (caps).
        """
        self.assertNetworkExists(netname)

        bridged = netattrs.get('bridged', True)
        if bridged:
            self.assertNetworkBridged(netname)
        else:
            self.assertNetworkBridgeless(netname)

        self.assertHostQos(netname, netattrs)

        self.assertNorthboundIface(netname, netattrs)
        self.assertSouthboundIface(netname, netattrs)
        self.assertVlan(netattrs)
        self.assertNetworkIp(netname, netattrs)

        sb_iface_exists = netattrs.get('nic') or netattrs.get('bonding')
        check_admin_state = bridged and not sb_iface_exists
        self.assertLinksUp(
            netname, netattrs, check_oper_state=not check_admin_state
        )
        self.assertNetworkSwitchType(netname, netattrs)
        self.assertNetworkMtu(netname, netattrs)

    def assertHostQos(self, netname, netattrs):
        if 'hostQos' not in netattrs:
            return

        self.assertHostQosOnNet(netname, netattrs)
        self.assertHostQosOnDevice(netattrs)

    def assertHostQosOnNet(self, netname, netattrs):
        network_caps = self.netinfo.networks[netname]
        qos_caps = _normalize_qos_config(network_caps['hostQos'])
        assert netattrs['hostQos'] == qos_caps

    def assertHostQosOnDevice(self, netattrs):
        vlan_id = netattrs.get('vlan', -1)
        host_qos = netattrs['hostQos']
        nic = netattrs.get('nic')
        bond = netattrs.get('bonding')

        if nic:
            dev_qos_caps = self.netinfo.nics[nic]['qos']
        elif bond:
            dev_qos_caps = self.netinfo.bondings[bond]['qos']

        for qos in dev_qos_caps:
            qos['hostQos'] = _normalize_qos_config(qos['hostQos'])
        qos_info = dict(hostQos=host_qos, vlan=vlan_id)
        assert qos_info in dev_qos_caps

    def assertNoQosOnNic(self, iface_name):
        assert 'qos' not in self.netinfo.nics[iface_name]

    def assertNetworkExists(self, netname):
        assert netname in self.netinfo.networks

    def assertNetworkBridged(self, netname):
        network_caps = self.netinfo.networks[netname]
        assert network_caps['bridged']
        assert netname in self.netinfo.bridges

    def assertNetworkBridgeless(self, netname):
        network_caps = self.netinfo.networks[netname]
        assert not network_caps['bridged']
        assert netname not in self.netinfo.bridges

    def assertNorthboundIface(self, netname, netattrs):
        nic = netattrs.get('nic')
        bond = netattrs.get('bonding')
        vlan = netattrs.get('vlan')
        bridged = netattrs.get('bridged', True)

        if bridged:
            iface = netname
        elif vlan is not None:
            iface = '{}.{}'.format(nic or bond, vlan)
        else:
            iface = nic or bond

        network_caps = self.netinfo.networks[netname]
        assert iface == network_caps['iface']

    def assertSouthboundIface(self, netname, netattrs):
        nic = netattrs.get('nic')
        bond = netattrs.get('bonding')
        vlan = netattrs.get('vlan')

        if vlan is not None and netattrs['switch'] == 'legacy':
            sb_iface = '{}.{}'.format(nic or bond, vlan)
        else:
            sb_iface = nic or bond

        network_caps = self.netinfo.networks[netname]
        assert sb_iface == network_caps['southbound']

    def assertVlan(self, netattrs):
        vlan = netattrs.get('vlan')
        if vlan is None:
            return

        nic = netattrs.get('nic')
        bond = netattrs.get('bonding')
        iface = '{}.{}'.format(nic or bond, vlan)

        assert iface in self.netinfo.vlans
        vlan_caps = self.netinfo.vlans[iface]
        assert isinstance(vlan_caps['vlanid'], int)
        assert int(vlan) == vlan_caps['vlanid']

    def assertBridgeOpts(self, netname, netattrs):
        bridge_caps = self.netinfo.bridges[netname]

        stp_request = 'on' if netattrs.get('stp', False) else 'off'
        assert bridge_caps['stp'] == stp_request

        self._assertCustomBridgeOpts(netattrs, bridge_caps)

    def _assertCustomBridgeOpts(self, netattrs, bridge_caps):
        custom_attrs = netattrs.get('custom', {})
        if 'bridge_opts' in custom_attrs:
            req_bridge_opts = dict(
                [
                    opt.split('=', 1)
                    for opt in custom_attrs['bridge_opts'].split(' ')
                ]
            )
            bridge_opts_caps = bridge_caps['opts']
            for br_opt, br_val in req_bridge_opts.items():
                assert br_val == bridge_opts_caps[br_opt]

    def assertNoNetwork(self, netname):
        self.assertNoNetworkExists(netname)
        self.assertNoBridgeExists(netname)
        self.assertNoNetworkExistsInRunning(netname)

    def assertNoNetworkExists(self, net):
        assert net not in self.netinfo.networks

    def assertNoBridgeExists(self, bridge):
        assert bridge not in self.netinfo.bridges

    def assertNoVlan(self, southbound_port, tag):
        vlan_name = '{}.{}'.format(southbound_port, tag)
        assert vlan_name not in self.netinfo.vlans

    def assertNoNetworkExistsInRunning(self, net):
        self.update_running_config()
        assert net not in self.running_config.networks

    def assertNetworkSwitchType(self, netname, netattrs):
        requested_switch = netattrs.get('switch', 'legacy')
        running_switch = self.netinfo.networks[netname]['switch']
        assert requested_switch == running_switch

    def assertNetworkMtu(self, netname, netattrs):
        requested_mtu = netattrs.get('mtu', 1500)
        netinfo = _normalize_caps(self.netinfo)
        running_mtu = netinfo.networks[netname]['mtu']
        assert requested_mtu == running_mtu

    def assertLinkMtu(self, devname, netattrs):
        requested_mtu = netattrs.get('mtu', 1500)
        netinfo = _normalize_caps(self.netinfo)
        if devname in netinfo.nics:
            running_mtu = netinfo.nics[devname]['mtu']
        elif devname in netinfo.bondings:
            running_mtu = netinfo.bondings[devname]['mtu']
        elif devname in netinfo.vlans:
            running_mtu = netinfo.vlans[devname]['mtu']
        elif devname in netinfo.bridges:
            running_mtu = netinfo.bridges[devname]['mtu']
        elif devname in netinfo.networks:
            running_mtu = netinfo.networks[devname]['mtu']
        else:
            raise DeviceNotInCapsError(devname)
        assert requested_mtu == int(running_mtu)

    def assertBond(self, bond, attrs):
        self.assertBondExists(bond)
        self.assertBondSlaves(bond, attrs['nics'])
        if 'options' in attrs:
            self.assertBondOptions(bond, attrs['options'])
        self.assertBondExistsInRunninng(bond, attrs['nics'])
        self.assertBondSwitchType(bond, attrs)
        self.assertBondHwAddress(bond, attrs)

    def assertBondExists(self, bond):
        assert bond in self.netinfo.bondings

    def assertBondSlaves(self, bond, nics):
        assert set(nics) == set(self.netinfo.bondings[bond]['slaves'])

    def assertBondActiveSlaveExists(self, bond, nics):
        assert bond in self.netinfo.bondings
        assert self.netinfo.bondings[bond]['active_slave'] in nics

    def assertBondNoActiveSlaveExists(self, bond):
        assert bond in self.netinfo.bondings
        assert self.netinfo.bondings[bond]['active_slave'] == ''

    def assertBondOptions(self, bond, options):
        requested_opts = _split_bond_options(options)
        running_opts = self.netinfo.bondings[bond]['opts']
        normalized_active_opts = _normalize_bond_opts(running_opts)
        assert set(requested_opts) <= set(normalized_active_opts)

    def assertBondExistsInRunninng(self, bond, nics):
        assert bond in self.running_config.bonds
        assert set(nics) == set(self.running_config.bonds[bond]['nics'])

    def assertBondSwitchType(self, bondname, bondattrs):
        requested_switch = bondattrs.get('switch', 'legacy')
        running_switch = self.netinfo.bondings[bondname]['switch']
        assert requested_switch == running_switch

    def assertBondHwAddress(self, bondname, bondattrs):
        requested_hwaddress = bondattrs.get('hwaddr')
        if requested_hwaddress:
            actual_hwaddress = self.netinfo.bondings[bondname]['hwaddr']
            assert requested_hwaddress == actual_hwaddress

    def assertNoBond(self, bond):
        self.assertNoBondExists(bond)
        self.assertNoBondExistsInRunning(bond)

    def assertNoBondExists(self, bond):
        assert bond not in self.netinfo.bondings

    def assertNoBondExistsInRunning(self, bond):
        self.update_running_config()
        assert bond not in self.running_config.bonds

    def assertLACPConfigured(self, bonds, nics):
        """When LACP is configured on bonds, the aggregator id and
        partner_mac should exist"""
        for bond in bonds:
            assert 'ad_aggregator_id' in self.netinfo.bondings[bond]
            assert 'ad_partner_mac' in self.netinfo.bondings[bond]
        for nic in nics:
            assert 'ad_aggregator_id' in self.netinfo.nics[nic]
            assert self.netinfo.nics[nic]['ad_aggregator_id'] is not None

    def assertNoLACPConfigured(self, bonds, nics):
        """When LACP is not configured on bonds, the aggregator id and
        partner_mac should not exist"""
        for bond in bonds:
            assert 'ad_aggregator_id' not in self.netinfo.bondings[bond]
            assert 'ad_partner_mac' not in self.netinfo.bondings[bond]
        for nic in nics:
            assert 'ad_aggregator_id' not in self.netinfo.nics[nic]

    def assertBondHwaddrToPartnerMac(self, hwaddr_bond, partner_bond):
        bond_caps = self.netinfo.bondings
        bond_hwaddr = bond_caps[hwaddr_bond]['hwaddr']
        bond_ad_partner_mac = bond_caps[partner_bond]['ad_partner_mac']
        assert bond_hwaddr == bond_ad_partner_mac

    def assertNetworkIp(self, net, attrs, ignore_ip=False):
        bridged = attrs.get('bridged', True)
        vlan = attrs.get('vlan')
        bond = attrs.get('bonding')
        nic = attrs.get('nic')
        switch = attrs.get('switch')
        is_valid_attrs = (
            nic is not None or bond is not None or switch is not None
        )
        assert is_valid_attrs

        if _ipv4_is_unused(attrs) and _ipv6_is_unused(attrs):
            return

        network_netinfo = self.netinfo.networks[net]

        if bridged:
            topdev_netinfo = self.netinfo.bridges[net]
        elif vlan is not None:
            vlan_name = '{}.{}'.format(bond or nic, attrs['vlan'])
            topdev_netinfo = self.netinfo.vlans[vlan_name]
        elif bond:
            topdev_netinfo = self.netinfo.bondings[bond]
        else:
            topdev_netinfo = self.netinfo.nics[nic]

        if 'ipaddr' in attrs:
            self.assertStaticIPv4(attrs, network_netinfo)
            self.assertStaticIPv4(attrs, topdev_netinfo)
        if attrs.get('bootproto') == 'dhcp':
            self.assertDHCPv4(network_netinfo, ignore_ip)
            self.assertDHCPv4(topdev_netinfo, ignore_ip)
        if _ipv4_is_unused(attrs):
            self.assertDisabledIPv4(network_netinfo)
            self.assertDisabledIPv4(topdev_netinfo)

        if 'ipv6addr' in attrs:
            self.assertStaticIPv6(attrs, network_netinfo)
            self.assertStaticIPv6(attrs, topdev_netinfo)
        elif attrs.get('dhcpv6'):
            self.assertDHCPv6(network_netinfo, ignore_ip)
            self.assertDHCPv6(topdev_netinfo, ignore_ip)
        elif attrs.get('ipv6autoconf'):
            self.assertIPv6Autoconf(network_netinfo)
            self.assertIPv6Autoconf(topdev_netinfo)
        elif _ipv6_is_unused(attrs):
            self.assertDisabledIPv6(network_netinfo)
            self.assertDisabledIPv6(topdev_netinfo)

        self.assertRoutesIPv4(attrs, network_netinfo, ignore_ip)
        self.assertRoutesIPv4(attrs, topdev_netinfo, ignore_ip)

        self.assertRoutesIPv6(attrs, network_netinfo, ignore_ip)
        self.assertRoutesIPv6(attrs, topdev_netinfo, ignore_ip)

    def assertStaticIPv4(self, netattrs, ipinfo):
        address = netattrs['ipaddr']
        netmask = netattrs.get('netmask') or prefix2netmask(
            int(netattrs.get('prefix'))
        )
        assert address == ipinfo['addr']
        assert netmask == ipinfo['netmask']
        ipv4 = ipaddress.IPv4Interface(u'{}/{}'.format(address, netmask))
        assert str(ipv4.with_prefixlen) in ipinfo['ipv4addrs']

    def assertStaticIPv6(self, netattrs, ipinfo):
        ipv6_address = str(ipaddress.IPv6Interface(str(netattrs['ipv6addr'])))
        assert ipv6_address in ipinfo['ipv6addrs']

    def assertDHCPv4(self, ipinfo, ignore_ip=False):
        assert ipinfo['dhcpv4']
        if not ignore_ip:
            assert ipinfo['addr'] != ''
            assert len(ipinfo['ipv4addrs']) > 0

    def assertDHCPv6(self, ipinfo, ignore_ip=False):
        assert ipinfo['dhcpv6']
        if not ignore_ip:
            length = len(ipinfo['ipv6addrs'])
            if length == 0:
                raise MissingDynamicIPv6Address(
                    f'IPv6 addresses are empty: {ipinfo}'
                )

    def assertIPv6Autoconf(self, ipinfo):
        assert ipinfo['ipv6autoconf']
        assert len(ipinfo['ipv6addrs']) > 0

    def assertDisabledIPv4(self, ipinfo):
        assert not ipinfo['dhcpv4']
        assert ipinfo['addr'] == ''
        assert ipinfo['ipv4addrs'] == []

    def assertDisabledIPv6(self, ipinfo):
        # TODO: We need to report if IPv6 is enabled on iface/host and
        # differentiate that from not acquiring an address.
        assert [] == ipinfo['ipv6addrs']

    def assertDhclient(self, iface, family):
        return dhclient.is_active(iface, family)

    def assertNoDhclient(self, iface, family):
        assert not self.assertDhclient(iface, family)

    def assertRoutesIPv4(self, netattrs, ipinfo, ignore_ip=False):
        # TODO: Support sourceroute on OVS switches
        if netattrs.get('switch', 'legacy') == 'legacy':
            is_dynamic = netattrs.get('bootproto') == 'dhcp'
            if is_dynamic and not ignore_ip:
                # When dynamic is used, route is assumed to be included.
                assert ipinfo['gateway']
            else:
                gateway = netattrs.get('gateway', '')
                assert gateway == ipinfo['gateway']

        self.assertDefaultRouteIPv4(netattrs, ipinfo)

    def assertRoutesIPv6(self, netattrs, ipinfo, ignore_ip=False):
        # TODO: Support sourceroute for IPv6 networks
        if netattrs.get('defaultRoute', False):
            is_dynamic = netattrs.get('ipv6autoconf') or netattrs.get('dhcpv6')
            if is_dynamic and not ignore_ip:
                # When dynamic is used, route is assumed to be included.
                assert ipinfo['ipv6gateway']
            else:
                gateway = netattrs.get('ipv6gateway', '::')
                assert gateway == ipinfo['ipv6gateway']

    def assertDefaultRouteIPv4(self, netattrs, ipinfo):
        # When DHCP is used, route is assumed to be included in the response.
        is_gateway_requested = (
            bool(netattrs.get('gateway'))
            or netattrs.get('bootproto') == 'dhcp'
        )
        is_default_route_requested = (
            netattrs.get('defaultRoute', False) and is_gateway_requested
        )
        assert is_default_route_requested == ipinfo['ipv4defaultroute']

    @retry_assert
    def assertLinksUp(self, net, attrs, check_oper_state=True):
        switch = attrs.get('switch', 'legacy')
        if switch == 'legacy':
            expected_links = _gather_expected_legacy_links(
                net, attrs, self.netinfo
            )
        elif switch == 'ovs':
            expected_links = _gather_expected_ovs_links(
                net, attrs, self.netinfo
            )
        if expected_links:
            for dev in expected_links:
                check_is_up = (
                    iface(dev).is_oper_up
                    if check_oper_state
                    else iface(dev).is_admin_up
                )
                assert check_is_up(), 'Dev {} is DOWN'.format(dev)

    def assertNameservers(self, nameservers):
        assert nameservers == self.netinfo.nameservers[: len(nameservers)]

    def _assert_kernel_vs_running_config(self):
        """
        This is a special test, that checks setup integrity through
        non vdsm api data.
        The networking configuration relies on a semi-persistent running
        configuration files, describing the requested configuration.
        This configuration is checked against the actual caps report.
        """

        running_config = kernelconfig.normalize(self.running_config)
        running_config = running_config.as_unicode()

        netinfo = _normalize_caps(self.netinfo)
        kernel_config = kernelconfig.KernelConfig(netinfo)

        _extend_with_bridge_opts(kernel_config, running_config)
        kernel_config = kernel_config.as_unicode()
        _normalize_bonds((kernel_config, running_config))

        self._assert_inclusive_nameservers(kernel_config, running_config)
        # Do not use KernelConfig.__eq__ to get a better exception if something
        # breaks.
        assert running_config['networks'] == kernel_config['networks']
        if nmstate.is_nmstate_backend():
            self._assert_inclusive_bond_options(kernel_config, running_config)
        assert running_config['bonds'] == kernel_config['bonds']

    def _assert_inclusive_bond_options(self, kernel_config, running_config):
        """
        Assert bond options in an inclusive manner, between the kernel and the
        running config.
        It supports cases where the desired bond options are applied in the
        kernel state, however, additional options exists in the kernel and not
        specified by the desired config.
        The func has a side effect of removing the bond options from the
        provided dicts, allowing further handling of the remaining data.
        """
        r_bonds_opts, k_bonds_opts = self._pop_bonds_options(
            running_config, kernel_config
        )
        for r_opts, k_opts in zip(r_bonds_opts, k_bonds_opts):
            assert r_opts in k_opts

    def _pop_bonds_options(self, running_config, kernel_config):
        r_bonds_opts = []
        k_bonds_opts = []
        for k_name, k_attrs in kernel_config['bonds'].items():
            r_bonds_opts.append(
                running_config['bonds'][k_name].pop('options', '')
            )
            k_bonds_opts.append(k_attrs.pop('options', ''))
        return r_bonds_opts, k_bonds_opts

    def _assert_inclusive_nameservers(self, kernel_config, running_config):
        """
        Assert nameservers in an inclusive manner, between the kernel and the
        running config.
        It supports cases where the desired namerservers options are applied in
        the kernel state, however, additional options exists in the kernel and
        not specified by the desired config.
        The func has a side effect of removing the nameservers from the
        provided dicts, allowing further handling of the remaining data.
        """
        r_nameservers_list, k_nameservers_list = self._pop_nameservers(
            running_config, kernel_config
        )
        for r_nameservers, k_nameservers in zip(
            r_nameservers_list, k_nameservers_list
        ):
            assert r_nameservers == k_nameservers[: len(r_nameservers)]

    def _pop_nameservers(self, running_config, kernel_config):
        r_nameservers_list = []
        k_nameservers_list = []
        for k_name, k_attrs in kernel_config['networks'].items():
            r_nameservers_list.append(
                running_config['networks'][k_name].pop('nameservers', '')
            )
            k_nameservers_list.append(k_attrs.pop('nameservers', ''))
        return r_nameservers_list, k_nameservers_list

    @contextmanager
    def reset_persistent_config(self):
        try:
            yield
        finally:
            self._vdsm_proxy.setSafeNetworkConfig()


def _extend_with_bridge_opts(kernel_config, running_config):
    for net, attrs in running_config['networks'].items():
        if not attrs['bridged']:
            continue
        if net not in kernel_config.networks:
            continue
        running_opts_str = attrs.get('custom', {}).get('bridge_opts')
        if not running_opts_str:
            continue
        running_opts_dict = bridge_opts_str_to_dict(running_opts_str)
        kernel_opts_dict = {
            key: val
            for key, val in bridges.bridge_options(net).items()
            if key in running_opts_dict
        }
        kernel_opts_str = bridge_opts_dict_to_sorted_str(kernel_opts_dict)
        kernel_config.networks[net].setdefault('custom', {})[
            'bridge_opts'
        ] = kernel_opts_str


def _ipv4_is_unused(attrs):
    return 'ipaddr' not in attrs and attrs.get('bootproto') != 'dhcp'


def _ipv6_is_unused(attrs):
    return (
        'ipv6addr' not in attrs
        and 'ipv6autoconf' not in attrs
        and 'dhcpv6' not in attrs
        and ipv6_supported()
    )


class SetupNetworksError(Exception):
    def __init__(self, status, msg):
        super(SetupNetworksError, self).__init__(msg)
        self.status = status
        self.msg = msg


class SetupNetworks(object):
    def __init__(
        self,
        vdsm_proxy,
        update_running_and_kernel_config,
        assert_kernel_vs_running,
    ):
        self.vdsm_proxy = vdsm_proxy
        self._update_configs = update_running_and_kernel_config
        self._assert_configs = assert_kernel_vs_running

    def __call__(self, networks, bonds, options):
        self.setup_networks = networks
        self.setup_bonds = bonds

        status, msg = self.vdsm_proxy.setupNetworks(networks, bonds, options)
        if status != SUCCESS:
            self._update_configs()
            raise SetupNetworksError(status, msg)

        if nmstate.is_nmstate_backend():
            if self._is_sync_dynamic():
                _wait_for_dhcp_response(10)
                self.vdsm_proxy.refreshNetworkCapabilities()
        else:
            if self._is_dynamic_ipv4():
                self._wait_for_dhcpv4_response(10)
                self.vdsm_proxy.refreshNetworkCapabilities()
        try:
            self._update_configs()
            self._assert_configs()
        except Exception:
            # Ignore cleanup failure, make sure to re-raise original exception.
            self._cleanup()
            raise

        return self

    def __enter__(self):
        pass

    def __exit__(self, type, value, traceback):
        status, msg = self._cleanup()
        if type is None and status != SUCCESS:
            raise SetupNetworksError(status, msg)

    def _cleanup(self):
        networks_caps = self.vdsm_proxy.netinfo.networks
        bonds_caps = self.vdsm_proxy.netinfo.bondings
        NETSETUP = {
            net: {'remove': True}
            for net in self.setup_networks
            if net in networks_caps
        }
        BONDSETUP = {
            bond: {'remove': True}
            for bond in self.setup_bonds
            if bond in bonds_caps
        }
        status, msg = self.vdsm_proxy.setupNetworks(NETSETUP, BONDSETUP, NOCHK)

        nics_used = [
            attr['nic']
            for attr in self.setup_networks.values()
            if 'nic' in attr
        ]
        for attr in self.setup_bonds.values():
            nics_used += attr.get('nics', [])
        for nic in nics_used:
            fileutils.rm_file(IFCFG_PREFIX + nic)

        return status, msg

    def _is_sync_dynamic(self):
        return (
            self._is_dynamic_ipv4() or self._is_dynamic_ipv6()
        ) and self._is_blocking_dhcp()

    def _is_dynamic_ipv4(self):
        for attr in self.setup_networks.values():
            if attr.get('bootproto') == 'dhcp':
                return True
        return False

    def _is_dynamic_ipv6(self):
        for attr in self.setup_networks.values():
            if attr.get('dhcpv6'):
                return True
        return False

    def _is_blocking_dhcp(self):
        for attr in self.setup_networks.values():
            if attr.get('blockingdhcp'):
                return True
        return False

    def _wait_for_dhcpv4_response(self, timeout=5):
        dev_names = self._collect_all_dhcpv4_interfaces()
        _wait_for_func(
            _did_every_dhcp_server_responded, timeout, dev_names=dev_names
        )

    def _collect_all_dhcpv4_interfaces(self):
        return [
            _get_network_iface_name(name, attr)
            for name, attr in self.setup_networks.items()
            if attr.get('bootproto') == 'dhcp'
        ]


def _did_every_dhcp_server_responded(dev_names):
    for dev_name in dev_names:
        if iface_is_tracked(dev_name):
            return False
    return True


def _wait_for_dhcp_response(timeout=5):
    _wait_for_func(MonitoredItemPool.instance().is_pool_empty, timeout)


def _wait_for_func(func, timeout=5, **func_kwargs):
    for attempt in range(timeout):
        if func(**func_kwargs):
            break
        time.sleep(1)
    time.sleep(1)


@contextmanager
def monitor_stable_link_state(device, wait_for_linkup=True):
    """Raises an exception if it detects that the device link state changes."""
    if wait_for_linkup:
        with waitfor.waitfor_linkup(device):
            pass
    iface_properties = iface(device).properties()
    original_state = iface_properties['state']
    try:
        with monitor.object_monitor(groups=('link',)) as mon:
            yield
    finally:
        state_changes = (e['state'] for e in mon if e['name'] == device)
        for state in state_changes:
            if state != original_state:
                raise UnexpectedLinkStateChangeError(
                    '{} link state changed: {} -> {}'.format(
                        device, original_state, state
                    )
                )


def attach_dev_to_bridge(tapdev, bridge):
    rc, _, err = exec_sync(['ip', 'link', 'set', tapdev, 'master', bridge])
    if rc != 0:
        pytest.fail(
            'Filed to add {} to {}. err: {}'.format(tapdev, bridge, err)
        )


def wait_bonds_lp_interval():
    """ mode 4 (802.3ad) is a relevant bond mode where bonds will attempt
        to synchronize with each other, sending learning packets to the
        slaves in intervals set via lp_interval
    """
    GRACE_PERIOD = 1
    LACP_BOND_MODE = '4'

    default_lp_interval = int(
        getDefaultBondingOptions(LACP_BOND_MODE)['lp_interval'][0]
    )
    time.sleep(default_lp_interval + GRACE_PERIOD)


def _normalize_caps(netinfo_from_caps):
    """
    Normalize network caps to allow kernel vs running config comparison.

    The netinfo object used by the tests is created from the network caps data.
    To allow the kernel vs running comparison, it is required to revert the
    caps data compatibility conversions (required by the oVirt Engine).
    """
    netinfo = deepcopy(netinfo_from_caps)
    # TODO: When production code drops compatibility normalization, remove it.
    for dev in netinfo.networks.values():
        dev['mtu'] = int(dev['mtu'])

    return netinfo


def _normalize_qos_config(qos):
    for value in qos.values():
        for attrs in value.values():
            if attrs.get('m1') == 0:
                del attrs['m1']
            if attrs.get('d') == 0:
                del attrs['d']
    return qos


def _normalize_bonds(configs):
    for cfg in configs:
        for bond_name, bond_attrs in cfg['bonds'].items():
            opts = dict(
                pair.split('=', 1) for pair in bond_attrs['options'].split()
            )

            normalized_opts = _normalize_bond_opts(opts)
            bond_attrs['options'] = ' '.join(sorted(normalized_opts))


def _normalize_bond_opts(opts):
    _normalize_arg_ip_target_option(opts)
    return [opt + '=' + val for (opt, val) in opts.items()]


def _normalize_arg_ip_target_option(opts):
    if "arp_ip_target" in opts.keys():
        opts['arp_ip_target'] = ','.join(
            sorted(opts['arp_ip_target'].split(','))
        )


def _split_bond_options(opts):
    return _numerize_bond_options(opts) if opts else opts


def _numerize_bond_options(opts):
    optmap = dict((pair.split('=', 1) for pair in opts.split()))

    mode = optmap.get('mode')
    if not mode:
        return opts

    optmap['mode'] = numeric_mode = bond_options.numerize_bond_mode(mode)
    for opname, opval in optmap.items():
        numeric_val = bond_opts_mapper.get_bonding_option_numeric_val(
            numeric_mode, opname, opval
        )
        if numeric_val is not None:
            optmap[opname] = numeric_val

    return _normalize_bond_opts(optmap)


def _gather_expected_legacy_links(net, attrs, netinfo):
    bond = attrs.get('bonding')
    devs = set()

    devs.add(_get_network_iface_name(net, attrs))
    if bond:
        slaves = netinfo.bondings[bond]['slaves']
        devs.update(slaves)

    return devs


def _get_network_iface_name(net_name, net_attrs):
    bridged = net_attrs.get('bridged', True)
    vlan = net_attrs.get('vlan')
    nic = net_attrs.get('nic')
    bond = net_attrs.get('bonding')
    base_iface = nic or bond
    return (
        net_name
        if bridged
        else '{}.{}'.format(base_iface, vlan)
        if vlan
        else base_iface
    )


def _gather_expected_ovs_links(net, attrs, netinfo):
    bond = attrs.get('bonding')
    nic = attrs.get('nic')

    devs = {net}
    if bond:
        devs.add(bond)
        slaves = netinfo.bondings[bond]['slaves']
        devs.update(slaves)
    elif nic:
        devs.add(nic)

    return devs


class DeviceNotInCapsError(Exception):
    pass


class UnexpectedLinkStateChangeError(Exception):
    pass


class MissingDynamicIPv6Address(Exception):
    pass
