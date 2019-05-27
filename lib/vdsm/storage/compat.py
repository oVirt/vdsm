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

from __future__ import absolute_import

import functools

import six

from vdsm.common import compat
from vdsm.storage import constants as sc


def compat_4k(func):
    """
    Decorate sanlock < 3.7.3 functions with align and sector arguments, so new
    code can call sanlock in an uniform way.

    Raises ValueError if calling old sanlock with incompatible align or sector
    values.
    """

    @functools.wraps(func)
    def decorator(*args, **kwargs):
        # Remove and validate sector.
        sector = kwargs.pop("sector", sc.BLOCK_SIZE_512)
        if sector != sc.BLOCK_SIZE_512:
            raise ValueError("Wrong sector size %d" % sector)

        # Remove and validate align.
        align = kwargs.pop("align", sc.ALIGNMENT_1M)
        if align != sc.ALIGNMENT_1M:
            raise ValueError("Wrong alignment %d" % align)

        return func(*args, **kwargs)

    return decorator


try:
    import sanlock

    # TODO: Remove when sanlock 3.7.3 is available.
    if not hasattr(sanlock, "SECTOR_SIZE"):
        sanlock.read_lockspace = compat_4k(sanlock.read_lockspace)
        sanlock.write_lockspace = compat_4k(sanlock.write_lockspace)
        sanlock.read_resource = compat_4k(sanlock.read_resource)
        sanlock.read_resource_owners = compat_4k(sanlock.read_resource_owners)
        sanlock.write_resource = compat_4k(sanlock.write_resource)

        sanlock.SECTOR_SIZE = (sc.BLOCK_SIZE_512,)
        sanlock.ALINGN_SIZE = (sc.ALIGNMENT_1M,)

except ImportError:
    if six.PY2:
        raise

    # sanlock is not avilable yet on python3, but we can still test the modules
    # using it with fakesanlock, avoiding python3 regressions.
    # TODO: remove when sanlock is available on python 3.

    class SanlockModule(compat.MissingModule):

        # Used during import, implement to make import pass.
        HOST_UNKNOWN = 1
        HOST_FREE = 2
        HOST_LIVE = 3
        HOST_FAIL = 4
        HOST_DEAD = 5

    sanlock = SanlockModule("sanlock is not available in python 3")

try:
    import ioprocess
except ImportError:
    if six.PY2:
        raise
    ioprocess = compat.MissingModule("ioprocess is not available in python 3")
