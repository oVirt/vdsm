# Copyright 2014-2017 Red Hat, Inc.
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
from contextlib import contextmanager
from ctypes import (CFUNCTYPE, byref, c_char, c_char_p, c_int, c_void_p,
                    c_size_t, sizeof)
from functools import partial
from socket import AF_UNSPEC
import errno

from . import _cache_manager, _nl_cache_get_first, _nl_cache_get_next
from . import _char_proto, _int_char_proto, _int_proto, _void_proto
from . import LIBNL_ROUTE, _pool, _none_proto
from . import _addr_to_str, CHARBUFFSIZE
from . import libnl

IFF_UP = 1 << 0             # Device administrative status.
IFF_BROADCAST = 1 << 1
IFF_DEBUG = 1 << 2
IFF_LOOPBACK = 1 << 3
IFF_POINTOPOINT = 1 << 4
IFF_NOTRAILERS = 1 << 5
IFF_RUNNING = 1 << 6        # Device operational_status
IFF_NOARP = 1 << 7
IFF_PROMISC = 1 << 8
IFF_ALLMULTI = 1 << 9
IFF_MASTER = 1 << 10
IFF_SLAVE = 1 << 11
IFF_MULTICAST = 1 << 12
IFF_PORTSEL = 1 << 13
IFF_AUTOMEDIA = 1 << 14
IFF_DYNAMIC = 1 << 15
IFF_LOWER_UP = 1 << 16
IFF_DORMANT = 1 << 17
IFF_ECHO = 1 << 18


def get_link(name):
    """Returns the information dictionary of the name specified link."""
    with _pool.socket() as sock:
        with _get_link(name=name, sock=sock) as link:
            if not link:
                raise IOError(errno.ENODEV, '%s is not present in the system' %
                              name)
            link_info = _link_info(link)
        return link_info


def iter_links():
    """Generator that yields an information dictionary for each link of the
    system."""
    with _pool.socket() as sock:
        with _nl_link_cache(sock) as cache:
            link = _nl_cache_get_first(cache)
            while link:
                yield _link_info(link, cache=cache)
                link = _nl_cache_get_next(link)


def is_link_up(link_flags, check_oper_status):
    """
    Check link status based on device status flags.
    :param link_flags: Status flags.
    :param check_oper_status: If set, the operational status of the link is
    checked in addition to the administrative status.
    :return:
    """
    iface_up = link_flags & IFF_UP
    if check_oper_status:
        iface_up = iface_up and (link_flags & IFF_RUNNING)
    return bool(iface_up)


def _link_info(link, cache=None):
    """Returns a dictionary with the information of the link object."""
    info = {}
    info['address'] = _addr_to_str(_rtnl_link_get_addr(link))
    info['flags'] = _rtnl_link_get_flags(link)
    info['index'] = _rtnl_link_get_ifindex(link)
    info['mtu'] = _rtnl_link_get_mtu(link)
    info['name'] = _rtnl_link_get_name(link)
    info['qdisc'] = _rtnl_link_get_qdisc(link)
    info['state'] = _link_state(link)

    link_type = _rtnl_link_get_type(link)
    if link_type is not None:
        info['type'] = link_type

    underlying_device_index = _rtnl_link_get_link(link)
    if underlying_device_index > 0:
        info['device_index'] = underlying_device_index
        try:
            info['device'] = _link_index_to_name(underlying_device_index,
                                                 cache=cache)
        except IOError as err:
            if err.errno != errno.ENODEV:
                raise

    master_index = _rtnl_link_get_master(link)
    if master_index > 0:
        info['master_index'] = master_index
        try:
            info['master'] = _link_index_to_name(master_index, cache=cache)
        except IOError as err:
            if err.errno != errno.ENODEV:
                raise

    vlanid = _rtnl_link_vlan_get_id(link)
    if vlanid >= 0:
        info['vlanid'] = vlanid

    return info


def _link_index_to_name(link_index, cache=None):
    """Returns the textual name of the link with index equal to link_index."""
    name = (c_char * CHARBUFFSIZE)()

    if cache is None:
        with _get_link(index=link_index) as link:
            if link is None:
                raise IOError(errno.ENODEV, 'Dev with index %s is not present '
                                            'in the system' % link_index)
            name = _rtnl_link_get_name(link)
        return name
    else:
        return _rtnl_link_i2name(cache, link_index, name, sizeof(name))


def _link_state(link):
    """Returns the textual representation of the link's operstate."""
    state = _rtnl_link_get_operstate(link)
    operstate = (c_char * CHARBUFFSIZE)()
    return _rtnl_link_operstate2str(state, operstate, sizeof(operstate))


# C function prototypes
# http://docs.python.org/2/library/ctypes.html#function-prototypes
# This helps ctypes know the calling conventions it should use to communicate
# with the binary interface of libnl and which types it should allocate and
# cast. Without it ctypes fails when not running on the main thread.
_link_alloc_cache = CFUNCTYPE(c_int, c_void_p, c_int, c_void_p)(
    ('rtnl_link_alloc_cache', LIBNL_ROUTE))
_link_is_vlan = _int_proto(('rtnl_link_is_vlan', LIBNL_ROUTE))
_vlan_get_id = _int_proto(('rtnl_link_vlan_get_id', LIBNL_ROUTE))
_rtnl_link_get_type = _char_proto(('rtnl_link_get_type', LIBNL_ROUTE))
_rtnl_link_get_kernel = CFUNCTYPE(c_int, c_void_p, c_int, c_char_p,
                                  c_void_p)(('rtnl_link_get_kernel',
                                             LIBNL_ROUTE))


def _rtnl_link_alloc_cache(sock):
    """Wraps the new link alloc cache to expose the libnl1 signature"""
    cache = c_void_p()
    err = _link_alloc_cache(sock, AF_UNSPEC, byref(cache))
    if err:
        raise IOError(-err, libnl.nl_geterror(err))
    return cache


def _rtnl_link_vlan_get_id(link):
    """Wraps the new vlan id retrieval to expose the libnl1 signature"""
    if _link_is_vlan(link):
        return _vlan_get_id(link)
    else:
        return -1


@contextmanager
def _get_link(name=None, index=0, sock=None):
    """ If defined both name and index, index is primary """
    # libnl/incluede/netlink/errno.h
    NLE_NODEV = 31

    if name is None and index == 0:
        raise ValueError('Must specify either a name or an index')
    link = c_void_p()

    try:
        if sock is None:
            with _pool.socket() as sock:
                err = _rtnl_link_get_kernel(sock, index, name, byref(link))
        else:
            err = _rtnl_link_get_kernel(sock, index, name, byref(link))
        if err:
            if -err == NLE_NODEV:
                link = None
            else:
                raise IOError(-err, libnl.nl_geterror(err))
        yield link
    finally:
        if link is not None:
            _rtnl_link_put(link)

_nl_link_cache = partial(_cache_manager, _rtnl_link_alloc_cache)

_rtnl_link_get_addr = _void_proto(('rtnl_link_get_addr', LIBNL_ROUTE))
_rtnl_link_get_flags = _int_proto(('rtnl_link_get_flags', LIBNL_ROUTE))
_rtnl_link_get_ifindex = _int_proto(('rtnl_link_get_ifindex', LIBNL_ROUTE))
_rtnl_link_get_link = _int_proto(('rtnl_link_get_link', LIBNL_ROUTE))
_rtnl_link_get_master = _int_proto(('rtnl_link_get_master', LIBNL_ROUTE))
_rtnl_link_get_mtu = _int_proto(('rtnl_link_get_mtu', LIBNL_ROUTE))
_rtnl_link_get_name = _char_proto(('rtnl_link_get_name', LIBNL_ROUTE))
_rtnl_link_get_operstate = _int_proto(('rtnl_link_get_operstate', LIBNL_ROUTE))
_rtnl_link_get_qdisc = _char_proto(('rtnl_link_get_qdisc', LIBNL_ROUTE))
_rtnl_link_get_by_name = CFUNCTYPE(c_void_p, c_void_p, c_char_p)((
    'rtnl_link_get_by_name', LIBNL_ROUTE))
_rtnl_link_i2name = CFUNCTYPE(c_char_p, c_void_p, c_int, c_char_p, c_size_t)((
    'rtnl_link_i2name', LIBNL_ROUTE))
_rtnl_link_operstate2str = _int_char_proto(('rtnl_link_operstate2str',
                                            LIBNL_ROUTE))
_rtnl_link_put = _none_proto(('rtnl_link_put', LIBNL_ROUTE))
