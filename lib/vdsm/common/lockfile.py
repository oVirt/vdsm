# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import
from __future__ import division

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
