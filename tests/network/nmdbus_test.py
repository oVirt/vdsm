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

from testlib import VdsmTestCase
from testValidation import broken_on_ci, ValidateRunningAsRoot

from .nmnettestlib import iface_name, NMService, nm_connection

from vdsm.network.nm.nmdbus import NMDbus
from vdsm.network.nm.nmdbus.active import NMDbusActiveConnections
from vdsm.network.nm.nmdbus.settings import NMDbusSettings


IPV4ADDR = '10.1.1.1/29'

_nm_service = None


@broken_on_ci('NetworkManager should not be started on CI nodes')
@ValidateRunningAsRoot
def setup_module():
    global _nm_service
    _nm_service = NMService()
    _nm_service.setup()
    NMDbus.init()


def teardown_module():
    _nm_service.teardown()


@attr(type='integration')
class TestNMConnectionSettings(VdsmTestCase):

    def test_configured_connections_attributes_existence(self):
        nm_settings = NMDbusSettings()

        iface = iface_name()
        with nm_connection(iface, IPV4ADDR):
            con_count = 0
            for nm_con in nm_settings.connections():
                if nm_con.connection.type in ('802-11-wireless', 'vpn'):
                    continue

                assert nm_con.connection.id is not None
                assert nm_con.connection.uuid is not None
                assert nm_con.connection.type is not None

                con_count += 1

            self.assertGreaterEqual(con_count, 1)


@attr(type='integration')
class TestNMActiveConnections(VdsmTestCase):

    def test_active_connections_properties_existence(self):
        nm_active_cons = NMDbusActiveConnections()

        iface = iface_name()
        with nm_connection(iface, IPV4ADDR):
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
        with nm_connection(iface, IPV4ADDR):
            for active_con in nm_active_cons.connections():
                settings_con = nm_settings.connection(active_con.con_path)

                assert active_con.uuid == settings_con.connection.uuid
                assert active_con.type == settings_con.connection.type
                assert active_con.id == settings_con.connection.id
