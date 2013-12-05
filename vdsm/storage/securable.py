#
# Copyright 2011-2014 Red Hat, Inc.
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

from functools import wraps

OVERRIDE_ARG = "__securityOverride"
SECURE_FIELD = "__secured__"
SECURE_METHOD_NAME = "__is_secure__"


class SecureError(RuntimeError):
    pass


def secured(cls):
    """Secured class decorator.

    When a class is @secured the execution of its methods is permitted
    only when the __is_secure__ method returns True.
    The methods that are not subject to this check are the special methods
    (e.g. __init__) including __is_secure__, and all the methods that are
    marked with the @unsecured decorator.
    """

    if not callable(cls.__dict__.get(SECURE_METHOD_NAME)):
        raise NotImplementedError("Security method not implemented")

    for name, value in cls.__dict__.iteritems():
        # Skipping non callable attributes, special methods (including
        # SECURE_METHOD_NAME) and unsecured methods.
        if (not callable(value) or not getattr(value, SECURE_FIELD, True)
                or name.startswith("__")):
            continue
        setattr(cls, name, _secure_method(value))

    return cls


def unsecured(f):
    """Unsecured method decorator.

    This decorator is used to mark the methods (of a @secured class) that
    are not subject to the __is_secure__ execution check.
    """
    setattr(f, SECURE_FIELD, False)
    return f


def _secure_method(method):
    @wraps(method)
    def wrapper(self, *args, **kwargs):
        override = kwargs.pop(OVERRIDE_ARG, False)

        if not (getattr(self, SECURE_METHOD_NAME)() is True
                or override is True):
            raise SecureError("Secured object is not in safe state")

        return method(self, *args, **kwargs)

    return wrapper
