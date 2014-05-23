# Copyright 2014 Red Hat, Inc.
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

from contextlib import contextmanager
from ctypes import (CDLL, CFUNCTYPE, byref, c_char, c_char_p, c_int, c_void_p,
                    c_size_t, get_errno, sizeof)
from distutils.version import StrictVersion
from functools import partial
from Queue import Empty, Queue
from threading import BoundedSemaphore
import errno
import ethtool

NETLINK_ROUTE = 0
_POOL_SIZE = 5
CHARBUFFSIZE = 40  # Increased to fit IPv6 expanded representations


def iter_links():
    """Generator that yields an information dictionary for each link of the
    system."""
    with _pool.socket() as sock:
        with _nl_link_cache(sock) as cache:
            link = _nl_cache_get_first(cache)
            while link:
                yield _link_info(cache, link)
                link = _nl_cache_get_next(link)


def iter_addrs():
    """Generator that yields an information dictionary for each network address
    in the system."""
    with _pool.socket() as sock:
        with _nl_addr_cache(sock) as addr_cache:
            with _nl_link_cache(sock) as link_cache:  # for index to label
                addr = _nl_cache_get_first(addr_cache)
                while addr:
                    yield _addr_info(link_cache, addr)
                    addr = _nl_cache_get_next(addr)


def get_link(name):
    """Returns the information dictionary of the name specified link."""
    with _pool.socket() as sock:
        with _nl_link_cache(sock) as cache:
            link = _rtnl_link_get_by_name(cache, name)
            if not link:
                raise IOError(errno.ENODEV, '%s is not present in the system' %
                              name)
            return _link_info(cache, link)


class NLSocketPool(object):
    """Pool of netlink sockets."""
    def __init__(self, size):
        if size <= 0:
            raise ValueError('Invalid socket pool size %r. Must be positive')
        self._semaphore = BoundedSemaphore(size)
        self._sockets = Queue(maxsize=size)

    @contextmanager
    def socket(self):
        """Returns a socket from the pool (creating it when needed)."""
        with self._semaphore:
            try:
                sock = self._sockets.get_nowait()
            except Empty:
                sock = _open_socket()
            try:
                yield sock
            finally:
                self._sockets.put_nowait(sock)


_pool = NLSocketPool(_POOL_SIZE)


def _open_socket():
    """Returns an open netlink socket."""
    sock = _nl_socket_alloc()
    if sock is None:
        raise IOError(get_errno(), 'Failed to allocate netlink handle')

    err = _nl_connect(sock, NETLINK_ROUTE)
    if err:
        _nl_socket_free(sock)
        raise IOError(-err, _nl_geterror())
    return sock


def _close_socket(sock):
    """Closes and frees the resources of the passed netlink socket."""
    _nl_socket_free(sock)


@contextmanager
def _cache_manager(cache_allocator, sock):
    """Provides a cache using cache_allocator and frees it and its links upon
    exit."""
    cache = cache_allocator(sock)
    try:
        yield cache
    finally:
        _nl_cache_free(cache)


def _addr_info(link_cache, addr):
    """Returns a dictionary with the address information."""
    index = _rtnl_addr_get_ifindex(addr)
    return {
        'label': (_rtnl_addr_get_label(addr) or
                  _link_index_to_name(link_cache, index)),
        'index': index,
        'family': _addr_family(addr),
        'prefixlen': _rtnl_addr_get_prefixlen(addr),
        'scope': _addr_scope(addr),
        'flags': _addr_flags(addr),
        'address': _addr_local(addr)}


def _link_info(cache, link):
    """Returns a dictionary with the information of the link object."""
    info = {}
    info['address'] = _link_address(link)
    info['flags'] = _rtnl_link_get_flags(link)
    info['index'] = _rtnl_link_get_ifindex(link)
    info['mtu'] = _rtnl_link_get_mtu(link)
    info['name'] = _rtnl_link_get_name(link)
    info['qdisc'] = _rtnl_link_get_qdisc(link)
    info['state'] = _link_state(link)

    # libnl-1 has a bug when getting type information.
    # https://github.com/tgraf/libnl-1.1-stable/issues/1
    # TODO: Add for libnl1 if the bug is fixed
    if _ethtool_uses_libnl3():
        link_type = _rtnl_link_get_type(link)
        if link_type is not None:
            info['type'] = link_type

    underlying_device_index = _rtnl_link_get_link(link)
    if underlying_device_index > 0:
        info['device'] = _link_index_to_name(cache, underlying_device_index)

    master_index = _rtnl_link_get_master(link)
    if master_index > 0:
        info['master'] = _link_index_to_name(cache, master_index)

    vlanid = _rtnl_link_vlan_get_id(link)
    if vlanid >= 0:
        info['vlanid'] = vlanid

    return info


def _link_index_to_name(cache, link_index):
    """Returns the textual name of the link with index equal to link_index."""
    name = (c_char * CHARBUFFSIZE)()
    return _rtnl_link_i2name(cache, link_index, name, sizeof(name))


def _link_address(link):
    """Returns the MAC address of a link object or None if the link does not
    have a physical address."""
    nl_addr = _rtnl_link_get_addr(link)
    if nl_addr:
        address = (c_char * CHARBUFFSIZE)()
        return _nl_addr2str(nl_addr, address, sizeof(address))
    else:
        return None


def _link_state(link):
    """Returns the textual representation of the link's operstate."""
    state = _rtnl_link_get_operstate(link)
    operstate = (c_char * CHARBUFFSIZE)()
    return _rtnl_link_operstate2str(state, operstate, sizeof(operstate))


def _addr_flags(addr):
    """Returns the textual translation of the address flags"""
    flags = (c_char * (CHARBUFFSIZE * 2))()
    return frozenset(_rtnl_addr_flags2str(_rtnl_addr_get_flags(addr), flags,
                                          sizeof(flags)).split(','))


def _addr_scope(addr):
    """Returns the scope name for which the address is defined."""
    scope = (c_char * CHARBUFFSIZE)()
    return _rtnl_scope2str(_rtnl_addr_get_scope(addr), scope, sizeof(scope))


def _addr_family(addr):
    """Returns the family name of the address."""
    family = (c_char * CHARBUFFSIZE)()
    return _nl_af2str(_rtnl_addr_get_family(addr), family, sizeof(family))


def _addr_local(addr):
    """Returns the textual representation of the address."""
    address = (c_char * CHARBUFFSIZE)()
    return _nl_addr2str(_rtnl_addr_get_local(addr), address, sizeof(address))


def _ethtool_uses_libnl3():
    """Returns whether ethtool uses libnl3."""
    return (StrictVersion('0.9') <=
            StrictVersion(ethtool.version.split()[1].lstrip('v')))


# C function prototypes
# http://docs.python.org/2/library/ctypes.html#function-prototypes
# This helps ctypes know the calling conventions it should use to communicate
# with the binary interface of libnl and which types it should allocate and
# cast. Without it ctypes fails when not running on the main thread.
_int_proto = CFUNCTYPE(c_int, c_void_p)
_char_proto = CFUNCTYPE(c_char_p, c_void_p)
_void_proto = CFUNCTYPE(c_void_p, c_void_p)
_none_proto = CFUNCTYPE(None, c_void_p)
_int_char_proto = CFUNCTYPE(c_char_p, c_int, c_char_p, c_size_t)

if _ethtool_uses_libnl3():
    LIBNL = CDLL('libnl-3.so.200',  use_errno=True)
    LIBNL_ROUTE = CDLL('libnl-route-3.so.200',  use_errno=True)

    _nl_socket_alloc = CFUNCTYPE(c_void_p)(('nl_socket_alloc', LIBNL))
    _nl_socket_free = _none_proto(('nl_socket_free', LIBNL))

    _link_alloc_cache = CFUNCTYPE(c_int, c_void_p, c_int, c_void_p)(
        ('rtnl_link_alloc_cache', LIBNL_ROUTE))
    _addr_alloc_cache = CFUNCTYPE(c_int, c_void_p, c_void_p)(
        ('rtnl_addr_alloc_cache', LIBNL_ROUTE))
    _link_is_vlan = _int_proto(('rtnl_link_is_vlan', LIBNL_ROUTE))
    _vlan_get_id = _int_proto(('rtnl_link_vlan_get_id', LIBNL_ROUTE))

    def _rtnl_link_alloc_cache(sock):
        """Wraps the new link alloc cache to expose the libnl1 signature"""
        AF_UNSPEC = 0
        cache = c_void_p()
        err = _link_alloc_cache(sock, AF_UNSPEC, byref(cache))
        if err:
            raise IOError(-err, _nl_geterror())
        return cache

    def _rtnl_addr_alloc_cache(sock):
        """Wraps the new addr alloc cache to expose the libnl1 signature"""
        cache = c_void_p()
        err = _addr_alloc_cache(sock, byref(cache))
        if err:
            raise IOError(-err, _nl_geterror())
        return cache

    _rtnl_link_get_type = _char_proto(('rtnl_link_get_type', LIBNL_ROUTE))

    def _rtnl_link_vlan_get_id(link):
        """Wraps the new vlan id retrieval to expose the libnl1 signature"""
        if _link_is_vlan(link):
            return _vlan_get_id(link)
        else:
            return -1
else:  # libnl-1
    # Change from handle to socket as it is now more accurately called in
    # libnl-3
    LIBNL_ROUTE = LIBNL = CDLL('libnl.so.1', use_errno=True)

    _nl_socket_alloc = CFUNCTYPE(c_void_p)(('nl_handle_alloc', LIBNL))
    _nl_socket_free = _none_proto(('nl_handle_destroy', LIBNL))

    _rtnl_link_vlan_get_id = _int_proto(('rtnl_link_vlan_get_id', LIBNL))
    _link_alloc_cache = _void_proto(('rtnl_link_alloc_cache', LIBNL))
    _addr_alloc_cache = _void_proto(('rtnl_addr_alloc_cache', LIBNL))

    def _alloc_cache(allocator, sock):
        cache = allocator(sock)
        if cache is None:
            raise IOError(get_errno(), 'Failed to allocate the cache')
        return cache

    _rtnl_link_alloc_cache = partial(_alloc_cache, _link_alloc_cache)
    _rtnl_addr_alloc_cache = partial(_alloc_cache, _addr_alloc_cache)

_nl_connect = CFUNCTYPE(c_int, c_void_p, c_int)(('nl_connect', LIBNL))
_nl_geterror = CFUNCTYPE(c_char_p)(('nl_geterror', LIBNL))

_nl_cache_free = _none_proto(('nl_cache_free', LIBNL))
_nl_cache_get_first = _void_proto(('nl_cache_get_first', LIBNL))
_nl_cache_get_next = _void_proto(('nl_cache_get_next', LIBNL))

_rtnl_link_get_addr = _void_proto(('rtnl_link_get_addr', LIBNL_ROUTE))
_rtnl_link_get_flags = _int_proto(('rtnl_link_get_flags', LIBNL_ROUTE))
_rtnl_link_get_ifindex = _int_proto(('rtnl_link_get_ifindex', LIBNL_ROUTE))
_rtnl_link_get_link = _int_proto(('rtnl_link_get_link', LIBNL_ROUTE))
_rtnl_link_get_master = _int_proto(('rtnl_link_get_master', LIBNL_ROUTE))
_rtnl_link_get_mtu = _int_proto(('rtnl_link_get_mtu', LIBNL_ROUTE))
_rtnl_link_get_name = _char_proto(('rtnl_link_get_name', LIBNL_ROUTE))
_rtnl_link_get_operstate = _int_proto(('rtnl_link_get_operstate', LIBNL_ROUTE))
_rtnl_link_get_qdisc = _char_proto(('rtnl_link_get_qdisc', LIBNL_ROUTE))

_rtnl_addr_get_label = _char_proto(('rtnl_addr_get_label', LIBNL_ROUTE))
_rtnl_addr_get_ifindex = _int_proto(('rtnl_addr_get_ifindex', LIBNL_ROUTE))
_rtnl_addr_get_family = _int_proto(('rtnl_addr_get_family', LIBNL_ROUTE))
_rtnl_addr_get_prefixlen = _int_proto(('rtnl_addr_get_prefixlen', LIBNL_ROUTE))
_rtnl_addr_get_scope = _int_proto(('rtnl_addr_get_scope', LIBNL_ROUTE))
_rtnl_addr_get_flags = _int_proto(('rtnl_addr_get_flags', LIBNL_ROUTE))
_rtnl_addr_get_local = _void_proto(('rtnl_addr_get_local', LIBNL_ROUTE))
_rtnl_addr_flags2str = _int_char_proto(('rtnl_addr_flags2str', LIBNL_ROUTE))

_nl_addr2str = CFUNCTYPE(c_char_p, c_void_p, c_char_p, c_size_t)((
    'nl_addr2str', LIBNL))
_rtnl_link_get_by_name = CFUNCTYPE(c_void_p, c_void_p, c_char_p)((
    'rtnl_link_get_by_name', LIBNL_ROUTE))
_rtnl_link_i2name = CFUNCTYPE(c_char_p, c_void_p, c_int, c_char_p, c_size_t)((
    'rtnl_link_i2name', LIBNL_ROUTE))
_rtnl_link_operstate2str = _int_char_proto(('rtnl_link_operstate2str',
                                            LIBNL_ROUTE))
_nl_af2str = _int_char_proto(('nl_af2str', LIBNL))
_rtnl_scope2str = _int_char_proto(('rtnl_scope2str', LIBNL_ROUTE))

_nl_link_cache = partial(_cache_manager, _rtnl_link_alloc_cache)
_nl_addr_cache = partial(_cache_manager, _rtnl_addr_alloc_cache)
