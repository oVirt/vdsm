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

import errno
import logging

import os


def touch_file(file_path):
    """
    http://www.unix.com/man-page/POSIX/1posix/touch/
    If a file at filePath already exists, its accessed and modified times are
    updated to the current time. Otherwise, the file is created.
    :param file_path: The file to touch
    """
    with open(file_path, 'a'):
        os.utime(file_path, None)


def rm_file(file_to_remove):
    """
    Try to remove a file.

    If the file doesn't exist it's assumed that it was already removed.
    """
    try:
        os.unlink(file_to_remove)
    except OSError as e:
        if e.errno == errno.ENOENT:
            logging.warning("File: %s already removed", file_to_remove)
        else:
            logging.exception("Removing file: %s failed", file_to_remove)
            raise
