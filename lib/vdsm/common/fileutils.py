# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

import errno
import io

import os
import shutil
import stat


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
