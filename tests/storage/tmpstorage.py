# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import
from __future__ import division

import itertools
import logging
import os

from vdsm.common import commands
from vdsm.common import cmdutils

from . cleanup import CleanupError
from . import loopback

log = logging.getLogger("test")


class TemporaryStorage(object):

    def __init__(self, tmpdir):
        self._tmpdir = tmpdir
        self._count = itertools.count()
        self._devices = {}

    def create_device(self, size, sector_size=None):
        """
        Create loop device of size bytes.

        The block device is detached when the instance is closed.
        """
        name = "backing-file-%03d" % next(self._count)
        backing_file = os.path.join(self._tmpdir, name)
        with open(backing_file, "w") as f:
            f.truncate(size)

        device = loopback.Device(backing_file, sector_size=sector_size)
        device.attach()
        self._devices[device.path] = device

        return device.path

    def remove_device(self, device_path):
        device = self._devices[device_path]
        self._remove_device_vg(device)
        device.detach()
        del self._devices[device_path]

    def devices(self):
        return tuple(self._devices)

    def close(self):
        errors = []

        for device in self._devices.values():
            try:
                self._remove_device_vg(device)
            except CleanupError as e:
                errors.append(e)

        for device in self._devices.values():
            try:
                device.detach()
            except Exception as e:
                errors.append("Error deatching device: %s: %s" % (device, e))

        if errors:
            raise CleanupError("Errors during close", errors)

    def _remove_device_vg(self, device):
        tmp_devices = ",".join(self._devices)
        cmd = [
            "vgs",
            "-o", "name",
            "--noheadings",
            "--devices", tmp_devices,
            "--select", "pv_name = %s" % device.path
        ]
        vg_name = commands.run(cmd).strip().decode()

        if vg_name:

            errors = []
            cmds = [
                ["vgchange", "-an", "--devices", tmp_devices, vg_name],
                ["lvremove", "-ff", "--devices", tmp_devices, vg_name],
                ["vgremove", "-ff", "--devices", tmp_devices, vg_name],
            ]

            # run all the commands even if some of them fail
            for cmd in cmds:

                try:
                    commands.run(cmd)
                except cmdutils.Error as e:
                    errors.append(e)

            # but report error if there is any failure
            if errors:
                raise CleanupError("Errors removing vg %s" % vg_name, errors)
