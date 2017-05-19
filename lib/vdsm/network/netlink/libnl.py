# Copyright 2017 Red Hat, Inc.
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

"""libnl and libnl-route bindings.

This module provides libnl functions bindings for Python. All ctypes imports
should be contained in this package, provided functions should be usable in
native Python manner.

- Functions have same names as their C counterparts.
- Text arguments are provided as native Python string (bytes in Python 2,
  unicode in Python 3).
- Returned text values are converted to native Python string.
- Values are returned only via 'return', never as a pointer argument.
- Errors are raised as exceptions, never as a return code.
"""

from __future__ import absolute_import

from ctypes import CDLL, CFUNCTYPE, sizeof, get_errno
from ctypes import c_char, c_char_p, c_int, c_void_p, c_size_t, py_object

from vdsm.common.cache import memoized

LIBNL = CDLL('libnl-3.so.200', use_errno=True)
LIBNL_ROUTE = CDLL('libnl-route-3.so.200', use_errno=True)

CHARBUFFSIZE = 40  # Increased to fit IPv6 expanded representations
HWADDRSIZE = 60    # InfiniBand HW address needs 59+1 bytes

# libnl/include/linux/rtnetlink.h
GROUPS = {
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
    'ipv6-prefix': 16      # RTNLGRP_IPV6_PREFIX
}


def nl_geterror(error_code):
    """Return error message for an error code.

    @arg error_code      error code

    @return error message
    """
    _nl_geterror = _libnl('nl_geterror', c_char_p, c_int)
    error_message = _nl_geterror(error_code)
    return _to_str(error_message)


def nl_addr2str(addr):
    """Convert abstract address object to string.

    @arg addr            Abstract address object.

    @return Address represented as string
    """
    _nl_addr2str = _libnl(
        'nl_addr2str', c_char_p, c_void_p, c_char_p, c_size_t)
    buf = (c_char * HWADDRSIZE)()
    address = _nl_addr2str(addr, buf, sizeof(buf))
    return _to_str(address)


def nl_af2str(family):
    """Convert address family code to string.

    @arg family          Address family code.

    @return Address family represented as string
    """
    _nl_af2str = _libnl('nl_af2str', c_char_p, c_int, c_char_p, c_size_t)
    buf = (c_char * CHARBUFFSIZE)()
    address_family = _nl_af2str(family, buf, sizeof(buf))
    return _to_str(address_family)


def rtnl_scope2str(scope):
    """Convert address scope code to string.

    @arg scope           Address scope code.

    @return Address scope represented as string
    """
    _rtnl_scope2str = _libnl_route(
        'rtnl_scope2str', c_char_p, c_int, c_char_p, c_size_t)
    buf = (c_char * CHARBUFFSIZE)()
    address_scope = _rtnl_scope2str(scope, buf, sizeof(buf))
    return _to_str(address_scope)


def nl_socket_alloc():
    """Allocate new netlink socket.

    @return Newly allocated netlink socket.
    """
    _nl_socket_alloc = _libnl('nl_socket_alloc', c_void_p)
    allocated_socket = _nl_socket_alloc()
    if allocated_socket is None:
        raise IOError(get_errno(), 'Failed to allocate socket.')
    return allocated_socket


def nl_connect(socket, protocol):
    """Create file descriptor and bind socket.

    @arg socket          Netlink socket
    @arg protocol        Netlink protocol to use
    """
    _nl_connect = _libnl('nl_connect', c_int, c_void_p, c_int)
    err = _nl_connect(socket, protocol)
    if err:
        raise IOError(-err, nl_geterror(err))


def nl_socket_free(socket):
    """Free a netlink socket.

    @arg socket          Netlink socket.
    """
    _nl_socket_free = _libnl('nl_socket_free', None, c_void_p)
    _nl_socket_free(socket)


def nl_socket_get_fd(socket):
    """Return the file descriptor of the backing socket.

    @arg socket          Netlink socket

    Only valid after calling nl_connect() to create and bind the respective
    socket.

    @return File descriptor.
    """
    _nl_socket_get_fd = _libnl('nl_socket_get_fd', c_int, c_void_p)
    file_descriptor = _nl_socket_get_fd(socket)
    if file_descriptor == -1:
        raise IOError(get_errno(), 'Failed to obtain socket file descriptor.')
    return file_descriptor


def nl_socket_add_memberships(socket, *groups):
    """Join groups.

    @arg socket          Netlink socket
    @arg group           Group identifier
    """
    _nl_socket_add_memberships = _libnl(
        'nl_socket_add_memberships',
        c_int, c_void_p, *((c_int,) * (len(GROUPS) + 1)))
    err = _nl_socket_add_memberships(socket, *groups)
    if err:
        raise IOError(-err, nl_geterror(err))


def nl_socket_drop_memberships(socket, *groups):
    """Leave groups.

    @arg socket          Netlink socket
    @arg group           Group identifier
    """
    _nl_socket_drop_memberships = _libnl(
        'nl_socket_drop_memberships',
        c_int, c_void_p, *((c_int,) * (len(GROUPS) + 1)))
    err = _nl_socket_drop_memberships(socket, *groups)
    if err:
        raise IOError(-err, nl_geterror(err))


def nl_socket_modify_cb(socket, cb_type, kind, function, argument):
    """Modify the callback handler associated with the socket.

    @arg socket          Netlink socket.
    @arg cb_type         which type callback to set
    @arg kind            kind of callback
    @arg function        callback function (CFUNCTYPE)
    @arg argument        argument to be passed to callback function
    """
    _nl_socket_modify_cb = _libnl(
        'nl_socket_modify_cb',
        c_int, c_void_p, c_int, c_int, c_void_p, py_object)
    err = _nl_socket_modify_cb(socket, cb_type, kind, function, argument)
    if err:
        raise IOError(-err, nl_geterror(err))


def prepare_cfunction_for_nl_socket_modify_cb(function):
    """Prepare callback function for nl_socket_modify_cb.

    @arg                  Python function accepting two objects (message and
                          extra argument) as arguments and returns integer
                          with libnl callback action.

    @return C function prepared for nl_socket_modify_cb.
    """
    c_function = CFUNCTYPE(c_int, c_void_p, c_void_p)(function)
    return c_function


def nl_socket_disable_seq_check(socket):
    """Disable sequence number checking.

    @arg socket          Netlink socket.

    Disables checking of sequence numbers on the netlink socket This is
    required to allow messages to be processed which were not requested by
    a preceding request message, e.g. netlink events.
    """
    _nl_socket_disable_seq_check = _libnl(
        'nl_socket_disable_seq_check', c_void_p, c_void_p)
    _nl_socket_disable_seq_check(socket)


def nl_cache_get_first(cache):
    """Return the first element in the cache.

    @arg cache           cache handle

    @return the first element in the cache or None if empty
    """
    _nl_cache_get_first = _libnl('nl_cache_get_first', c_void_p, c_void_p)
    return _nl_cache_get_first(cache)


def nl_cache_get_next(element):
    """Return the next element in the cache

    @arg element         current element

    @return the next element in the cache or None if reached the end
    """
    _nl_cache_get_next = _libnl('nl_cache_get_next', c_void_p, c_void_p)
    return _nl_cache_get_next(element)


def nl_cache_free(cache):
    """Free a cache.

    @arg cache           Cache to free.

    Calls nl_cache_clear() to remove all objects associated with the
    cache and frees the cache afterwards.
    """
    _nl_cache_free = _libnl('nl_cache_free', None, c_void_p)
    _nl_cache_free(cache)


def c_object_argument(argument):
    """Prepare prepare Python object to be used as an C argument.

    @arg                  Python object.

    Reference to the returned object must be kept by caller as long as it might
    be used by any C binding function (beware of callback arguments).

    @return C object (py_object) prepared to be used as an C argument.
    """
    return py_object(argument)


@memoized
def _libnl(function_name, return_type, *arguments):
    return CFUNCTYPE(return_type, *arguments)((function_name, LIBNL))


@memoized
def _libnl_route(function_name, return_type, *arguments):
    return CFUNCTYPE(return_type, *arguments)((function_name, LIBNL_ROUTE))


def _to_str(value):
    """Convert textual value to native string.

    Passed value (bytes output of libnl CFUNCTYPE) will be returned as a native
    str value (bytes in Python 2, unicode in Python 3).
    """
    if isinstance(value, str):
        return value
    elif isinstance(value, bytes):
        return value.decode('utf-8')
    else:
        raise ValueError(
            'Expected a textual value, given {} of type {}.'.format(
                value, type(value)))
