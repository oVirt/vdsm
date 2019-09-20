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
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA
#
# Refer to the README and COPYING files for full details of the license
#

from __future__ import absolute_import
from __future__ import division

import unittest

from dbus.exceptions import DBusException

from network.nettestlib import dummy_devices
from network.nettestlib import requires_systemctl
from network.nmnettestlib import iface_name, TEST_LINK_TYPE, NMService
from network.nmnettestlib import nm_connections

from vdsm.network.nm import errors
from vdsm.network.nm.nmdbus import NMDbus
from vdsm.network.nm.nmdbus import nmtypes
from vdsm.network.nm.nmdbus.active import NMDbusActiveConnections
from vdsm.network.nm.nmdbus.device import NMDbusDevice
from vdsm.network.nm.nmdbus.settings import NMDbusSettings


IPV4ADDR = '10.1.1.1/29'

_nm_service = None


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


class TestNMConnectionSettings(unittest.TestCase):
    def setUp(self):
        self.nm_settings = NMDbusSettings()
        self.iface = iface_name()

    def test_configured_connections_attributes_existence(self):
        with dummy_devices(1) as nics:
            with nm_connections(self.iface, IPV4ADDR, slaves=nics) as connames:
                nm_con = self._get_connection(connames[0])

                self.assertEqual(connames[0], nm_con.connection.id)
                self.assertIsNotNone(nm_con.connection.uuid)
                self.assertIsNotNone(nm_con.connection.type)

    def test_delete_one_of_two_connections(self):
        with dummy_devices(1) as nics:
            with nm_connections(
                self.iface, IPV4ADDR, slaves=nics, con_count=2
            ) as connames:

                con0 = self._get_connection(connames[0])
                con0.delete()
                self.assertIsNone(self._get_connection(connames[0]))

                con1 = self._get_connection(connames[1])
                self.assertEqual(connames[1], con1.connection.id)

    def _get_connection(self, con_name):
        for nm_con in self.nm_settings.connections():
            if nm_con.connection.id == con_name:
                return nm_con


class TestNMActiveConnections(unittest.TestCase):
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
                    connection_path = active_con.con_path()
                    settings_con = nm_settings.connection(connection_path)

                    assert active_con.uuid() == settings_con.connection.uuid
                    assert active_con.type() == settings_con.connection.type
                    assert active_con.id() == settings_con.connection.id


class TestNMDevice(unittest.TestCase):
    def test_device_attributes_existence(self):
        nm_device = NMDbusDevice()
        nm_settings = NMDbusSettings()

        device_count = 0
        iface = iface_name()
        with dummy_devices(1) as nics:
            with nm_connections(iface, IPV4ADDR, slaves=nics):
                for device in nm_device.devices():
                    try:
                        assert device.interface() is not None
                        assert device.state() is not None
                        assert device.active_connection_path() is not None
                        assert device.connections_path() is not None
                    except errors.NMPropertiesNotFoundError:
                        continue

                    for connection_path in device.connections_path():
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
            with nm_connections(
                iface, IPV4ADDR, slaves=nics, con_count=con_count
            ):
                device = nm_device.device(iface)
                for connection_path in device.connections_path():
                    settings_con = nm_settings.connection(connection_path)
                    configured_connections.add(settings_con.connection.id)

                ac = nm_act_cons.connection(device.active_connection_path())
                active_connections.add(ac.id())

        self.assertEqual(con_count, len(configured_connections))
        self.assertEqual(set([iface + '0']), active_connections)


class TestNMConnectionCreation(unittest.TestCase):
    def test_nm_connection_lifetime(self):
        nm_act_cons = NMDbusActiveConnections()
        nm_device = NMDbusDevice()

        iface = iface_name()
        with dummy_devices(1) as nics:
            with nm_connections(iface, IPV4ADDR, slaves=nics):
                device = nm_device.device(iface)
                device.syncoper.waitfor_activated_state()
                active_con_path = device.active_connection_path()
                active_con = nm_act_cons.connection(active_con_path)

                self.assertEqual(TEST_LINK_TYPE, str(active_con.type()))
                self.assertEqual(
                    nmtypes.NMActiveConnectionState.ACTIVATED,
                    active_con.state(),
                )

        self._assert_no_device(iface)

    def _assert_no_device(self, iface):
        nm_device = NMDbusDevice()
        with self.assertRaises(errors.NMDeviceNotFoundError):
            nm_device.device(iface)
