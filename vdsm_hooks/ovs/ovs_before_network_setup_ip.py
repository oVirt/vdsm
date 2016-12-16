#!/usr/bin/python2
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
from functools import partial
import os

import six

from vdsm import sysctl
from vdsm.compat import suppress
from vdsm.network import ipwrapper
from vdsm.network.configurators.iproute2 import Iproute2
from vdsm.network.ip import address
from vdsm.network.ip import dhclient
from vdsm.network.models import NetDevice
from vdsm.network.sourceroute import DynamicSourceRoute

from ovs_utils import BRIDGE_NAME
import ovs_utils

log = partial(ovs_utils.log, tag='ovs_before_network_setup_ip: ')


iproute2 = Iproute2()


class IPConfig(object):
    """Gather network's IP configuration and store it in an object"""
    def __init__(self, net, attrs):
        self.top_dev = net if 'vlan' in attrs else BRIDGE_NAME
        self.ipv4 = _get_ipv4_model(attrs)
        self.ipv6 = _get_ipv6_model(attrs)
        self.port = attrs.get('bonding') or attrs.get('nic')
        self.blocking_dhcp = 'blockingdhcp' in attrs


def _get_ipv4_model(attrs):
    addr = attrs.get('ipaddr')
    netmask = attrs.get('netmask')
    gateway = attrs.get('gateway')
    default_route = attrs.get('defaultRoute')
    bootproto = attrs.get('bootproto')
    return address.IPv4(addr, netmask, gateway, default_route, bootproto)


def _get_ipv6_model(attrs):
    addr = attrs.get('ipv6addr')
    gateway = attrs.get('ipv6gateway')
    default_route = attrs.get('defaultRoute')
    autoconf = attrs.get('ipv6autoconf')
    dhcp = attrs.get('dhcpv6')
    return address.IPv6(addr, gateway, default_route, autoconf, dhcp)


def _run_dhclient(iface, blockingdhcp, default_route, family):
    dhcp = dhclient.DhcpClient(iface, family, default_route)
    rc = dhcp.start(blockingdhcp)
    if blockingdhcp and rc:
        log('failed to start dhclient%s on iface %s' % (family, iface))


def _set_network_ip_config(ip_config):
    """Set IP configuration on Vdsm controlled OVS network"""
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


def _remove_network_ip_config(ip_config):
    """Remove IP configuration from Vdsm controlled OVS network"""
    iface = ip_config.top_dev
    ipv4 = ip_config.ipv4
    ipv6 = ip_config.ipv6

    net_dev = NetDevice(iface, iproute2, ipv4=ipv4, ipv6=ipv6)
    DynamicSourceRoute.addInterfaceTracking(net_dev)
    dhclient.DhcpClient(iface).shutdown()
    iproute2._removeSourceRoute(net_dev, DynamicSourceRoute)
    if ipv4.address or ipv6.address:
        with suppress(ipwrapper.IPRoute2Error):  # device does not exist
            ipwrapper.addrFlush(iface)


def _drop_nic_ip_config(iface):
    """Drop IP configuration of a new nic controlled by VDSM"""
    if os.path.exists(os.path.join('/sys/class/net', iface)):
        dhclient.kill(iface, family=4)
        dhclient.kill(iface, family=6)
        address.flush(iface)


def configure_ip(nets, init_nets, bonds, init_bonds):
    ip_config_to_set = {}
    ip_config_to_remove = {}

    for net, attrs in six.iteritems(nets):
        if 'remove' in attrs:  # if network was removed
            # remove network's IP configuration (running dhclient)
            ip_config = IPConfig(net, init_nets[net])
            ip_config_to_remove[ip_config.top_dev] = ip_config
        else:
            ip_config = IPConfig(net, attrs)
            if net in init_nets:  # if network was edited
                init_ip_config = IPConfig(net, init_nets[net])

                # drop IP of newly attached nics
                if init_nets[net].get('nic') != attrs.get('nic') is not None:
                    _drop_nic_ip_config(attrs.get('nic'))

                # if IP config is to be changed or network's top device was
                # changed, remove initial IP configuration and set the new one
                # if there is any
                if (ip_config.ipv4 != init_ip_config.ipv4 or
                    ip_config.ipv6 != init_ip_config.ipv6 or
                        attrs.get('vlan') != init_nets[net].get('vlan')):
                    ip_config_to_remove[
                        init_ip_config.top_dev] = init_ip_config
                    if ip_config.ipv4 or ip_config.ipv6:
                        ip_config_to_set[ip_config.top_dev] = ip_config
            else:  # if network was added
                # drop IP of newly attached nics
                nic = attrs.get('nic')
                if nic is not None:
                    _drop_nic_ip_config(nic)

                # set networks IP configuration if any
                if ip_config.ipv4 or ip_config.ipv6:
                    ip_config_to_set[ip_config.top_dev] = ip_config

    for bond, attrs in six.iteritems(bonds):
        # drop initial IP config of bonds' slaves
        if 'remove' not in attrs:
            nics_to_drop_ip_from = (
                set(attrs.get('nics')) - set(init_bonds[bond].get('nics'))
                if bond in init_bonds else attrs.get('nics'))
            for nic in nics_to_drop_ip_from:
                _drop_nic_ip_config(nic)

    log('Remove IP configuration of: %s' % ip_config_to_remove)
    log('Set IP configuration: %s' % ip_config_to_set)
    for iface, ip_config in six.iteritems(ip_config_to_remove):
        _remove_network_ip_config(ip_config)
    for iface, ip_config in six.iteritems(ip_config_to_set):
        _set_network_ip_config(ip_config)
