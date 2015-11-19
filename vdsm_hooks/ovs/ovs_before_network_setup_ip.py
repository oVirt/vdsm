#!/usr/bin/env python
# Copyright 2015 Red Hat, Inc.
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
from collections import namedtuple
import sys

from vdsm import ipwrapper, sysctl

import hooking

from ovs_utils import suppress, BRIDGE_NAME

# TODO: move required modules into vdsm/lib
sys.path.append('/usr/share/vdsm')
from network.configurators.dhclient import DhcpClient
from network.configurators.iproute2 import Iproute2
from network.models import NetDevice, IPv4, IPv6
from network.sourceroute import DynamicSourceRoute


iproute2 = Iproute2()

IPConfig = namedtuple('IPConfig', ['top_dev', 'ipv4', 'ipv6', 'port',
                                   'blocking_dhcp'])


def _get_ipv4_model(attrs):
    address = attrs.get('ipaddr')
    netmask = attrs.get('netmask')
    gateway = attrs.get('gateway')
    default_route = attrs.get('defaultRoute')
    bootproto = attrs.get('bootproto')
    return IPv4(address, netmask, gateway, default_route, bootproto)


def _get_ipv6_model(attrs):
    address = attrs.get('ipv6addr')
    gateway = attrs.get('ipv6gateway')
    default_route = attrs.get('defaultRoute')
    autoconf = attrs.get('ipv6autoconf')
    dhcp = attrs.get('dhcpv6')
    return IPv6(address, gateway, default_route, autoconf, dhcp)


def _run_dhclient(iface, blockingdhcp, default_route, family):
    dhclient = DhcpClient(iface, family, default_route)
    rc = dhclient.start(blockingdhcp)
    if blockingdhcp and rc:
        hooking.log('failed to start dhclient%s on iface %s' % (family, iface))


def _set_ip_config(ip_config):
    iface = ip_config.top_dev
    ipv4 = ip_config.ipv4
    ipv6 = ip_config.ipv6
    port = ip_config.port
    blocking_dhcp = ip_config.blocking_dhcp

    net_dev = NetDevice(iface, iproute2, ipv4=ipv4, ipv6=ipv6,
                        blockingdhcp=blocking_dhcp)
    DynamicSourceRoute.addInterfaceTracking(net_dev)
    ipwrapper.linkSet(iface, ['down'])
    if ipv4.address:
        ipwrapper.addrAdd(iface, ipv4.address, ipv4.netmask)
    if ipv4.gateway and ipv4.defaultRoute:
        ipwrapper.routeAdd(['default', 'via', ipv4.gateway])
    if ipv6.address or ipv6.ipv6autoconf or ipv6.dhcpv6:
        sysctl.disable_ipv6(iface, disable=False)
    else:
        sysctl.disable_ipv6(iface)
    if ipv6.address:
        ipv6addr, ipv6netmask = ipv6.address.split('/')
        ipwrapper.addrAdd(iface, ipv6addr, ipv6netmask, family=6)
        if ipv6.gateway:
            ipwrapper.routeAdd(['default', 'via', ipv6.gateway], dev=iface,
                               family=6)
    ipwrapper.linkSet(port, ['up'])
    ipwrapper.linkSet(iface, ['up'])
    if ipv4.bootproto == 'dhcp':
        _run_dhclient(iface, blocking_dhcp, ipv4.defaultRoute, 4)
    if ipv6.dhcpv6:
        _run_dhclient(iface, blocking_dhcp, ipv6.defaultRoute, 6)
    iproute2._addSourceRoute(net_dev)


def _remove_ip_config(ip_config):
    iface = ip_config.top_dev
    ipv4 = ip_config.ipv4
    ipv6 = ip_config.ipv6

    net_dev = NetDevice(iface, iproute2, ipv4=ipv4, ipv6=ipv6)
    DynamicSourceRoute.addInterfaceTracking(net_dev)
    DhcpClient(iface).shutdown()
    iproute2._removeSourceRoute(net_dev, DynamicSourceRoute)
    if ipv4.address or ipv6.address:
        with suppress(ipwrapper.IPRoute2Error):  # device does not exist
            ipwrapper.addrFlush(iface)


def configure_ip(nets, init_nets):

    def _gather_ip_config(attrs):
        top_dev = net if 'vlan' in attrs else BRIDGE_NAME
        ipv4 = _get_ipv4_model(attrs)
        ipv6 = _get_ipv6_model(attrs)
        port = attrs.get('nic') or attrs.get('bonding')
        blocking_dhcp = 'blockingdhcp' in attrs
        return IPConfig(top_dev, ipv4, ipv6, port, blocking_dhcp)

    ip_config_to_set = {}
    ip_config_to_remove = {}

    for net, attrs in nets.items():
        if net in init_nets:
            init_ip_config = _gather_ip_config(init_nets[net])
            ip_config_to_remove[init_ip_config.top_dev] = init_ip_config
        if 'remove' not in attrs:
            ip_config = _gather_ip_config(attrs)
            if ip_config.ipv4 or ip_config.ipv6:
                ip_config_to_set[ip_config.top_dev] = ip_config

    hooking.log('Remove IP configuration of: %s' % ip_config_to_remove)
    hooking.log('Set IP configuration: %s' % ip_config_to_set)
    for iface, ip_config in ip_config_to_remove.iteritems():
        _remove_ip_config(ip_config)
    for iface, ip_config in ip_config_to_set.items():
        _set_ip_config(ip_config)
