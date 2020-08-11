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
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA
#
# Refer to the README and COPYING files for full details of the license
#

"""
QEMU dirty bitmaps helper module
"""

import logging

from vdsm.common import cmdutils
from vdsm.common import exception

from vdsm.storage import qemuimg


log = logging.getLogger("storage.bitmaps")

# Dirty bitmaps flags:

# The bitmap must reflect all changes of the virtual disk by any
# application that would write to this qcow2 file.
AUTO = 'auto'

# This flag is set by any process actively modifying the qcow2 file,
# and cleared when the updated bitmap is flushed to the qcow2 image.
# The presence of this flag in an offline image means that the bitmap
# was not saved correctly after its last usage, and may contain
# inconsistent data.
IN_USE = 'in-use'


def add_bitmaps(src_path, dst_path):
    """
    Add the bitmaps from source to destination path
    while skipping invalid bitmaps.

    Arguments:
        src_path (string): Path to the source image
        dst_path (string): Path to the destination image
    """
    src_vol_info = qemuimg.info(src_path)
    if 'bitmaps' not in src_vol_info:
        return

    for bitmap in src_vol_info['bitmaps']:
        if not _valid(bitmap):
            continue

        log.info(
            "Add bitmap %s to %r",
            bitmap['name'], dst_path)

        try:
            op = qemuimg.bitmap_add(
                dst_path,
                bitmap['name'],
                granularity=bitmap['granularity']
            )
            op.run()
        except cmdutils.Error as e:
            raise exception.AddBitmapError(
                reason="Failed to add bitmap: {}".format(e),
                bitmap=bitmap,
                dst_vol_path=dst_path)


def _valid(bitmap):
    # A bitmap is not valid when it doesn't contain the
    # 'auto' flag, which means that the bitmaps is deactivated or,
    # contain the in-use which means that the bitmap was not
    # properly saved when the qemu process was shut down last time
    # thus didn't consistently record all the changed sector.
    return (AUTO in bitmap['flags'] and
            IN_USE not in bitmap['flags'])
