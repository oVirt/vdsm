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
    for name, bitmap in _query_bitmaps(src_path, filter=_valid).items():
        _add_bitmap(dst_path, name, bitmap['granularity'])


def merge_bitmaps(base_path, top_path, base_parent_path=None):
    """
    Add and merge the bitmaps from top volume that don't exist
    on the base volume while skipping invalid/disabled bitmaps.

    Should be used only after the block-commit operation ends,
    and all the bitmaps that exist both on the top volume
    and on the base volume were already merged by the block-commit
    operation.

    Arguments:
        base_path (string): Path to the base volume
        top_path (string): Path to the top volume
        base_parent_path (string): Path to the parent of the
            base volume

    Returns:
    """
    valid_top_bitmaps = _query_bitmaps(top_path, filter=_valid)
    base_bitmaps = _query_bitmaps(base_path)
    if base_parent_path:
        parent_bitmaps = _query_bitmaps(base_parent_path)
    else:
        parent_bitmaps = {}

    # Add the missing bitmaps in base volume as disabled bitmaps
    for name, bitmap in valid_top_bitmaps.items():
        # If the bitmap exists on the base volume parent and not on the
        # base volume itself, there is a hole in the bitmaps chain and
        # the bitmap shouldn't be used.
        if name not in base_bitmaps:
            if name in parent_bitmaps:
                log.warning(
                    "Bitmap %s doesn't exist on base volume %r but "
                    "exists on base volume parent %r, bitmaps chain "
                    "isn't valid", name, base_path, base_parent_path)
                continue

            _add_bitmap(base_path, name, bitmap['granularity'])

        # Merge bitmaps content from top_vol to the base_vol. If the
        # bitmap content is already merged by the block-commit or by
        # a previous merge_bitmaps() call,then this will be a no-op
        # for this bitmap.
        _merge_bitmap(top_path, base_path, name)


def _add_bitmap(vol_path, bitmap, granularity, enable=True):
    log.info("Add bitmap %s to %r", bitmap, vol_path)

    try:
        op = qemuimg.bitmap_add(
            vol_path,
            bitmap,
            enable=enable,
            granularity=granularity
        )
        op.run()
    except cmdutils.Error as e:
        raise exception.AddBitmapError(
            reason="Failed to add bitmap: {}".format(e),
            bitmap=bitmap,
            dst_vol_path=vol_path)


def _merge_bitmap(src_path, dst_path, bitmap):
    log.info(
        "Merge bitmap %s from %r to %r",
        bitmap, src_path, dst_path)

    try:
        op = qemuimg.bitmap_merge(
            src_image=src_path,
            src_bitmap=bitmap,
            src_fmt=qemuimg.FORMAT.QCOW2,
            dst_image=dst_path,
            dst_bitmap=bitmap
        )
        op.run()
    except cmdutils.Error as e:
        raise exception.MergeBitmapError(
            reason="Failed to merge bitmap: {}".format(e),
            bitmap=bitmap,
            src_vol_path=src_path,
            dst_vol_path=dst_path)


def _query_bitmaps(vol_path, filter=None):
    vol_info = qemuimg.info(vol_path)

    # For raw format there is no format specific data.
    if "format-specific" not in vol_info:
        return {}

    # Bitmaps are reported only if qemu-img support bitmaps, and the image has
    # bitmaps.
    bitmaps = vol_info["format-specific"]["data"].get("bitmaps", [])

    return {b["name"]: b for b in bitmaps if filter is None or filter(b)}


def _valid(bitmap):
    # A bitmap is not valid when it doesn't contain the
    # 'auto' flag, which means that the bitmaps is deactivated or,
    # contain the in-use which means that the bitmap was not
    # properly saved when the qemu process was shut down last time
    # thus didn't consistently record all the changed sector.
    return (AUTO in bitmap['flags'] and
            IN_USE not in bitmap['flags'])
