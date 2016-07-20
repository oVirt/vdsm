#
# Copyright 2016 Red Hat, Inc.
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
import errno
import os


def close_fd(fd):
    """
    Close fd once, ignoring EINTR.

    close(2) warns not to retry close() after EINTR:

        Note that the return value should be used only for diagnostics. In
        particular close() should not be retried after an EINTR since this
        may cause a reused descriptor from another thread to be closed.

    See also this discussion about close and EINTR on linux:
    http://lwn.net/Articles/576478/
    """
    try:
        os.close(fd)
    except EnvironmentError as e:
        if e.errno != errno.EINTR:
            raise


def uninterruptible(func, *args, **kwargs):
    """
    Call func with *args and *kwargs and return the result, retrying if func
    failed with EINTR. This may happen if func invoked a system call and the
    call was interrupted by signal.

    WARNING: Use only with functions which are safe to restart after EINTR.
    """
    while True:
        try:
            return func(*args, **kwargs)
        except EnvironmentError as e:
            if e.errno != errno.EINTR:
                raise
