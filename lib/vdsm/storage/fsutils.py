# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import
import io
import os


def size(filename):
    """
    Return actual file size, should work with both file and block device.
    """
    with io.open(filename, "rb") as f:
        f.seek(0, os.SEEK_END)
        return f.tell()
