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
from __future__ import division


class HosttailError(Exception):
    pass


def hosttail_split(hosttail):
    """
    Given a hosttail, in the format host:tail, return the host and tail.
    The tail part may be a port or path (for mount points).
    For an IPv6 address host, it returns the address without the surrounding
    brackets.
    """
    try:
        if _is_literal_ipv6_addr_soft_check(hosttail):
            host, tail = _ipv6addr_hosttail_split(hosttail)
        else:
            host, tail = hosttail.split(':', 1)

        if not len(host) or not len(tail):
            raise ValueError

    except ValueError:
        raise HosttailError('%s is not a valid hosttail address:' % hosttail)
    return host, tail


def hosttail_join(host, tail):
    """
    Given a host and a tail, this method returns:
    - "[host]:tail" if the host contains at least one colon,
                    for example when the host is an IPv6 address.
    - "host:tail"   otherwise

    The tail part may be a port or a path (for mount points).
    """
    if ':' in host:
        host = '[' + host + ']'
    return host + ':' + tail


def normalize_literal_addr(addr):
    """
    Given a valid IP address, return it in a literal form.
    """
    if _is_literal_ipv6_addr_soft_check(addr):
        res = addr
    elif _is_ipv6_addr_soft_check(addr):
        res = '[{}]'.format(addr)
    else:
        res = addr
    return res


def _is_ipv6_addr_soft_check(addr):
    return addr.count(':') > 1


def _is_literal_ipv6_addr_soft_check(addr):
    return addr.startswith('[')


def _ipv6addr_hosttail_split(hostport):
    end_of_host = hostport.index(']:')
    return hostport[1:end_of_host], hostport[end_of_host + 2:]
