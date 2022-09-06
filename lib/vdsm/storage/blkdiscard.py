# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import

from vdsm.common import commands
from vdsm.common.cmdutils import CommandPath
from vdsm.common.units import MiB

from vdsm.storage import operation

# From tests that we've run on netapp, 32M is the most optimal discard step
# size in terms of total discard time and minimal starvation of other processes
# that run in parallel.
OPTIMAL_DISCARD_STEP = 32 * MiB

_blkdiscard = CommandPath("blkdiscard", "/sbin/blkdiscard")


def discard(device):
    """
    Discards a block device.

    Arguments:
        device (str): The path to the block device to discard.

    Raises:
        cmdutils.Error if an error has occurred in blkdiscard.
    """
    cmd = [
        _blkdiscard.cmd,
        "--step", "%d" % OPTIMAL_DISCARD_STEP,
    ]
    cmd.append(device)
    commands.run(cmd)


def zeroout_operation(device, size):
    """
    Returns an operation.Command object to zero a block device using
    "blkdiscard --zeroout".

    Arguments:
        device (str): The path to the block device to zero.
        size (int): The number of bytes to zero.
    """
    return operation.Command([
        _blkdiscard.cmd,
        "--zeroout",
        "--step", "%d" % OPTIMAL_DISCARD_STEP,
        "--length", "%d" % size,
        device,
    ])
