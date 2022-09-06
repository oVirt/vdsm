# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

"""
sysfs - helper for accessing sysfs attributes.
"""

from __future__ import absolute_import

import errno
import io
from vdsm.common import password


def read_int(path, default=None):
    """
    Return integer value of a sysfs attribute, or default if default is
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
    Return ProtectedPassword wrapping value of a sysfs attribute, or default if
    default is specified and the attribute does not exists.

    Raises:
        EnviromentError if reading the attribute fails, or default was not
            specified and the attribute does not exist.
    """
    value = read(path, default)
    return password.ProtectedPassword(value)


def read(path, default=None):
    """
    Return the contents of a sysfs attribute, or default if default is
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
