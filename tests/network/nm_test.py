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
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA  02110-1301 USA
#
# Refer to the README and COPYING files for full details of the license
#
from __future__ import absolute_import

from nose.plugins.attrib import attr

from dbus.exceptions import DBusException

from testlib import VdsmTestCase
from testValidation import broken_on_ci, ValidateRunningAsRoot

from .nmnettestlib import iface_name, NMService, nm_connections

from vdsm.network.nm import networkmanager


IPV4ADDR = '10.1.1.1/29'

_nm_service = None


@broken_on_ci('NetworkManager should not be started on CI nodes')
@ValidateRunningAsRoot
def setup_module():
    global _nm_service
    _nm_service = NMService()
    _nm_service.setup()
    try:
        networkmanager.init()
    except DBusException as ex:
        # Unfortunately, nose labeling does not operate on module fixtures.
        # We let the test fail if init was not successful.
        if 'Failed to connect to socket' not in ex.args[0]:
            raise


def teardown_module():
    _nm_service.teardown()


@attr(type='functional')
class TestNMService(VdsmTestCase):

    def test_network_manager_service_is_running(self):
        self.assertTrue(networkmanager.is_running())


@attr(type='functional')
class TestNMConnectionCleanup(VdsmTestCase):

    def test_remove_all_non_active_connection_from_a_device(self):
        iface = iface_name()
        with nm_connections(iface, IPV4ADDR, con_count=3):

            device = networkmanager.Device(iface)
            device.cleanup_inactive_connections()

            self.assertEqual(1, sum(1 for _ in device.connections()))
