# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

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
