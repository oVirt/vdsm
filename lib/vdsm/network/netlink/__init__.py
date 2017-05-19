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
from ctypes import CFUNCTYPE, c_char_p, c_int, c_void_p, c_size_t
from functools import partial
from threading import BoundedSemaphore

from six.moves import queue

from . import libnl

_POOL_SIZE = 5
_NETLINK_ROUTE = 0

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
    sock = libnl.nl_socket_alloc()
    try:
        if callback_function is not None:
            libnl.nl_socket_disable_seq_check(sock)
            libnl.nl_socket_modify_cb(sock, _NL_CB_DEFAULT, _NL_CB_CUSTOM,
                                      callback_function, callback_arg)

        libnl.nl_connect(sock, _NETLINK_ROUTE)
    except:
        libnl.nl_socket_free(sock)
        raise
    return sock


def _close_socket(sock):
    """Closes and frees the resources of the passed netlink socket."""
    libnl.nl_socket_free(sock)


@contextmanager
def _cache_manager(cache_allocator, sock):
    """Provides a cache using cache_allocator and frees it and its links upon
    exit."""
    cache = cache_allocator(sock)
    try:
        yield cache
    finally:
        _nl_cache_free(cache)


def _socket_memberships(socket_membership_function, socket, groups):
    groups_codes = [libnl.GROUPS[g] for g in groups]
    groups_codes = groups_codes + [0] * (
        len(libnl.GROUPS) - len(groups_codes) + 1)
    try:
        socket_membership_function(socket, *groups_codes)
    except:
        libnl.nl_socket_free(socket)
        raise


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

_nl_msg_parse = CFUNCTYPE(c_int, c_void_p, c_void_p, c_void_p)(
    ('nl_msg_parse', libnl.LIBNL))
_nl_object_get_type = _char_proto(('nl_object_get_type', libnl.LIBNL))
_nl_recvmsgs_default = _int_proto(('nl_recvmsgs_default', libnl.LIBNL))

_nl_cache_free = _none_proto(('nl_cache_free', libnl.LIBNL))
_nl_cache_get_first = _void_proto(('nl_cache_get_first', libnl.LIBNL))
_nl_cache_get_next = _void_proto(('nl_cache_get_next', libnl.LIBNL))

_add_socket_memberships = partial(_socket_memberships,
                                  libnl.nl_socket_add_memberships)
_drop_socket_memberships = partial(_socket_memberships,
                                   libnl.nl_socket_drop_memberships)
