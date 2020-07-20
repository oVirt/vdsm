# Copyright 2016-2020 Red Hat, Inc.
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

import pytest

from dbus.exceptions import DBusException

from network.nettestlib import dummy_device
from network.nmnettestlib import iface_name
from network.nmnettestlib import NMService
from network.nmnettestlib import nm_connections
from network.nmnettestlib import TEST_LINK_TYPE

from vdsm.network.nm import errors
from vdsm.network.nm.nmdbus import NMDbus
from vdsm.network.nm.nmdbus import nmtypes
from vdsm.network.nm.nmdbus.active import NMDbusActiveConnections
from vdsm.network.nm.nmdbus.device import NMDbusDevice
from vdsm.network.nm.nmdbus.settings import NMDbusSettings

from .netintegtestlib import requires_systemctl


IFACE = iface_name()
IPV4ADDR = '10.1.1.1/29'


@pytest.fixture(scope='module', autouse=True)
def setup():
    requires_systemctl()
    nm_service = NMService()
    nm_service.setup()
    try:
        NMDbus.init()
    except DBusException as ex:
        if 'Failed to connect to socket' in ex.args[0]:
            pytest.skip('dbus socket or the NM service may not be available')
    yield
    nm_service.teardown()


@pytest.fixture
def nic0():
    with dummy_device() as nic:
        yield nic


@pytest.fixture(scope='module')
def nmd_bus():
    return {
        'active_cons': NMDbusActiveConnections(),
        'device': NMDbusDevice(),
        'settings': NMDbusSettings(),
    }


class TestNMConnectionSettings(object):
    def test_configured_connections_attributes_existence(self, nic0, nmd_bus):
        with nm_connections(IFACE, IPV4ADDR, slaves=(nic0,)) as connames:
            nm_con = self._get_connection(connames[0], nmd_bus)

            assert nm_con.connection.id == connames[0]
            assert nm_con.connection.uuid is not None
            assert nm_con.connection.type is not None

    def test_delete_one_of_two_connections(self, nic0, nmd_bus):
        with nm_connections(
            IFACE, IPV4ADDR, slaves=(nic0,), con_count=2
        ) as connames:

            con0 = self._get_connection(connames[0], nmd_bus)
            con0.delete()
            assert self._get_connection(connames[0], nmd_bus) is None

            con1 = self._get_connection(connames[1], nmd_bus)
            assert con1.connection.id == connames[1]

    def _get_connection(self, con_name, nmd_bus):
        for nm_con in nmd_bus['settings'].connections():
            if nm_con.connection.id == con_name:
                return nm_con


class TestNMActiveConnections(object):
    def test_active_connections_properties_existence(self, nic0, nmd_bus):
        with nm_connections(IFACE, IPV4ADDR, slaves=(nic0,)):
            con_count = 0
            for connection in nmd_bus['active_cons'].connections():
                assert connection.id is not None
                assert connection.uuid is not None
                assert connection.type is not None
                assert connection.master_con_path is not None

                con_count += 1

            assert con_count > 0

    def test_active_connections_properties_vs_connection_settings(
        self, nic0, nmd_bus
    ):
        with nm_connections(IFACE, IPV4ADDR, slaves=(nic0,)):
            for active_con in nmd_bus['active_cons'].connections():
                connection_path = active_con.con_path()
                settings_con = nmd_bus['settings'].connection(connection_path)

                assert active_con.uuid() == settings_con.connection.uuid
                assert active_con.type() == settings_con.connection.type
                assert active_con.id() == settings_con.connection.id


class TestNMDevice(object):
    def test_device_attributes_existence(self, nic0, nmd_bus):
        device_count = 0
        with nm_connections(IFACE, IPV4ADDR, slaves=(nic0,)):
            for device in nmd_bus['device'].devices():
                try:
                    assert device.interface() is not None
                    assert device.state() is not None
                    assert device.active_connection_path() is not None
                    assert device.connections_path() is not None
                except errors.NMPropertiesNotFoundError:
                    continue

                for connection_path in device.connections_path():
                    settings_con = nmd_bus['settings'].connection(
                        connection_path
                    )
                    assert settings_con.connection.uuid is not None

                device_count += 1

        assert device_count > 0

    def test_device_with_single_connection(self, nic0, nmd_bus):
        self._test_device_with_n_connections(1, nic0, nmd_bus)

    def test_device_with_multiple_connections(self, nic0, nmd_bus):
        self._test_device_with_n_connections(2, nic0, nmd_bus)

    def _test_device_with_n_connections(self, con_count, nic, nmd_bus):
        configured_connections = set()
        active_connections = set()
        with nm_connections(
            IFACE, IPV4ADDR, slaves=(nic,), con_count=con_count
        ):
            device = nmd_bus['device'].device(IFACE)
            for connection_path in device.connections_path():
                settings_con = nmd_bus['settings'].connection(connection_path)
                configured_connections.add(settings_con.connection.id)

            active_con = nmd_bus['active_cons'].connection(
                device.active_connection_path()
            )
            active_connections.add(active_con.id())

        assert len(configured_connections) == con_count
        assert active_connections == {IFACE + '0'}


class TestNMConnectionCreation(object):
    def test_nm_connection_lifetime(self, nmd_bus):
        with dummy_device() as nic:
            with nm_connections(IFACE, IPV4ADDR, slaves=(nic,)):
                device = nmd_bus['device'].device(IFACE)
                device.syncoper.waitfor_activated_state()
                active_con_path = device.active_connection_path()
                active_con = nmd_bus['active_cons'].connection(active_con_path)

                assert str(active_con.type()) == TEST_LINK_TYPE
                active_state = nmtypes.NMActiveConnectionState.ACTIVATED
                assert active_con.state() == active_state

        self._assert_no_device(IFACE, nmd_bus['device'])

    def _assert_no_device(self, iface, nm_device):
        with pytest.raises(errors.NMDeviceNotFoundError):
            nm_device.device(iface)
