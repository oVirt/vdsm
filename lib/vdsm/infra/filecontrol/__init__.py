#
# Copyright 2014 Red Hat, Inc.
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
