#
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
#

"""
sysfs - helper for accessing sysfs attributes.
"""

from __future__ import absolute_import

import errno
import io
from vdsm.common import password


def read_int(path, default=None):
    """
    Return integer value of a sysfs attribute, or defualt if default is
    specified and the attribute does not exist.

    Raises:
        EnviromentError if reading the attribute fails, or default was not
            specified and the attribute does not exist.
        ValueError if the attribute is cannot be parsed as int
    """
    value = read(path, default)
    return int(value)


def read_password(path, default=None):
    """
    Return ProtectedPassword wrapping value of a sysfs attribute, or defualt if
    default is specified and the attribute does not exists.

    Raises:
        EnviromentError if reading the attribute fails, or default was not
            specified and the attribute does not exist.
    """
    value = read(path, default)
    return password.ProtectedPassword(value)


def read(path, default=None):
    """
    Return the contents of a sysfs attribute, or defualt if default is
    specified and the attribute does not exist.

    Raises:
        EnviromentError if reading the attribute fails, or default was not
            specified and the attribute does not exist.
    """
    try:
        with io.open(path, "r") as f:
            value = f.read()
    except EnvironmentError as e:
        if e.errno != errno.ENOENT or default is None:
            raise
        return default
    else:
        return value.strip()
