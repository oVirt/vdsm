# Copyright 2016-2018 Red Hat, Inc.
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
from __future__ import division

import functools
import sys

from dbus.exceptions import DBusException
import six


class NMDeviceNotFoundError(Exception):
    pass


class NMPropertiesNotFoundError(Exception):
    pass


class NMConnectionNotFoundError(Exception):
    pass


class NMTimeoutError(Exception):
    pass


def nmerror_dev_not_found():
    return nmerror(
        src_exception=DBusException,
        dst_exception=NMDeviceNotFoundError,
        predicate=lambda ex: ex.args[0] == 'No device found for '
        'the requested iface.',
    )


def nmerror_properties_not_found():
    return nmerror(
        src_exception=DBusException,
        dst_exception=NMPropertiesNotFoundError,
        predicate=lambda ex: ex.args[0].startswith(
            "No such interface 'org.freedesktop.DBus.Properties' on object"
        ),
    )


def nmerror(src_exception, dst_exception, predicate):
    def wrapper(func):
        @functools.wraps(func)
        def wrapped_func(*args, **kwargs):
            try:
                return func(*args, **kwargs)
            except src_exception as ex:
                if predicate(ex):
                    _, value, tb = sys.exc_info()
                    six.reraise(dst_exception, dst_exception(*value.args), tb)
                raise

        return wrapped_func

    return wrapper
