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
from __future__ import absolute_import

"""
Functions to encode and decode base 10 values to base 26, which are used
by the Linux kernel to construct the storage device node names (e.g. vda,
sdb).

Link to (one of) the Kernel implementation:
http://elixir.free-electrons.com/linux/latest/source/drivers/scsi/sd.c#L3155
"""


def encode(index):
    devindex = ''

    i = int(index)
    while i > 0:
        devindex = chr(ord('a') + (i % 26)) + devindex
        i //= 26

    return devindex
