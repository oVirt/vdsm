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
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA  02110-1301 USA
#
# Refer to the README and COPYING files for full details of the license
#

from __future__ import absolute_import
import argparse

from vdsm import utils
from vdsm.storage import xlease
from . import expose


@expose('format-xleases')
def format_xleases(*args):
    """
    format-xleases sd_id path

    WARNING:

        This is a destructive operation - you must put the storage
        domain into maintenance before running this tool.

    Format the xleases volume index, dropping all leases from the index.
    This does not delete sanlock resources on the volume. If you want to
    restore existing sanlock resources, use rebuild-index. If you want
    to delete also sanlock resources on this volume you can wipe the
    entire volume using dd before formatting it.

    Notes:

    - With iSCSI based storage you may need to connect to the traget
      using iscsiadm.

    - With file based storage, you may need to mount the storage domain.

    - With block based stoage, you need to activate the xleases logical
      volume before the operation, and deactivate after the operation.

    If formatting fails, the volume will not be usable (it will be
    marked as "updating"), but the operation can be tried again.

    Creating xleases volume on file storage:

        PATH=/rhev/data-center/mnt/server:_path/sd-id/dom_md/xleases
        truncate -s 1G $PATH
        vdsm-tool format-xleases sd-id $PATH

    Creating the xleases volume on block storage:

        lvcreate --name xleases --size 1g sd-id
        vdsm-tool format-xleases sd-id /dev/sd-id/xleases
        lvchange -an sd-id/xleases
    """
    args = parse_args(args)
    backend = xlease.DirectFile(args.path)
    with utils.closing(backend):
        xlease.format_index(args.sd_id, backend)


@expose('rebuild-xleases')
def rebuild_xleases(*args):
    """
    rebuild-xleases sd_id path

    WARNING:

        This is a destructive operation - you must put the storage
        domain into maintenance before running this tool.
        The xleases volume index is the source of truth so rebuilding
        from storage can break it badly.

    Rebuild the xleases volume index, restoring all sanlock resource on
    the xleases volume. If you want to drop all leases in the index, use
    format-xleases.

    Notes:

    - With iSCSI based storage you may need to connect to the traget
      using iscsiadm.

    - With file based storage, you may need to mount the storage domain.

    - With block based stoage, you need to activate the xleases logical
      volume before the operation, and deactivate after the operation.

    If rebuilding fails, the volume will not be usable (it will be
    marked as "updating"), but the operation can be tried again.

    Rebuilding xleases volume on file storage:

        PATH=/rhev/data-center/mnt/server:_path/sd-id/dom_md/xleases
        vdsm-tool rebuild-xleases sd-id $PATH

    Rebuilding the xleases volume on block storage:

        lvchange -ay sd-id/xleases
        vdsm-tool rebuild-xleases sd-id /dev/sd-id/xleases
        lvchange -an sd-id/xleases
    """
    args = parse_args(args)
    backend = xlease.DirectFile(args.path)
    with utils.closing(backend):
        xlease.rebuild_index(args.sd_id, backend)


def parse_args(args):
    parser = argparse.ArgumentParser()
    parser.add_argument('sd_id', help="storage domain UUID")
    parser.add_argument('path', help="path to xleases volume")
    return parser.parse_args(args[1:])
