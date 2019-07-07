# Copyright (C) 2019 Red Hat, Inc.
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
"""
userstorage.py - configure storage for vdsm storage tests.
"""

from __future__ import absolute_import
from __future__ import division

import argparse
import errno
import logging
import os
import subprocess

# because this is used both as script and as a module, we don't import any vdsm
# module here. Importing vdsm modules requires changing PYTHONPATH to run this
# script.

BASE_DIR = "/var/tmp/vdsm-storage"

log = logging.getLogger()


class Unsupported(Exception):
    """
    Raised if configuration is not supported in the testing environment.
    """


class Path(object):
    """
    Base class for path objects.
    """

    name = None  # subclass should define.

    def setup(self):
        raise NotImplementedError

    def teardown(self):
        raise NotImplementedError

    def exists(self):
        raise NotImplementedError

    def __str__(self):
        return self.name


class LoopDevice(Path):

    def __init__(self, name, size, sector_size=512):
        self.name = name
        self.size = size
        self.sector_size = sector_size
        self.path = os.path.join(BASE_DIR, "loop." + name)
        self._backing = os.path.join(BASE_DIR, "backing." + name)

    def setup(self):
        # Detecting unsupported environment automatically makes it easy to run
        # the tests everywhere without any configuration.
        if self.sector_size == 4096:
            if not HAVE_SECTOR_SIZE or "OVIRT_CI" in os.environ:
                raise Unsupported("Sector size {} not supported"
                                  .format(self.sector_size))

        if self.exists():
            log.debug("Reusing loop device %s", self.path)
            return

        log.info("Creating backing file %s", self._backing)
        with open(self._backing, "w") as f:
            f.truncate(self.size)

        log.info("Creating loop device %s", self.path)
        device = self._create_loop_device()

        # Remove stale symlink.
        if os.path.islink(self.path):
            os.unlink(self.path)

        os.symlink(device, self.path)

        if os.geteuid() != 0:
            _chown(self.path)

    def teardown(self):
        log.info("Removing loop device %s", self.path)
        if self.exists():
            self._remove_loop_device()
        _remove_file(self.path)

        log.info("Removing backing file %s", self._backing)
        _remove_file(self._backing)

    def exists(self):
        return os.path.exists(self.path)

    def _create_loop_device(self):
        cmd = [
            "sudo",
            "losetup",
            "-f", self._backing,
            "--show",
        ]

        if HAVE_SECTOR_SIZE:
            cmd.append("--sector-size")
            cmd.append(str(self.sector_size))

        out = subprocess.check_output(cmd)
        return out.decode("utf-8").strip()

    def _remove_loop_device(self):
        subprocess.check_call(["sudo", "losetup", "-d", self.path])


class Mount(Path):

    def __init__(self, loop):
        self._loop = loop
        self.path = os.path.join(BASE_DIR, "mount." + loop.name)

    @property
    def name(self):
        return self._loop.name

    @property
    def sector_size(self):
        return self._loop.sector_size

    def setup(self):
        if self.exists():
            log.debug("Reusing mount %s", self.path)
            return

        self._loop.setup()

        log.info("Creating filesystem %s", self.path)
        self._create_filesystem()
        _create_dir(self.path)
        self._mount_loop()

        if os.geteuid() != 0:
            _chown(self.path)

    def teardown(self):
        log.info("Unmounting filesystem %s", self.path)

        if self.exists():
            self._unmount_loop()

        _remove_dir(self.path)

        self._loop.teardown()

    def exists(self):
        with open("/proc/self/mounts") as f:
            for line in f:
                if self.path in line:
                    return True
        return False

    def _create_filesystem(self):
        # TODO: Use -t xfs (requires xfsprogs package).
        subprocess.check_call(["sudo", "mkfs", "-q", self._loop.path])

    def _mount_loop(self):
        subprocess.check_call(["sudo", "mount", self._loop.path, self.path])

    def _unmount_loop(self):
        subprocess.check_call(["sudo", "umount", self.path])


class File(Path):

    def __init__(self, mount):
        """
        Create file based storage.
        """
        self._mount = mount
        self.path = os.path.join(mount.path, "file")

    @property
    def name(self):
        return self._mount.name

    @property
    def sector_size(self):
        return self._mount.sector_size

    def setup(self):
        if self.exists():
            log.debug("Reusing file %s", self.path)
            return

        self._mount.setup()

        log.info("Creating file %s", self.path)
        open(self.path, "w").close()

    def teardown(self):
        log.info("Removing file %s", self.path)
        _remove_file(self.path)
        self._mount.teardown()

    def exists(self):
        return os.path.exists(self.path)


def _chown(path):
    user_group = "%(USER)s:%(USER)s" % os.environ
    subprocess.check_call(["sudo", "chown", user_group, path])


def _create_dir(path):
    try:
        os.makedirs(path)
    except EnvironmentError as e:
        if e.errno != errno.EEXIST:
            raise


def _remove_file(path):
    try:
        os.remove(path)
    except EnvironmentError as e:
        if e.errno != errno.ENOENT:
            raise


def _remove_dir(path):
    try:
        os.rmdir(path)
    except EnvironmentError as e:
        if e.errno != errno.ENOENT:
            raise


def _have_sector_size():
    out = subprocess.check_output(["losetup", "-h"])
    return "--sector-size <num>" in out.decode()


HAVE_SECTOR_SIZE = _have_sector_size()

PATHS = {
    "file-512":
        File(Mount(LoopDevice("file-512", size=1024**3, sector_size=512))),
    "file-4k":
        File(Mount(LoopDevice("file-4k", size=1024**3, sector_size=4096))),
}


def main():
    parser = argparse.ArgumentParser(
        description='Configure storage for vdsm storage tests')
    parser.add_argument("command", choices=["setup", "teardown"])
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="[userstorage] %(levelname)-7s %(message)s")

    if args.command == "setup":
        setup(args)
    elif args.command == "teardown":
        teardown(args)


def setup(args):
    _create_dir(BASE_DIR)
    for p in PATHS.values():
        try:
            p.setup()
        except Unsupported as e:
            log.warning("Cannot setup %s storage: %s", p.name, e)


def teardown(args):
    for p in PATHS.values():
        p.teardown()
    _remove_dir(BASE_DIR)


if __name__ == "__main__":
    main()
