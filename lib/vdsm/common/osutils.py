# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import
from __future__ import division
import errno
import os
import re
import select

from vdsm.common import time


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


def uninterruptible_poll(pollfun, timeout=-1):
    """
    This wrapper is used to handle the interrupt exceptions that might
    occur during a poll system call. The wrapped function must be defined
    as poll([timeout]) where the special timeout value 0 is used to return
    immediately and -1 is used to wait indefinitely.
    """
    # When the timeout < 0 we shouldn't compute a new timeout after an
    # interruption.
    endtime = None if timeout < 0 else time.monotonic_time() + timeout

    while True:
        try:
            return pollfun(timeout)
        except (IOError, select.error) as e:
            if e.args[0] != errno.EINTR:
                raise

        if endtime is not None:
            timeout = max(0, endtime - time.monotonic_time())


UMASK_RE = re.compile(r"^Umask:\t(\d+)$", re.M)


def get_umask():
    """
    This is a thread-safe implementation of umask retrieval.
    Same implementation to be used in both VDSM code and tests.
    """
    with open("/proc/self/status") as f:
        status = f.read()

    match = UMASK_RE.search(status)
    if match is None:
        raise RuntimeError("No umask in {!r}".format(status))

    return int(match.group(1), base=8)
