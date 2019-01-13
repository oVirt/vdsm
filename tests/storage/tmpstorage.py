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
import re

from vdsm.common import commands
from vdsm.common import cmdutils

import loopback

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
        self._devices = []

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
        self._devices.append(device)

        return device.path

    def devices(self):
        return tuple(d.path for d in self._devices)

    def lvm_config(self):
        if self._devices:
            pattern = "|".join("^{}$".format(d.path) for d in self._devices)
            accept = '"a|{}|", '.format(pattern)
        else:
            accept = ""
        filt = '[%s"r|.*|"]' % accept
        return self.CONF % filt

    def close(self):
        self._destroy_vgs()
        self._detach_devices()

    def _destroy_vgs(self):
        conf = self.lvm_config()
        vgs = set()

        for device in self._devices:
            vg_name = self._run_silent([
                "vgs",
                "-o", "name",
                "--noheadings",
                "--config", conf,
                "--select", "pv_name = %s" % device.path
            ])
            if vg_name:
                vgs.add(vg_name)

        for vg_name in vgs:
            self._run_silent(["vgchange", "-an", "--config", conf, vg_name])
            self._run_silent(["lvremove", "-ff", "--config", conf, vg_name])
            self._run_silent(["vgremove", "-ff", "--config", conf, vg_name])

    def _run_silent(self, cmd):
        try:
            return commands.run(cmd).strip().decode()
        except cmdutils.Error as e:
            log.exception("%s", e)
            return None

    def _detach_devices(self):
        for device in self._devices:
            try:
                device.detach()
            except Exception:
                log.exception("Error deatching device: %s", device)
