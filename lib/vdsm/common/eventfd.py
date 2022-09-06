# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import
from __future__ import division
"""
This is a module to implement a python wrapper for eventfd(2).

Please check the man page for complete information on eventfd(2).

Flags:

- EFD_CLOEXEC: Set the close-on-exec (FD_CLOEXEC) flag on the new file
  descriptor.
- EFD_NONBLOCK: Set the O_NONBLOCK file status flag on the new open file
  description.
- EFD_SEMAPHORE: Provide semaphore-like semantics for reads from the new file
  descriptor.
"""
import ctypes
import os


libc = ctypes.CDLL("libc.so.6", use_errno=True)

EFD_SEMAPHORE = 0o0000001
EFD_CLOEXEC = 0o2000000
EFD_NONBLOCK = 0o0004000


class EventFD(object):
    """
    Creates an "eventfd object" that can be used as an event wait/notify
    mechanism by user-space applications, and by the kernel to notify
    user-space applications of events.

    The object contains an unsigned 64-bit integer (uint64_t) counter that is
    maintained by the kernel.  This counter is initialized with the value
    specified in the argument initial_value.
    """
    def __init__(self, initial_value=0, flags=0):
        fd = libc.eventfd(
            ctypes.c_uint(initial_value),
            ctypes.c_int(flags)
        )
        self._verify_code(fd)
        self._fd = fd

    def fileno(self):
        "Return the number of the eventfd"
        return self._fd

    def read(self):
        """
        The semantics of read depend on whether the eventfd counter currently
        has a nonzero value and whether the EFD_SEMAPHORE flag was specified
        when creating the eventfd file descriptor:

        - If EFD_SEMAPHORE was not specified and the eventfd counter has a
          nonzero value, then a read() returns that value, and the counter's
          value is reset to zero.
        - If EFD_SEMAPHORE was specified and the eventfd counter has a nonzero
          value, then a read() the value 1, and the counter's value is
          decremented by 1.
        - If the eventfd counter is zero at the time of the call to read(),
          then the call either blocks until the counter becomes nonzero (at
          which time, the read() proceeds as described above) or fails with
          the error EAGAIN if the file descriptor has been made nonblocking.
        """
        n = ctypes.c_uint64()
        rv = libc.read(self._fd, ctypes.pointer(n),
                       ctypes.sizeof(ctypes.c_uint64))
        self._verify_code(rv)

        return int(n.value)

    def write(self, value=1):
        """
        A  write()  call  adds the integer value to the counter. The maximum
        value that may be stored in the counter is the largest unsigned 64-bit
        value minus 1 (i.e., 0xfffffffffffffffe). If the addition would cause
        the counter's value to exceed the maximum, then the write() either
        blocks until a read() is performed on the file descriptor, or fails
        with the error EAGAIN if the file descriptor has been made nonblocking.
        """
        n = ctypes.c_uint64(value)
        rv = libc.write(self._fd, ctypes.pointer(n),
                        ctypes.sizeof(ctypes.c_uint64))
        self._verify_code(rv)

    def _verify_code(self, code):
        if code < 0:
            err = ctypes.get_errno()
            if err != 0:
                msg = os.strerror(err)
                raise OSError(err, msg)

    def close(self):
        "Closes the fd and clears the internal counter"
        if self._fd != -1:
            libc.close(self._fd)
            self._fd = -1

    def __del__(self):
        self.close()
