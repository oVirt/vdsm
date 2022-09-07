# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import
from __future__ import division

import logging
from collections import namedtuple

from vdsm.common import cmdutils
from vdsm.common import commands
from vdsm.common.units import GiB
from vdsm.storage import constants as sc

from . cleanup import CleanupError

log = logging.getLogger("test")


class TemporaryFS(object):
    """
    Temporary file system created on provided device. Contains also support for
    mounting newly created FS.
    """
    def __init__(self, tmp_storage):
        self.tmp_storage = tmp_storage
        self._mounts = {}

    def create_filesystem(self, filesystem, remote_path):
        """
        Creates loopback device, build file system on of it and finally mounts
        it to specified directory.
        """
        loopback_path = self.tmp_storage.create_device(
            filesystem.size, sector_size=filesystem.block_size)

        try:
            commands.run(["mkfs", "-t", filesystem.fs_type, loopback_path])
            commands.run(["mount", loopback_path, remote_path])
        except Exception:
            self.tmp_storage.remove_device(loopback_path)
            raise

        self._mounts[remote_path] = (loopback_path, True)

    def remove_filesystem(self, remote_path):
        """
        Unmounts file system mounted at remote_path and removes underlying
        loopback device.
        """
        loopback_path, mounted = self._mounts[remote_path]

        if mounted:
            commands.run(["umount", remote_path])
            self._mounts[remote_path] = (loopback_path, False)

        self.tmp_storage.remove_device(loopback_path)
        del self._mounts[remote_path]

    def close(self):
        errors = []
        for mount in self._mounts.copy():
            try:
                self.remove_filesystem(mount)
            except (cmdutils.Error, CleanupError) as e:
                errors.append("Cannot remove filesystem %s: %s" % (mount, e))

        if errors:
            raise CleanupError("Errors during close", errors)


class FileSystem(namedtuple("FileSystem", ("fs_type", "block_size", "size"))):
    """
    Class for keeping information about created files system.
    """
    __slots__ = ()

    def __new__(cls, fs_type="ext4", block_size=sc.BLOCK_SIZE_512,
                size=10 * GiB):
        return super(FileSystem, cls).__new__(
            cls, fs_type=fs_type, block_size=block_size, size=size)
