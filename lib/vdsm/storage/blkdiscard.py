#
# Copyright 2016-2017 Red Hat, Inc.
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

from vdsm.common import cmdutils
from vdsm.common import commands
from vdsm.common.cmdutils import CommandPath

from vdsm.storage import operation

# The kernel reports the maximum value of WRITE_SAME in
# /sys/devices/virtual/dm-*/queue/write_same_max_bytes. However testing
# shows that this value is not reliable. For example, with LIO, the
# reported value is 65535 * 512, but the server will fail any request
# bigger than 4096 * 512.
#
# When the request fails, the kernel *silently* falls-back to
# inefficient manually writing zeros, and changes both the LV and the
# underlying multipath device write_same_max_bytes to 0. Future
# BLKZEROOUT requests on any LV on this multipath device will use the
# inefficient manual zero fallback.
#
# qemu-img is using 2 MiB by defualt for BLKZEROOUT during qemu-img
# convert, so we can safely use the same value.
#
# Note: on newer kernels (>4.12), the kernel is not using WRITE_SAME for
# zeroing. We will need to test again this value when we require newer
# kernels.
SAFE_WRITE_SAME_SIZE = 2 * 1024**2

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

    rc, out, err = commands.execCmd(cmd)

    if rc != 0:
        raise cmdutils.Error(cmd, rc, out, err)


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
        "--step", "%d" % SAFE_WRITE_SAME_SIZE,
        "--length", "%d" % size,
        device,
    ])
