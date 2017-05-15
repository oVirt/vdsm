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
- Test arguments are provided as native Python string.
- Returned text values are converted from binary to native Python string.
- Callback arguments are provided as native Python functions, binding function
  is responsible for converting them into CFUNCTYPE and keeping a reference.
- Values are returned only via 'return', never as a pointer argument.
- Errors are raised as exceptions, never as a return code.
"""

from __future__ import absolute_import

from ctypes import CDLL, CFUNCTYPE
from ctypes import c_char_p, c_int

from vdsm.common.cache import memoized

LIBNL = CDLL('libnl-3.so.200', use_errno=True)


def nl_geterror(error_code):
    """Return error message for an error code.

    @arg error_code      error code

    @return error message
    """
    _nl_geterror = _libnl('nl_geterror', c_char_p, c_int)
    error_message = _nl_geterror(error_code)
    return error_message.decode()


@memoized
def _libnl(function_name, return_type, *arguments):
    return CFUNCTYPE(return_type, *arguments)((function_name, LIBNL))
