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
from __future__ import division

import errno
import io

from contextlib import contextmanager
import os
import shutil
import stat
import tempfile


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
        if e.errno != errno.ENOENT:
            raise


@contextmanager
def atomic_file_write(filename, flag):
    """
    Atomically write into a file.

    Usage:

        with atomic_write('foo.txt', 'w') as f:
            f.write('shrubbery')
            # there are no changes on foo.txt yet
        # now it is changed
    """
    fd, tmp_filename = tempfile.mkstemp(
        dir=os.path.dirname(os.path.abspath(filename)),
        prefix=os.path.basename(filename) + '.',
        suffix='.tmp')
    os.close(fd)
    try:
        if os.path.exists(filename):
            shutil.copyfile(filename, tmp_filename)
        with open(tmp_filename, flag) as f:
            yield f
    except:
        rm_file(tmp_filename)
        raise
    else:
        os.rename(tmp_filename, filename)


def rm_tree(dir_to_remove):
    """
    Try to remove a directory and all it's contents.

    If the directory doesn't exist it's assumed that it was already removed.
    """
    try:
        shutil.rmtree(dir_to_remove)
    except OSError as e:
        if e.errno != errno.ENOENT:
            raise


def parse_key_val_file(file_name, delim='='):
    d = {}
    with io.open(file_name) as f:
        for line in f:
            if line.startswith("#"):
                continue
            kv = line.split(delim, 1)
            if len(kv) != 2:
                continue
            k, v = map(lambda x: x.strip(), kv)
            d[k] = v
    return d


def is_block_device(path):
    return stat.S_ISBLK(os.stat(path).st_mode)
