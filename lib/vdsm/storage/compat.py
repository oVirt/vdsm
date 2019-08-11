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


def _wait_to_async(func):
    """
    Convert the wait=False argument to async=True.

    Sanlock bindings for python 2 uses async=False, while python 3 version uses
    wait=True. This decorator converts python 2 function to behave like python
    3 version, so we can convert all code to use wait=True.

    TODO: Remove when python 2 support is drooped.
    """
    @functools.wraps(func)
    def decorator(*args, **kw):
        wait = kw.pop("wait", None)
        if wait is not None:
            kw["async"] = not wait
        return func(*args, **kw)
    return decorator


try:
    import sanlock

    if six.PY2:
        sanlock.add_lockspace = _wait_to_async(sanlock.add_lockspace)
        sanlock.rem_lockspace = _wait_to_async(sanlock.rem_lockspace)
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
