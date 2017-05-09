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

import os


def monotonic_time():
    """
    Return the amount of time, in secs, elapsed since a fixed
    arbitrary point in time in the past.
    This function is useful if the client just
    needs to use the difference between two given time points.

    With respect to time.time():
    * The resolution of this function is lower. On Linux,
      the resolution is 1/_SC_CLK_TCK, which in turn depends on
      the value of HZ configured in the kernel. A commonly
      found resolution is 10 (ten) ms.
    * This function is resilient with respect to system clock
      adjustments.
    """
    return os.times()[4]
