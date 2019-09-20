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

from vdsm.network.ovs import switch

from network.compat import mock


@mock.patch('vdsm.network.ovs.switch.ovsdb')
@mock.patch('vdsm.network.ovs.info.OvsInfo')
class ListOVSAcquiredIfacesTests(unittest.TestCase):
    def test_add_network_with_nic(self, mock_ovs_info, mock_ovsdb):
        _init_ovs_info(mock_ovs_info)
        _init_ovsdb_mock(mock_ovsdb)

        self._assert_acquired_ifaces_post_switch_setup(
            mock_ovs_info,
            nets2add={'net': {'nic': 'eth0', 'mtu': 1500}},
            expected_ifaces={'eth0'},
        )

    def test_add_network_with_bond(self, mock_ovs_info, mock_ovsdb):
        _init_ovs_info(mock_ovs_info)
        _init_ovsdb_mock(mock_ovsdb)

        self._assert_acquired_ifaces_post_switch_setup(
            mock_ovs_info,
            nets2add={'net': {'bonding': 'bond1', 'mtu': 1500}},
            expected_ifaces={'bond1'},
        )

    def _assert_acquired_ifaces_post_switch_setup(
        self, _ovs_info, nets2add, expected_ifaces
    ):

        with mock.patch(
            'vdsm.network.ovs.driver.vsctl.Transaction.commit',
            return_value=None,
        ), mock.patch(
            'vdsm.network.ovs.switch.link.get_link',
            return_value={'address': '01:23:45:67:89:ab'},
        ):

            setup = switch.NetsAdditionSetup(_ovs_info)
            setup.prepare_setup(nets2add)
            setup.commit_setup()

            self.assertEqual(setup.acquired_ifaces, expected_ifaces)


def _init_ovs_info(mock_ovs_info):
    mock_ovs_info.bridges = {}
    mock_ovs_info.bridges_by_sb = {}
    mock_ovs_info.northbounds_by_sb = {}


def _init_ovsdb_mock(mock_ovsdb):
    mock_ovsdb.list_interface_info.return_value.execute.return_value = [
        {'mtu': 1500}
    ]
