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

import glob

import unittest

from dbus.exceptions import DBusException

from network.nettestlib import dummy_devices
from network.nettestlib import requires_systemctl
from network.nmnettestlib import iface_name, NMService, nm_connections

from vdsm.network.nm import networkmanager


IPV4ADDR = '10.1.1.1/29'

_nm_service = None


@requires_systemctl
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


class TestNMService(unittest.TestCase):
    def test_network_manager_service_is_running(self):
        self.assertTrue(networkmanager.is_running())


class TestNMConnectionCleanup(unittest.TestCase):
    def test_remove_all_non_active_connection_from_a_device(self):
        iface = iface_name()
        with dummy_devices(1) as nics:
            with nm_connections(iface, IPV4ADDR, slaves=nics, con_count=3):

                device = networkmanager.Device(iface)
                device.cleanup_inactive_connections()

                self.assertEqual(1, sum(1 for _ in device.connections()))


class TestNMIfcfg2Connection(unittest.TestCase):

    NET_CONF_DIR = '/etc/sysconfig/network-scripts/'
    NET_CONF_PREF = NET_CONF_DIR + 'ifcfg-'

    def test_detect_connection_based_on_ifcfg_file(self):
        """
        NM may use ifcfg files as its storage format for connections via the
        ifcfg-rh settings plugin.
        This is the default option under RHEL/Centos/Fedora.
        When a connection is defined, it is saved in an ifcfg file, however,
        the filename is not recorded in NM records.
        In some scenarios, it is useful look for the ifcfg filename based on
        a given connection or the other way around, looking for the connection
        given the filename.
        """
        iface = iface_name()
        with dummy_devices(1) as nics:
            with nm_connections(
                iface, IPV4ADDR, slaves=nics, con_count=3, save=True
            ):
                device = networkmanager.Device(iface)
                expected_uuids = {
                    con.connection.uuid for con in device.connections()
                }

                actual_uuids = {
                    networkmanager.ifcfg2connection(file)[0]
                    for file in self._ifcfg_files()
                }

                self.assertLessEqual(expected_uuids, actual_uuids)

    @staticmethod
    def _ifcfg_files():
        paths = glob.iglob(TestNMIfcfg2Connection.NET_CONF_PREF + '*')
        for ifcfg_file_name in paths:
            yield ifcfg_file_name
