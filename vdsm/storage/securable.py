#
# Copyright 2011 Red Hat, Inc.
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

from threading import Event
from functools import wraps

OVERRIDE_ARG = "__securityOverride"
SECURE_FIELD = "__secured__"


class SecureError(RuntimeError):
    pass


class MetaSecurable(type):
    def __new__(cls, name, bases, dct):
        for fun, val in dct.iteritems():
            if not callable(val):
                continue

            if (hasattr(val, SECURE_FIELD) and
                    not getattr(val, SECURE_FIELD)):
                continue

            if fun.startswith("__"):
                # Wrapping builtins might cause weird results
                continue

            dct[fun] = secured(val)

        dct['__securable__'] = True
        return type.__new__(cls, name, bases, dct)


def unsecured(f):
    setattr(f, SECURE_FIELD, False)
    return f


def secured(f):
    @wraps(f)
    def wrapper(self, *args, **kwargs):
        if not hasattr(self, "__securable__"):
            raise RuntimeError("Secured object is not a securable")

        override = kwargs.pop(OVERRIDE_ARG, False)

        if not (self._isSafe() or override):
            raise SecureError()

        return f(self, *args, **kwargs)

    return wrapper


class Securable(object):
    __metaclass__ = MetaSecurable

    def __init__(self):
        self._safety = Event()

    @unsecured
    def _isSafe(self):
        return self._safety.isSet()

    @unsecured
    def _setSafe(self):
        self._safety.set()

    @unsecured
    def _setUnsafe(self):
        self._safety.clear()
