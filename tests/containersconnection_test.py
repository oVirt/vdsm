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
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA
# 02110-1301  USA
#
# Refer to the README and COPYING files for full details of the license
#

from vdsm.virt.containers import docker

from vdsm import containersconnection
from testlib import VdsmTestCase as TestCaseBase
from monkeypatch import MonkeyPatchScope


class TruePath(object):
    def __init__(self):
        self.cmd = '/bin/true'


class NonePath(object):
    def __init__(self):
        self.cmd = None


class ContainersconnectionTests(TestCaseBase):

    def test_open_connection_succeeds(self):
        with MonkeyPatchScope([(docker, '_DOCKER', TruePath())]):
            conn = containersconnection.open_connection()
            self.assertIsNotNone(conn)

    def test_open_connection_raises(self):
        with MonkeyPatchScope([(docker, '_DOCKER', NonePath())]):
            self.assertRaises(containersconnection.NotAvailable,
                              containersconnection.open_connection)

    def test_get_succeeds(self):
        with MonkeyPatchScope([(docker, '_DOCKER', TruePath())]):
            conn = containersconnection.get()
            self.assertIsNotNone(conn)

    def test_get_raises(self):
        with MonkeyPatchScope([(docker, '_DOCKER', NonePath())]):
            self.assertRaises(containersconnection.NotAvailable,
                              containersconnection.get)

    def test_get_reuses_instances(self):
        with MonkeyPatchScope([(docker, '_DOCKER', TruePath())]):
            conn1 = containersconnection.get()
            conn2 = containersconnection.get()
            self.assertIsNotNone(conn1)
            self.assertIsNotNone(conn2)
            self.assertIs(conn1, conn2)
