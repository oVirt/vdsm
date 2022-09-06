# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

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
    :param int offset: start allocating from that offset, specified in bytes.
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
