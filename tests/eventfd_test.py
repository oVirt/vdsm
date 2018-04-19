#
# Copyright 2015-2016 Red Hat, Inc.
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
import fcntl
from nose import tools
from testlib import VdsmTestCase

from vdsm.common import eventfd


class TestEventFD(VdsmTestCase):
    def test_create(self):
        efd = eventfd.EventFD()
        tools.assert_not_equals(efd, None)

    def test_close(self):
        efd = eventfd.EventFD()
        efd.close()
        tools.assert_equals(efd.fileno(), -1)

    def test_read(self):
        value = 10
        efd = eventfd.EventFD(value)
        tools.assert_equals(efd.read(), value)

    def test_write(self):
        value = 10
        efd = eventfd.EventFD()
        efd.write(value)
        tools.assert_equals(efd.read(), value)

    def test_write_with_coe_flag(self):
        value = 10
        efd = self._set_flag(eventfd.EFD_CLOEXEC)
        efd.write(value)
        tools.assert_equals(efd.read(), value)

    def test_write_with_nbio_flag(self):
        value = 10
        efd = self._set_flag(eventfd.EFD_NONBLOCK)
        efd.write(value)
        tools.assert_equals(efd.read(), value)

    def test_write_with_sem_flag(self):
        value = 10
        efd = self._set_flag(eventfd.EFD_SEMAPHORE)
        efd.write(value)
        tools.assert_equals(efd.read(), 1)

    def _set_flag(self, flag):
        efd = eventfd.EventFD(flags=flag)
        tools.assert_equals(0, eventfd.EFD_CLOEXEC & fcntl.fcntl(
                            efd, fcntl.F_GETFD))
        return efd
