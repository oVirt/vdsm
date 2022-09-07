# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

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
