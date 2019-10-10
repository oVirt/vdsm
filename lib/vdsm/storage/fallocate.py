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
fallocate wrapping module
"""

from __future__ import absolute_import

import sys
from vdsm.storage import operation

_FALLOCATE = "/usr/libexec/vdsm/fallocate"


def allocate(image, size, offset=0):
    """
    Creates a new 'allocate' operation object,
    that will create new file and preallocate disk space
    for it when run.
    Operation can be aborted during it's execution.

    :param str image: filename with path
    :param int size: expected file size in bytes
    :param int offset: start allocating from that offset,
           specified in bytes.
    :return operation object, encapsulating fallocate helper call
    """
    # This is the only sane way to run python scripts that work with both
    # python2 and python3 in the tests.
    # TODO: Remove when we drop python 2.
    cmd = [sys.executable, _FALLOCATE]

    if offset > 0:
        cmd.extend(("--offset", str(offset)))

    cmd.append(str(size))
    cmd.append(image)

    return operation.Command(cmd)
