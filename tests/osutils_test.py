# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import
from __future__ import division
import errno
import os
import time

from testlib import VdsmTestCase
from testlib import forked
from vdsm.common import osutils


class TestCloseFd(VdsmTestCase):

    # Run in a child process to ensure single thread. Otherwise another thread
    # opening a file descriptor may cause false failure.
    @forked
    def test_close(self):
        fds = os.pipe()
        for fd in fds:
            osutils.close_fd(fd)
        time.sleep(0.1)
        for fd in fds:
            path = "/proc/self/fd/%d" % fd
            self.assertFalse(os.path.exists(path))

    def test_propagate_other_errors(self):
        with self.assertRaises(OSError) as e:
            osutils.close_fd(-1)
        self.assertEqual(e.exception.errno, errno.EBADF)


class TestUniterruptible(VdsmTestCase):

    def test_retry_on_eintr(self):
        count = [0]

        def fail(n):
            count[0] += 1
            if count[0] == n:
                return n
            raise OSError(errno.EINTR, "Fake error")

        self.assertEqual(osutils.uninterruptible(fail, 3), 3)

    def test_raise_other(self):

        def fail():
            raise OSError(0, "Fake error")

        self.assertRaises(OSError, osutils.uninterruptible, fail)

    def test_args_kwargs(self):

        def func(*args, **kwargs):
            return args, kwargs

        self.assertEqual(osutils.uninterruptible(func, "a", "b", c=3),
                         (("a", "b"), {"c": 3}))
