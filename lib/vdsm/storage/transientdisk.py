# Copyright 2019 Red Hat, Inc.
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
import logging
import os

from vdsm.common.constants import P_TRANSIENT_DISKS

from vdsm.storage import constants as sc
from vdsm.storage import exception as se
from vdsm.storage import qemuimg

log = logging.getLogger("storage.transientdisk")

OWNER_DIR_PERMISSIONS = 0o750


def create_disk(
        owner_name, disk_name, size=None, backing=None, backing_format=None):
    """
    Create a transient disk, optionally based on another disk.

    Arguments:
        owner_name (str): Owner name of this disk. When creating multiple disks
            this name can be used to locate related disks.
        disk_name (str): File name of the new disk.
        size (int): Size of the new disk. If disk has a backing file and the
            size is not specified, the disk size is taken from the backing
            file.
        backing (str): Path of the backing file, if this disk should be based
            on another disk.
        backing_format (str): If backing is specified, you must specify the
            backing file format.

    Returns:
        dict with "path" to the new disk.
    """
    dir_path = _owner_dir(owner_name)
    _create_dir(dir_path)
    disk_path = _disk_dir(owner_name, disk_name)
    log.info("Creating transient disk %s", disk_path)

    _create_placeholder(disk_path)
    try:
        operation = qemuimg.create(
            disk_path,
            size=size,
            format=qemuimg.FORMAT.QCOW2,
            qcow2Compat='1.1',
            backing=backing,
            backingFormat=backing_format)
        operation.run()
        os.chmod(disk_path, sc.FILE_VOLUME_PERMISSIONS)
    except:
        remove_disk(owner_name, disk_name)
        raise

    return dict(path=disk_path)


def remove_disk(owner_name, disk_name):
    disk_path = _disk_dir(owner_name, disk_name)
    log.info("Removing transient disk %s", disk_path)
    try:
        os.unlink(disk_path)
    except OSError as e:
        if e.errno != errno.ENOENT:
            raise

    # Remove the transient disks directory if empty.
    dir_path = _owner_dir(owner_name)
    try:
        _remove_dir(dir_path)
    except OSError as e:
        if e.errno != errno.ENOTEMPTY:
            raise


def list_disks(owner_name):
    dir_path = _owner_dir(owner_name)
    try:
        return os.listdir(dir_path)
    except OSError as e:
        if e.errno != errno.ENOENT:
            raise
        return []


def _create_dir(path):
    try:
        os.makedirs(path)
    except OSError as e:
        if e.errno != errno.EEXIST or not os.path.isdir(path):
            raise

    os.chmod(path, OWNER_DIR_PERMISSIONS)
    log.info("Created directory: %s, %04o", path, OWNER_DIR_PERMISSIONS)


def _remove_dir(path):
    try:
        os.rmdir(path)
    except OSError as e:
        if e.errno != errno.ENOENT:
            raise
    log.info("Directory %s removed", path)


def _owner_dir(owner_name):
    return os.path.join(P_TRANSIENT_DISKS, owner_name)


def _disk_dir(owner_name, disk_name):
    return os.path.join(P_TRANSIENT_DISKS, owner_name, disk_name)


def _create_placeholder(disk_path):
    try:
        # Ensures that there is no such file or symlink
        # and a new file is created. This is basically
        # a lock file preventing other calls from creating the same
        # file, and preventing deletion of the directory by another
        # call while we try to create a disk.
        fd = os.open(disk_path, os.O_RDONLY | os.O_CREAT | os.O_EXCL)
        os.close(fd)
    except OSError as e:
        if e.errno == errno.EEXIST:
            raise se.TransientDiskAlreadyExists(disk_path)
        raise
