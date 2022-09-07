# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import
from __future__ import division
from contextlib import contextmanager
import fcntl
import os

from testlib import VdsmTestCase
from vdsm.common import filecontrol


@contextmanager
def open_fd():
    r, w = os.pipe()
    try:
        yield r
    finally:
        os.close(r)
        os.close(w)


class TestFileControl(VdsmTestCase):
    def test_non_blocking(self):
        with open_fd() as fd:
            self.assertEqual(0, filecontrol.set_non_blocking(fd))
            self.assertTrue(os.O_NONBLOCK & fcntl.fcntl(fd, fcntl.F_GETFL))

    def test_blocking(self):
        with open_fd() as fd:
            self.assertEqual(0, filecontrol.set_non_blocking(fd, False))
            self.assertFalse(os.O_NONBLOCK & fcntl.fcntl(fd, fcntl.F_GETFL))

    def test_close_on_exec(self):
        with open_fd() as fd:
            self.assertEqual(0, filecontrol.set_close_on_exec(fd))
            self.assertTrue(fcntl.FD_CLOEXEC & fcntl.fcntl(fd, fcntl.F_GETFD))

    def test_no_close_on_exec(self):
        with open_fd() as fd:
            self.assertEqual(0, filecontrol.set_close_on_exec(fd, False))
            self.assertFalse(fcntl.FD_CLOEXEC & fcntl.fcntl(fd, fcntl.F_GETFD))
