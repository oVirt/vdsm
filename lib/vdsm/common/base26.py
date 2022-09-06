# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import
from __future__ import division

"""
Functions to encode and decode base 10 values to base 26, which are used
by the Linux kernel to construct the storage device node names (e.g. vda,
sdb).

Link to (one of) the Kernel implementation:
http://elixir.free-electrons.com/linux/latest/source/drivers/scsi/sd.c#L3155
"""


def encode(index):
    """
    Converts the given base 10 integer index to
    the corresponding base 26 string value.
    """

    value = ''

    i = int(index)
    if i < 0:
        raise ValueError('invalid index: %i' % i)

    while i >= 0:
        value = chr(ord('a') + (i % 26)) + value
        i = (i // 26) - 1

    return value


def decode(value):
    """
    Converts the given base 26 string value to
    the corresponding base 10 integer index.
    """

    index = 0
    for pos, char in enumerate(reversed(value)):
        val = ord(char) - ord('a') + 1
        if val < 1 or val > 26:
            raise ValueError('Invalid character %r in %r' % (char, value))
        index += val * (26 ** pos)

    return index - 1
