# Copyright 2016 Red Hat, Inc.
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

import six

from vdsm.network import errors as ne
from vdsm.network.netconfpersistence import RunningConfig

from .address import IPv4, IPv6


def validate(nets):
    default_route_nets = set()
    no_default_route_nets = set()
    for net, attrs in six.iteritems(nets):
        if 'remove' in attrs:
            continue
        _validate_nameservers(net, attrs)

        if attrs['defaultRoute']:
            default_route_nets.add(net)
        else:
            no_default_route_nets.add(net)

    _validate_default_route(default_route_nets, no_default_route_nets)


def _validate_default_route(default_route_nets, no_default_route_nets):
    for net, attrs in six.iteritems(RunningConfig().networks):
        if attrs['defaultRoute'] and net not in no_default_route_nets:
            default_route_nets.add(net)
    if len(default_route_nets) > 1:
        raise ne.ConfigNetworkError(
            ne.ERR_BAD_PARAMS,
            'Only a single default route network is allowed.')


def _validate_nameservers(net, attrs):
    if attrs['nameservers']:
        _validate_nameservers_network(attrs)
        _validate_nameservers_address(attrs['nameservers'])


def _validate_nameservers_network(attrs):
    if not attrs['defaultRoute']:
        raise ne.ConfigNetworkError(
            ne.ERR_BAD_PARAMS,
            'Name servers may only be defined on the default host network')


def _validate_nameservers_address(nameservers_addr):
    for addr in nameservers_addr:
        addr = _normalize_address(addr)
        if ':' in addr:
            IPv6.validateAddress(addr)
        else:
            IPv4.validateAddress(addr)


def _normalize_address(addr):
    """
    The nameserver address may be tailed with the interface from which it
    should be reached: 'fe80::1%eth0'
    Please see zone identifier RFC for more information:
        https://tools.ietf.org/html/rfc6874
    For the purpose of address validation, such tail is ignored.
    """
    return addr.split('%', 1)[0]
