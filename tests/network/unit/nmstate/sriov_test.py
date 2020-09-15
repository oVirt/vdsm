#
# Copyright 2020 Red Hat, Inc.
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

from vdsm.network import nmstate
from vdsm.network.nmstate import sriov

from .testlib import IFACE0


class TestSriov(object):
    def test_create_sriov_state_with_2_vfs(self):
        expected_state = {
            nmstate.Interface.KEY: [
                {
                    nmstate.Interface.NAME: IFACE0,
                    nmstate.Interface.STATE: nmstate.InterfaceState.UP,
                    nmstate.Ethernet.CONFIG_SUBTREE: {
                        nmstate.Ethernet.SRIOV_SUBTREE: {
                            nmstate.Ethernet.SRIOV.TOTAL_VFS: 2,
                            nmstate.Ethernet.SRIOV.VFS_SUBTREE: [
                                {nmstate.Ethernet.SRIOV.VFS.ID: 0},
                                {nmstate.Ethernet.SRIOV.VFS.ID: 1},
                            ],
                        }
                    },
                }
            ]
        }
        desired_sriov_state = sriov.create_sriov_state(IFACE0, 2)

        assert expected_state == desired_sriov_state

    def test_create_sriov_state_with_no_vfs(self):
        expected_state = {
            nmstate.Interface.KEY: [
                {
                    nmstate.Interface.NAME: IFACE0,
                    nmstate.Interface.STATE: nmstate.InterfaceState.UP,
                    nmstate.Ethernet.CONFIG_SUBTREE: {
                        nmstate.Ethernet.SRIOV_SUBTREE: {
                            nmstate.Ethernet.SRIOV.TOTAL_VFS: 0,
                            nmstate.Ethernet.SRIOV.VFS_SUBTREE: [],
                        }
                    },
                }
            ]
        }
        desired_sriov_state = sriov.create_sriov_state(IFACE0, 0)

        assert expected_state == desired_sriov_state
