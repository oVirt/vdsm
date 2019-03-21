#
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
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA  02110-1301 USA
#
# Refer to the README and COPYING files for full details of the license
#

from __future__ import absolute_import
from __future__ import division

import itertools
import logging
import os
import pprint
import re

import six

from vdsm.common import commands
from vdsm.common import cmdutils

from . import loopback

log = logging.getLogger("test")


class CleanupError(Exception):
    def __init__(self, msg, errors):
        self.msg = msg
        self.errors = errors

    def __str__(self):
        return "%s: %s" % (self.msg, pprint.pformat(self.errors))

    def __repr__(self):
        return str(self)


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

    def create_device(self, size):
        """
        Create loop device of size bytes.

        The block device is detached when the instance is closed.
        """
        name = "backing-file-%03d" % next(self._count)
        backing_file = os.path.join(self._tmpdir, name)
        with open(backing_file, "w") as f:
            f.truncate(size)

        device = loopback.Device(backing_file)
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
