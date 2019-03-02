#
# Copyright 2016-2018 Red Hat, Inc.
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

from __future__ import absolute_import

from vdsm.common import commands
from vdsm.common.cmdutils import CommandPath

from vdsm.storage import operation

# From tests that we've run on netapp, 32M is the most optimal discard step
# size in terms of total discard time and minimal starvation of other processes
# that run in parallel.
OPTIMAL_DISCARD_STEP = 32 * 1024**2

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
