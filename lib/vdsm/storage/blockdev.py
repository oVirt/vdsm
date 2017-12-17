#
# Copyright 2017 Red Hat, Inc.
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
blockdev - data operations on block devices.
"""

from __future__ import absolute_import

import logging

from contextlib import contextmanager

from vdsm import constants
from vdsm import utils
from vdsm.common import cmdutils
from vdsm.common import exception
from vdsm.config import config

from vdsm.storage import blkdiscard
from vdsm.storage import fsutils
from vdsm.storage import operation
from vdsm.storage import constants as sc
from vdsm.storage import exception as se

log = logging.getLogger("storage.blockdev")

MIN_BLOCK_SIZE_MB = 1
MAX_BLOCK_SIZE_MB = 64


class _NullTask(object):
    """
    A task that doesn't call to its given callback when it is aborted.
    Useful as a default non op task like object.
    """

    @contextmanager
    def abort_callback(self, callback):
        yield


def zero(device_path, size=None, task=_NullTask()):
    """
    Zero a block device.

    Arguments:
        device_path (str): Path to block device to wipe
        size (int): Number of bytes to write. If not specified, use the device
            size. Size must be aligned to `vdsm.storage.constants.BLOCK_SIZE`.
        task (`storage.task.Task`): Task running this operation. If specified,
            the zero operation will be aborted if the task is aborted.

    Raises:
        `vdsm.common.exception.ActionStopped` if the wipe was aborted
        `vdsm.storage.exception.VolumesZeroingError` if writing to storage
            failed.
        `vdsm.storage.exception.InvalidParameterException` if size is not
            aligned to `vdsm.storage.constants.BLOCK_SIZE`.
    """
    if size is None:
        # Always aligned to LVM extent size (128MiB).
        size = fsutils.size(device_path)
    elif size % sc.BLOCK_SIZE:
        raise se.InvalidParameterException("size", size)

    log.info("Zeroing device %s (size=%d)", device_path, size)
    with utils.stopwatch("Zero device %s" % device_path,
                         level=logging.INFO, log=log):
        zero_method = config.get('irs', 'zero_method')
        try:
            if zero_method == "blkdiscard":
                _zero_blkdiscard(device_path, size, task)
            elif zero_method == "dd":
                _zero_dd(device_path, size, task)
            else:
                raise exception.InvalidConfiguration(
                    reason="Unsupported value for irs:zero_method",
                    zero_method=zero_method)
        except se.StorageException as e:
            raise se.VolumesZeroingError("Zeroing device %s failed: %s"
                                         % (device_path, e))


def _zero_blkdiscard(device_path, size, task):
    op = blkdiscard.zeroout_operation(device_path, size)
    with task.abort_callback(op.abort):
        op.run()


def _zero_dd(device_path, size, task):
    # Write optimal size blocks. Images are always aligned to
    # optimal size blocks, so we typically have only one call.
    block_size = zero_block_size()
    blocks = size // block_size
    if blocks > 0:
        _run_dd(device_path, 0, block_size, blocks, task)

    # When zeroing special volumes size may not be aligned to
    # optimal block size, so we need to write the last block.
    rest = size % block_size
    if rest > 0:
        offset = blocks * block_size
        _run_dd(device_path, offset, rest, 1, task)


def _run_dd(path, offset, block_size, count, task):
    op = operation.Command([
        constants.EXT_DD,
        "if=/dev/zero",
        "of=%s" % path,
        "bs=%d" % block_size,
        "count=%d" % count,
        "seek=%d" % offset,
        "oflag=direct,seek_bytes",
        "conv=notrunc,fsync",
    ])
    with task.abort_callback(op.abort):
        op.run()


def zero_block_size():
    value = config.get('irs', 'zero_block_size_mb')
    try:
        value = int(value)
    except ValueError:
        raise exception.InvalidConfiguration(
            reason="Unsupported value for irs:zero_block_size_mb",
            zero_block_size_mb=value)
    if value < MIN_BLOCK_SIZE_MB or value > MAX_BLOCK_SIZE_MB:
        raise exception.InvalidConfiguration(
            reason="Out of range value for irs:zero_block_size_mb",
            zero_block_size_mb=value,
            min=MIN_BLOCK_SIZE_MB,
            max=MAX_BLOCK_SIZE_MB)

    return value * constants.MEGAB


def discard(device_path):
    """
    Discard a block device.

    Discard is best effort; if the operation fails we don't fail the flow
    calling it.

    Arguments:
        device_path (str): Path to block device to discard
    """
    log.info("Discarding device %s", device_path)
    try:
        with utils.stopwatch("Discarded device %s" % device_path,
                             level=logging.INFO, log=log):
            blkdiscard.discard(device_path)
    except cmdutils.Error as e:
        log.warning("Discarding device %s failed: %s", device_path, e)
