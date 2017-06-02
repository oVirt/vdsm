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
- Callback arguments are provided as native Python functions, binding function
  is responsible for converting them into CFUNCTYPE and keeping a reference.
- Values are returned only via 'return', never as a pointer argument.
- Errors are raised as exceptions, never as a return code.
"""

from __future__ import absolute_import

from ctypes import CDLL, CFUNCTYPE, sizeof
from ctypes import c_char, c_char_p, c_int, c_void_p, c_size_t

from vdsm.common.cache import memoized

LIBNL = CDLL('libnl-3.so.200', use_errno=True)
LIBNL_ROUTE = CDLL('libnl-route-3.so.200', use_errno=True)

CHARBUFFSIZE = 40  # Increased to fit IPv6 expanded representations
HWADDRSIZE = 60    # InfiniBand HW address needs 59+1 bytes


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
