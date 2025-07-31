# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import
from __future__ import division

import copy


class ProtectedPassword(object):
    """
    Protect a password so it will not be logged or serialized by mistake.
    """
    def __init__(self, value):
        self.value = value

    def __eq__(self, other):
        return isinstance(other, type(self)) and self.value == other.value

    def __ne__(self, other):
        return not self.__eq__(other)

    def __str__(self):
        return "********"

    def __repr__(self):
        return repr(str(self))

    def __hash__(self):
        return hash((self.__class__, self.value))


def protect_passwords(obj):
    """
    Replace "password" values with ProtectedPassword() object.

    Accept a dict, list of dicts or nested structure containing these types.
    """
    for d, key, value in _walk(obj):
        d[key] = ProtectedPassword(value)
    return obj


def unprotect_passwords(obj):
    """
    Return `obj` with `ProtectedPassword` objects replaced by actual values.

    Accept a dict, list of dicts or nested structure containing these types.
    The original `obj` remains unmodified.
    """
    obj = copy.deepcopy(obj)
    for d, key, value in _walk(obj):
        if isinstance(value, ProtectedPassword):
            d[key] = value.value
    return obj


def unprotect(obj):
    """
    If obj is a protected password, return the protected value. Otherwise
    returns obj.
    """
    if isinstance(obj, ProtectedPassword):
        return obj.value
    return obj


def _walk(obj):
    if isinstance(obj, dict):
        for key, value in obj.items():
            if key == "password" or \
               isinstance(key, str) and key.startswith("_X_"):
                yield obj, key, value
            elif isinstance(value, (dict, list)):
                for d, k, v in _walk(value):
                    yield d, k, v
    elif isinstance(obj, list):
        for item in obj:
            for d, k, v in _walk(item):
                yield d, k, v
