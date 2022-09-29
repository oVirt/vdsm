# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import
from __future__ import division

from collections import defaultdict

import ipaddress
import logging
import socket

from vdsm.network.netlink import addr as nl_addr
from vdsm.network.sysctl import is_ipv6_local_auto as sysctl_is_ipv6_local_auto


def getIpInfo(dev, ipaddrs=None, ipv4_gateway=None):
    """Report IP addresses of a device. if there are multiple primary IP
    addresses, report in ipv4addr the one that is in the same subnet of
    ipv4_gateway, if it is supplied."""
    # TODO: support same logic for ipv6

    if ipaddrs is None:
        ipaddrs = getIpAddrs()
    ipv4addr = ipv4netmask = ''
    ipv4addrs = []
    ipv6addrs = []

    def addr_in_gw_net(address, prefix, ipv4_gw):
        addr_iface = ipaddress.ip_interface(f'{address}/{prefix}')
        gw_net = ipaddress.ip_interface(f'{ipv4_gw}/{prefix}').network
        return addr_iface in gw_net

    for addr in ipaddrs[dev]:
        if addr['scope'] == 'link':
            continue
        address_cidr = nl_addr.cidr_form(addr)  # x.y.z.t/N
        if addr['family'] == 'inet':  # ipv4
            ipv4addrs.append(address_cidr)
            if nl_addr.is_primary(addr) and ipv4_gateway and ipv4addr == '':
                address, prefix = nl_addr.split(addr)
                if addr_in_gw_net(address, prefix, ipv4_gateway):
                    ipv4addr, ipv4netmask = _addr_and_netmask_from_cidr(
                        address_cidr
                    )
        else:  # ipv6
            ipv6addrs.append(address_cidr)
    if ipv4addrs and ipv4addr == '':
        # If we didn't find an address in the gateway subnet (which is
        # legal if there is another route that takes us to the gateway) we
        # choose to report the first address
        ipv4addr, ipv4netmask = _addr_and_netmask_from_cidr(ipv4addrs[0])

    return ipv4addr, ipv4netmask, ipv4addrs, ipv6addrs


def getIpAddrs():
    addrs = defaultdict(list)
    for addr in nl_addr.iter_addrs():
        addrs[addr['label']].append(addr)
    return addrs


def IPv4toMapped(ip):
    """Return an IPv6 IPv4-mapped address for the IPv4 address"""
    mapped = None

    try:
        ipv6bin = b'\x00' * 10 + b'\xff\xff' + socket.inet_aton(ip)
        mapped = socket.inet_ntop(socket.AF_INET6, ipv6bin)
    except socket.error as e:
        logging.debug("getIfaceByIP: %s", e)

    return mapped


def getDeviceByIP(ip):
    """
    Get network device by IP address
    :param ip: String representing IPv4 or IPv6
    """
    for addr in nl_addr.iter_addrs():
        address = addr['address'].split('/')[0]
        if (
            addr['family'] == 'inet' and ip in (address, IPv4toMapped(address))
        ) or (addr['family'] == 'inet6' and ip == address):
            return addr['label']
    return ''


def getIpAddresses():
    "Return a list of the host's IPv4 addresses"
    return [
        addr['address']
        for addr in nl_addr.iter_addrs()
        if addr['family'] == 'inet'
    ]


def is_ipv6(nladdr):
    return nladdr['family'] == 'inet6'


def is_dynamic(nladdr):
    return not nl_addr.is_permanent(nladdr)


def is_ipv6_local_auto(iface):
    return sysctl_is_ipv6_local_auto(iface)


def _addr_and_netmask_from_cidr(address_cidr):
    ip_iface = ipaddress.ip_interface(address_cidr)
    return str(ip_iface.ip), str(ip_iface.network.netmask)
