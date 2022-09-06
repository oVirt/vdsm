# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import
from __future__ import division
import fcntl
import os


def _set_or_clear_bits(set, word, bits):
    if set:
        return word | bits
    else:
        return word & (~bits)


def set_non_blocking(fd, value=True):
    """Set O_NONBLOCK flag on file descriptor"""
    flags = fcntl.fcntl(fd, fcntl.F_GETFL)
    flags = _set_or_clear_bits(value, flags, os.O_NONBLOCK)
    return fcntl.fcntl(fd, fcntl.F_SETFL, flags)


def set_close_on_exec(fd, value=True):
    """Set O_CLOEXEC flag on file descriptor"""
    flags = fcntl.fcntl(fd, fcntl.F_GETFD)
    flags = _set_or_clear_bits(value, flags, fcntl.FD_CLOEXEC)
    return fcntl.fcntl(fd, fcntl.F_SETFD, flags)
