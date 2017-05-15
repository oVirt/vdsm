#
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
from ctypes import (CDLL, CFUNCTYPE, c_char, c_char_p, c_int, c_void_p,
                    c_size_t, get_errno, py_object, sizeof)
from functools import partial
from threading import BoundedSemaphore

from six.moves import queue

from . import libnl

_POOL_SIZE = 5
_NETLINK_ROUTE = 0
CHARBUFFSIZE = 40  # Increased to fit IPv6 expanded representations
HWADDRSIZE = 60    # InfiniBand HW address needs 59+1 bytes

_NLE_NODEV = 31  # libnl/incluede/netlink/errno.h

_NL_CB_DEFAULT = 0  # libnl/include/netlink/handlers.h
_NL_CB_CUSTOM = 3   # libnl/include/netlink/handlers.h

_NL_STOP = 2  # libnl/python/netlink/capi.i

_NL_ROUTE_NAME = 'route'
_NL_ROUTE_ADDR_NAME = _NL_ROUTE_NAME + '/addr'  # libnl/lib/route/addr.c
_NL_ROUTE_LINK_NAME = _NL_ROUTE_NAME + '/link'  # libnl/lib/route/link.c


class NLSocketPool(object):
    """Pool of netlink sockets."""
    def __init__(self, size):
        if size <= 0:
            raise ValueError('Invalid socket pool size %r. Must be positive')
        self._semaphore = BoundedSemaphore(size)
        self._sockets = queue.Queue(maxsize=size)

    @contextmanager
    def socket(self):
        """Returns a socket from the pool (creating it when needed)."""
        with self._semaphore:
            try:
                sock = self._sockets.get_nowait()
            except queue.Empty:
                sock = _open_socket()
            try:
                yield sock
            finally:
                self._sockets.put_nowait(sock)


_pool = NLSocketPool(_POOL_SIZE)


def _open_socket(callback_function=None, callback_arg=None):
    """Returns an open netlink socket.
        callback_function: Modify the callback handler associated with the
        socket. Callback function requires two arguments:
            nl_message: netlink message passed by the socket
            args: optional argument defined by _nl_socket_modify_cb()
        callback_arg: optional argument passed to the callback function
    """
    sock = _nl_socket_alloc()
    if sock is None:
        raise IOError(get_errno(), 'Failed to allocate netlink handle')

    if callback_function is not None:
        # seq_check is by default enabled. We have to disable it in order to
        # allow messages, which were not requested by a preceding request
        # message, to be processed, e.g. netlink events.
        err = _nl_socket_disable_seq_check(sock)
        if err:
            _nl_socket_free(sock)
            raise IOError(-err, libnl.nl_geterror(err))

        # _nl_socket_modify_cb(socket, which type callback to set, kind of
        # callback, callback function, arguments to be passed to callback)
        err = _nl_socket_modify_cb(sock, _NL_CB_DEFAULT, _NL_CB_CUSTOM,
                                   callback_function, callback_arg)

    err = _nl_connect(sock, _NETLINK_ROUTE)
    if err:
        _nl_socket_free(sock)
        raise IOError(-err, libnl.nl_geterror(err))
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


# libnl/include/linux/rtnetlink.h
_GROUPS = {
    'link': 1,             # RTNLGRP_LINK
    'notify': 2,           # RTNPGRP_NOTIFY
    'neigh': 3,            # RTNLGRP_NEIGH
    'tc': 4,               # RTNLGRP_TC
    'ipv4-ifaddr': 5,      # RTNLGRP_IPV4_IFADDR
    'ipv4-mroute': 6,      # RTNLGRP_IPV4_MROUTE
    'ipv4-route': 7,       # RTNLGRP_IPV4_ROUTE
    'ipv6-ifaddr': 9,      # RTNLGRP_IPV6_IFADDR
    'ipv6-mroute': 10,     # RTNLGRP_IPV6_MROUTE
    'ipv6-route': 11,      # RTNLGRP_IPV6_ROUTE
    'ipv6-ifinfo': 12,     # RTNLGRP_IPV6_IFINFO
    'decnet-ifaddr': 13,   # RTNLGRP_DECnet_IFADDR
    'decnet-route': 14,    # RTNLGRP_DECnet_ROUTE
    'ipv6-prefix': 16}     # RTNLGRP_IPV6_PREFIX


def _socket_memberships(socket_membership_function, socket, groups):
    groups_codes = [_GROUPS[g] for g in groups]
    groups_codes = groups_codes + [0] * (len(_GROUPS) - len(groups_codes) + 1)
    err = socket_membership_function(socket, *groups_codes)
    if err:
        _nl_socket_free(socket)
        raise IOError(-err, libnl.nl_geterror(err))


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
_socket_memberships_proto = CFUNCTYPE(c_int, c_void_p,
                                      *((c_int,) * (len(_GROUPS) + 1)))

LIBNL_ROUTE = CDLL('libnl-route-3.so.200', use_errno=True)

_nl_socket_alloc = CFUNCTYPE(c_void_p)(('nl_socket_alloc', libnl.LIBNL))
_nl_socket_free = _none_proto(('nl_socket_free', libnl.LIBNL))

_nl_msg_parse = CFUNCTYPE(c_int, c_void_p, c_void_p, c_void_p)(
    ('nl_msg_parse', libnl.LIBNL))
_nl_socket_add_memberships = _socket_memberships_proto(
    ('nl_socket_add_memberships', libnl.LIBNL))
_nl_socket_disable_seq_check = _void_proto(('nl_socket_disable_seq_check',
                                            libnl.LIBNL))
_nl_socket_drop_memberships = _socket_memberships_proto(
    ('nl_socket_drop_memberships', libnl.LIBNL))
_nl_socket_get_fd = _int_proto(('nl_socket_get_fd', libnl.LIBNL))
_nl_socket_modify_cb = CFUNCTYPE(
    c_int, c_void_p, c_int, c_int, c_void_p, py_object)((
        'nl_socket_modify_cb', libnl.LIBNL))
_nl_object_get_type = _char_proto(('nl_object_get_type', libnl.LIBNL))
_nl_recvmsgs_default = _int_proto(('nl_recvmsgs_default', libnl.LIBNL))

_nl_connect = CFUNCTYPE(c_int, c_void_p, c_int)(('nl_connect', libnl.LIBNL))

_nl_cache_free = _none_proto(('nl_cache_free', libnl.LIBNL))
_nl_cache_get_first = _void_proto(('nl_cache_get_first', libnl.LIBNL))
_nl_cache_get_next = _void_proto(('nl_cache_get_next', libnl.LIBNL))
_nl_addr2str = CFUNCTYPE(c_char_p, c_void_p, c_char_p, c_size_t)((
    'nl_addr2str', libnl.LIBNL))
_nl_af2str = _int_char_proto(('nl_af2str', libnl.LIBNL))
_rtnl_scope2str = _int_char_proto(('rtnl_scope2str', LIBNL_ROUTE))


_add_socket_memberships = partial(_socket_memberships,
                                  _nl_socket_add_memberships)
_drop_socket_memberships = partial(_socket_memberships,
                                   _nl_socket_drop_memberships)
