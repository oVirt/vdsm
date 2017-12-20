#
# Copyright 2018 Red Hat, Inc.
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
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA  02110-1301 USA
#
# Refer to the README and COPYING files for full details of the license

from __future__ import absolute_import

import errno
import os
import fcntl
from . import filecontrol


class LockError(Exception):
    """ Raised when failed to get lock """


def lock(filename):
    """
    This function is used for process level lock taken for the entire process
    lifetime. Additional process of the same instance will raise LockError in
    case the first instance was not properly stopped.
    """
    try:
        # following lock file fd is explicitly "leaked" to avoid using it
        # by another process instance. It will be closed when process
        # terminates
        lockfile = os.path.join(filename)
        fd = os.open(lockfile, os.O_RDWR | os.O_CREAT)
        filecontrol.set_close_on_exec(fd)

        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except EnvironmentError as e:
        if e.errno == errno.EWOULDBLOCK:
            raise LockError("Instance already running")
        else:
            raise LockError(str(e))
