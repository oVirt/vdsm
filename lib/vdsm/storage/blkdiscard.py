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

from vdsm import cmdutils
from vdsm import commands
from vdsm.common.cmdutils import CommandPath

from vdsm.storage import operation

_blkdiscard = CommandPath("blkdiscard", "/sbin/blkdiscard")


def discard(device):
    """
    Discards a block device.

    Arguments:
        device (str): The path to the block device to discard.

    Raises:
        cmdutils.Error if an error has occurred in blkdiscard.
    """
    cmd = [_blkdiscard.cmd]
    cmd.append(device)

    rc, out, err = commands.execCmd(cmd)

    if rc != 0:
        raise cmdutils.Error(cmd, rc, out, err)


def zeroout_operation(device, step, size):
    """
    Returns an operation.Command object to zero a block device using
    "blkdiscard --zeroout".

    Arguments:
        device (str): The path to the block device to zero.
        step (int): The number of bytes to zero within one iteration.
        size (int): The number of bytes to zero.
    """
    return operation.Command([
        _blkdiscard.cmd,
        "--zeroout",
        "--step", "%d" % step,
        "--length", "%d" % size,
        device,
    ])
