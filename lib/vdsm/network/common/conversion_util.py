# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later


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
