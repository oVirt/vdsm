# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

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
