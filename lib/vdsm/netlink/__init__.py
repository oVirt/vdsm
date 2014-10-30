#
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
from ctypes import (CDLL, CFUNCTYPE, c_char, c_char_p, c_int, c_void_p,
                    c_size_t, get_errno, sizeof)
from Queue import Empty, Queue
from threading import BoundedSemaphore

_POOL_SIZE = 5
_NETLINK_ROUTE = 0
CHARBUFFSIZE = 40  # Increased to fit IPv6 expanded representations
HWADDRSIZE = 60    # InfiniBand HW address needs 59+1 bytes


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

    err = _nl_connect(sock, _NETLINK_ROUTE)
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


def _addr_to_str(addr):
    """Returns the textual representation of a netlink address (be it hardware
    or IP) or None if the address is None"""
    if addr is not None:
        address = (c_char * HWADDRSIZE)()
        return _nl_addr2str(addr, address, sizeof(address))


def _af_to_str(af_num):
    """Returns the textual address family representation of the numerical id"""
    family = (c_char * CHARBUFFSIZE)()
    return _nl_af2str(af_num, family, sizeof(family))


def _scope_to_str(scope_num):
    """Returns the textual scope representation of the numerical id"""
    scope = (c_char * CHARBUFFSIZE)()
    return _rtnl_scope2str(scope_num, scope, sizeof(scope))

# C function prototypes
# http://docs.python.org/2/library/ctypes.html#function-prototypes
# This helps ctypes know the calling conventions it should use to communicate
# with the binary interface of libnl and which types it should allocate and
# cast. Without it ctypes fails when not running on the main thread.
_int_proto = CFUNCTYPE(c_int, c_void_p)
_int_char_proto = CFUNCTYPE(c_char_p, c_int, c_char_p, c_size_t)
_char_proto = CFUNCTYPE(c_char_p, c_void_p)
_void_proto = CFUNCTYPE(c_void_p, c_void_p)
_none_proto = CFUNCTYPE(None, c_void_p)


LIBNL = CDLL('libnl-3.so.200',  use_errno=True)
LIBNL_ROUTE = CDLL('libnl-route-3.so.200',  use_errno=True)

_nl_socket_alloc = CFUNCTYPE(c_void_p)(('nl_socket_alloc', LIBNL))
_nl_socket_free = _none_proto(('nl_socket_free', LIBNL))

_nl_connect = CFUNCTYPE(c_int, c_void_p, c_int)(('nl_connect', LIBNL))
_nl_geterror = CFUNCTYPE(c_char_p)(('nl_geterror', LIBNL))

_nl_cache_free = _none_proto(('nl_cache_free', LIBNL))
_nl_cache_get_first = _void_proto(('nl_cache_get_first', LIBNL))
_nl_cache_get_next = _void_proto(('nl_cache_get_next', LIBNL))
_nl_addr2str = CFUNCTYPE(c_char_p, c_void_p, c_char_p, c_size_t)((
    'nl_addr2str', LIBNL))
_nl_af2str = _int_char_proto(('nl_af2str', LIBNL))
_rtnl_scope2str = _int_char_proto(('rtnl_scope2str', LIBNL_ROUTE))
