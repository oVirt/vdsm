#
# Copyright 2016-2018 Red Hat, Inc.
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
#

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

from unittest import mock

import dbus

from testlib import VdsmTestCase as TestCaseBase

from vdsm.common import hostutils


class TestHostInShutdown(TestCaseBase):

    def test_host_in_shutdown(self):
        with mock.patch('dbus.SystemBus') as mock_sysbus:
            instance = mock_sysbus()
            obj = instance.get_object()
            obj.ListJobs.return_value = (('shutdown.target', 'start'),)
            in_shutdown = hostutils.host_in_shutdown()

        self.assertEqual(in_shutdown, True)

    def test_host_not_in_shutdown(self):
        with mock.patch('dbus.SystemBus') as mock_sysbus:
            instance = mock_sysbus()
            obj = instance.get_object()
            obj.ListJobs.return_value = ()
            in_shutdown = hostutils.host_in_shutdown()

        self.assertEqual(in_shutdown, False)

    def test_dbus_exception(self):
        with mock.patch('dbus.SystemBus') as mock_sysbus:
            mock_sysbus.side_effect = dbus.DBusException()
            in_shutdown = hostutils.host_in_shutdown()

        self.assertEqual(in_shutdown, False)
