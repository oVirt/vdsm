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
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA
#
# Refer to the README and COPYING files for full details of the license
#

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
