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
