# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from .schema import Ethernet
from .schema import Interface
from .schema import InterfaceState


def create_sriov_state(device, numvfs):
    state = {
        Interface.KEY: [
            {
                Interface.NAME: device,
                Interface.STATE: InterfaceState.UP,
                Ethernet.CONFIG_SUBTREE: {
                    Ethernet.SRIOV_SUBTREE: {
                        Ethernet.SRIOV.TOTAL_VFS: numvfs,
                        Ethernet.SRIOV.VFS_SUBTREE: _create_vfs_ids_subtree(
                            numvfs
                        ),
                    }
                },
            }
        ]
    }

    return state


def _create_vfs_ids_subtree(num):
    return [{Ethernet.SRIOV.VFS.ID: id} for id in range(num)] if num else []
