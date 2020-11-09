# Copyright 2017-2020 Red Hat, Inc.
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


def to_str(value):
    """Convert textual value to native string.

    Passed value will be returned as a native str value (unicode in Python 3).
    """
    if not isinstance(value, (str, bytes)):
        raise ValueError(
            f'Expected a textual value, given {value} of type {type(value)}.'
        )
    elif isinstance(value, bytes):
        return value.decode('utf-8')
    return value


def to_binary(value):
    """Convert textual value to binary."""
    if isinstance(value, bytes):
        return value
    else:
        return value.encode('utf-8')
