#
# Copyright 2016-2017 Red Hat, Inc.
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
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA
# 02110-1301  USA
#
# Refer to the README and COPYING files for full details of the license
#

"""
loop - Create temporary loop devices
"""

import logging
import os

from vdsm import commands
from vdsm import udevadm
from vdsm.common import cmdutils

log = logging.getLogger("loopback")


class Device(object):

    def __init__(self, backing_file):
        self._backing_file = backing_file
        self._path = None

    @property
    def path(self):
        return self._path

    @property
    def backing_file(self):
        return self._backing_file

    def attach(self):
        if self._path is not None:
            raise AssertionError("Device is attached: %s" % self)
        cmd = ["losetup", "--find", "--show", self._backing_file]
        rc, out, err = commands.execCmd(cmd, raw=True)
        if rc != 0:
            raise cmdutils.Error(cmd, rc, out, err)
        self._path = out.strip().decode("ascii")

    def detach(self):
        if self._path is None:
            raise AssertionError("Device is detached: %s" % self)
        cmd = ["losetup", "--detach", self._path]
        rc, out, err = commands.execCmd(cmd, raw=True)
        if rc != 0:
            raise cmdutils.Error(cmd, rc, out, err)
        self._path = None
        # After deactivating lvs backed by loop device, we get tons of udev
        # events. We must wait for the events or we may get stale lvs that
        # would fail the next tests.
        #
        # $ udevadm monitor -u
        # ...
        # UDEV  [314195.642497] remove   /devices/virtual/block/dm-4 (block)
        # UDEV  [314195.643032] remove   /devices/virtual/block/dm-4 (block)
        # UDEV  [314195.653214] remove   /devices/virtual/bdi/253:4 (bdi)
        # UDEV  [314195.664478] remove   /devices/virtual/block/dm-5 (block)
        # UDEV  [314195.664863] remove   /devices/virtual/block/dm-5 (block)
        # UDEV  [314195.674426] remove   /devices/virtual/bdi/253:5 (bdi)
        # UDEV  [314195.807277] change   /devices/virtual/block/loop0 (block)
        udevadm.settle(5)

    def is_attached(self):
        if self._path is None:
            return False
        return os.path.isdir(self._sysfs_dir())

    def _sysfs_dir(self):
        dev = os.path.basename(self._path)
        return "/sys/devices/virtual/block/%s/loop" % dev

    def __enter__(self):
        self.attach()
        return self

    def __exit__(self, t, v, tb):
        try:
            self.detach()
        except cmdutils.Error:
            if t is None:
                raise
            log.exception("Cannot detach loop device: %s" % self)

    def __repr__(self):
        return "<%s backing_file=%s, path=%s at 0x%x>" % (
            self.__class__.__name__, self._backing_file, self._path, id(self))
