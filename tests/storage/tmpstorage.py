# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import
from __future__ import division

import itertools
import logging
import os
import re

import six

from vdsm.common import commands
from vdsm.common import cmdutils

from . cleanup import CleanupError
from . import loopback

log = logging.getLogger("test")


class TemporaryStorage(object):

    CONF = """
        global {
         use_lvmetad=0
        }
        devices {
         write_cache_state=0
         filter=%s
        }
    """
    CONF = re.sub(r"\s+", " ", CONF).strip()

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

    def lvm_config(self):
        if self._devices:
            pattern = "|".join("^{}$".format(path) for path in self._devices)
            accept = '"a|{}|", '.format(pattern)
        else:
            accept = ""
        filt = '[%s"r|.*|"]' % accept
        return self.CONF % filt

    def close(self):
        errors = []

        for device in six.itervalues(self._devices):
            try:
                self._remove_device_vg(device)
            except CleanupError as e:
                errors.append(e)

        for device in six.itervalues(self._devices):
            try:
                device.detach()
            except Exception as e:
                errors.append("Error deatching device: %s: %s" % (device, e))

        if errors:
            raise CleanupError("Errors during close", errors)

    def _remove_device_vg(self, device):
        conf = self.lvm_config()
        cmd = [
            "vgs",
            "-o", "name",
            "--noheadings",
            "--config", conf,
            "--select", "pv_name = %s" % device.path
        ]
        vg_name = commands.run(cmd).strip().decode()

        if vg_name:

            errors = []
            cmds = [
                ["vgchange", "-an", "--config", conf, vg_name],
                ["lvremove", "-ff", "--config", conf, vg_name],
                ["vgremove", "-ff", "--config", conf, vg_name],
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
