# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import
import inspect
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

    for name, value in cls.__dict__.items():
        # Skipping non callable attributes, special methods (including
        # SECURE_METHOD_NAME) and unsecured methods.
        if (not inspect.isfunction(value) or
                not getattr(value, SECURE_FIELD, True) or
                name.startswith("__")):
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

        if not (getattr(self, SECURE_METHOD_NAME)() is True or
                override is True):
            raise SecureError("Secured object is not in safe state")

        return method(self, *args, **kwargs)

    return wrapper
