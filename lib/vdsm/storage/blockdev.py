# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

"""
blockdev - data operations on block devices.
"""

from __future__ import absolute_import

import logging

from contextlib import contextmanager

from vdsm import utils
from vdsm.common import cmdutils

from vdsm.storage import blkdiscard
from vdsm.storage import fsutils
from vdsm.storage import constants as sc
from vdsm.storage import exception as se

log = logging.getLogger("storage.blockdev")


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
            size. Size must be aligned to
            `vdsm.storage.constants.BLOCK_SIZE_4K`.
        task (`storage.task.Task`): Task running this operation. If specified,
            the zero operation will be aborted if the task is aborted.

    Raises:
        `vdsm.common.exception.ActionStopped` if the wipe was aborted
        `vdsm.storage.exception.VolumesZeroingError` if writing to storage
            failed.
        `vdsm.storage.exception.InvalidParameterException` if size is not
            aligned to `vdsm.storage.constants.BLOCK_SIZE_4K`.
    """
    if size is None:
        # Always aligned to LVM extent size (128MiB).
        size = fsutils.size(device_path)
    elif size % sc.BLOCK_SIZE_4K:
        raise se.InvalidParameterException("size", size)

    log.info("Zeroing device %s (size=%d)", device_path, size)
    with utils.stopwatch("Zero device %s" % device_path,
                         level=logging.INFO, log=log):
        try:
            op = blkdiscard.zeroout_operation(device_path, size)
            with task.abort_callback(op.abort):
                op.run()
        except se.StorageException as e:
            raise se.VolumesZeroingError("Zeroing device %s failed: %s"
                                         % (device_path, e))


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
