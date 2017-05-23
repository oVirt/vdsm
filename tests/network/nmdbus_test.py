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

from nose.plugins.attrib import attr

from dbus.exceptions import DBusException

from testlib import VdsmTestCase
from testValidation import ValidateRunningAsRoot

from .nettestlib import dummy_devices
from .nettestlib import requires_systemctl
from .nmnettestlib import iface_name, TEST_LINK_TYPE, NMService, nm_connections

from vdsm.network.nm.errors import NMDeviceNotFoundError
from vdsm.network.nm.nmdbus import NMDbus
from vdsm.network.nm.nmdbus import types
from vdsm.network.nm.nmdbus.active import NMDbusActiveConnections
from vdsm.network.nm.nmdbus.device import NMDbusDevice
from vdsm.network.nm.nmdbus.settings import NMDbusSettings


IPV4ADDR = '10.1.1.1/29'

_nm_service = None


@ValidateRunningAsRoot
@requires_systemctl
def setup_module():
    global _nm_service
    _nm_service = NMService()
    _nm_service.setup()
    try:
        NMDbus.init()
    except DBusException as ex:
        # Unfortunately, nose labeling does not operate on module fixtures.
        # We let the test fail if init was not successful.
        if 'Failed to connect to socket' not in ex.args[0]:
            raise


def teardown_module():
    _nm_service.teardown()


@attr(type='integration')
class TestNMConnectionSettings(VdsmTestCase):

    def test_configured_connections_attributes_existence(self):
        nm_settings = NMDbusSettings()

        iface = iface_name()
        with dummy_devices(1) as nics:
            with nm_connections(iface, IPV4ADDR, slaves=nics):
                con_count = 0
                for nm_con in nm_settings.connections():
                    if nm_con.connection.type in ('802-11-wireless', 'vpn'):
                        continue

                    assert nm_con.connection.id is not None
                    assert nm_con.connection.uuid is not None
                    assert nm_con.connection.type is not None

                    con_count += 1

                self.assertGreaterEqual(con_count, 1)

    def test_delete_a_non_active_connection(self):
        nm_settings = NMDbusSettings()

        iface = iface_name()
        with dummy_devices(1) as nics:
            with nm_connections(iface, IPV4ADDR, slaves=nics, con_count=2):
                con_count_pre_delete = sum(1
                                           for _ in nm_settings.connections())
                con = self._connection_to_delete(nm_settings, iface + '0')

                con.delete()

                con_count_post_delete = sum(
                    1 for _ in nm_settings.connections())
                self.assertEqual(
                    con_count_pre_delete, con_count_post_delete + 1)

    @staticmethod
    def _connection_to_delete(nm_settings, con_name):
        for nm_con in nm_settings.connections():
            if nm_con.connection.id == con_name:
                return nm_con


@attr(type='integration')
class TestNMActiveConnections(VdsmTestCase):

    def test_active_connections_properties_existence(self):
        nm_active_cons = NMDbusActiveConnections()

        iface = iface_name()
        with dummy_devices(1) as nics:
            with nm_connections(iface, IPV4ADDR, slaves=nics):
                con_count = 0
                for connection in nm_active_cons.connections():
                    assert connection.id is not None
                    assert connection.uuid is not None
                    assert connection.type is not None
                    assert connection.master_con_path is not None

                    con_count += 1

                self.assertGreaterEqual(con_count, 1)

    def test_active_connections_properties_vs_connection_settings(self):
        nm_active_cons = NMDbusActiveConnections()
        nm_settings = NMDbusSettings()

        iface = iface_name()
        with dummy_devices(1) as nics:
            with nm_connections(iface, IPV4ADDR, slaves=nics):
                for active_con in nm_active_cons.connections():
                    settings_con = nm_settings.connection(active_con.con_path)

                    assert active_con.uuid == settings_con.connection.uuid
                    assert active_con.type == settings_con.connection.type
                    assert active_con.id == settings_con.connection.id


@attr(type='integration')
class TestNMDevice(VdsmTestCase):

    def test_device_attributes_existence(self):
        nm_device = NMDbusDevice()
        nm_settings = NMDbusSettings()

        device_count = 0
        for device in nm_device.devices():
            assert device.interface is not None
            assert device.state is not None
            assert device.active_connection_path is not None
            assert device.connections_path is not None

            for connection_path in device.connections_path:
                settings_con = nm_settings.connection(connection_path)
                assert settings_con.connection.uuid is not None

            device_count += 1

        self.assertGreaterEqual(device_count, 1)

    def test_device_with_single_connection(self):
        self._test_device_with_n_connections(1)

    def test_device_with_multiple_connections(self):
        self._test_device_with_n_connections(2)

    def _test_device_with_n_connections(self, con_count):
        nm_device = NMDbusDevice()
        nm_settings = NMDbusSettings()
        nm_act_cons = NMDbusActiveConnections()

        configured_connections = set()
        active_connections = set()

        iface = iface_name()
        with dummy_devices(1) as nics:
            with nm_connections(iface, IPV4ADDR, slaves=nics,
                                con_count=con_count):
                device = nm_device.device(iface)
                for connection_path in device.connections_path:
                    settings_con = nm_settings.connection(connection_path)
                    configured_connections.add(settings_con.connection.id)

                ac = nm_act_cons.connection(device.active_connection_path)
                active_connections.add(ac.id)

        self.assertEqual(con_count, len(configured_connections))
        self.assertEqual(set([iface + '0']), active_connections)


@attr(type='integration')
class TestNMConnectionCreation(VdsmTestCase):

    def test_nm_connection_lifetime(self):
        nm_act_cons = NMDbusActiveConnections()
        nm_device = NMDbusDevice()

        iface = iface_name()
        with dummy_devices(1) as nics:
            with nm_connections(iface, IPV4ADDR, slaves=nics):
                device = nm_device.device(iface)
                active_con_path = device.active_connection_path
                active_con = nm_act_cons.connection(active_con_path)

                self.assertEqual(TEST_LINK_TYPE, str(active_con.type))
                self.assertEqual(types.NMActiveConnectionState.ACTIVATED,
                                 active_con.state)

        self._assert_no_device(iface)

    def _assert_no_device(self, iface):
        nm_device = NMDbusDevice()
        with self.assertRaises(NMDeviceNotFoundError):
            nm_device.device(iface)
