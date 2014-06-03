#
# Copyright 2014 Red Hat, Inc.
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

import signal

from . import utils

# Fedora, EL6
_VIRTSPARSIFY = utils.CommandPath("virt-sparsify",
                                  "/usr/bin/virt-sparsify",)


class Error(Exception):
    def __init__(self, ecode, stderr):
        self.ecode = ecode
        self.stderr = stderr

    def __str__(self):
        return "[Error %d] %s" % (self.ecode, self.stderr)


def sparsify(src_vol, tmp_vol, dst_vol, src_format=None, dst_format=None):
    """
    Sparsify the 'src_vol' volume (src_format) to 'dst_vol' volume (dst_format)
    using libguestfs virt-sparsify

    src_vol: path of base volume
    tmp_vol: path of temporary volume created with src_vol as backing volume
    dst_vol: path of destination volume
    src_format: format of base volume ('raw' or `qcow2')
    src_format: format of destination volume ('raw' or `qcow2')
    """
    cmd = [_VIRTSPARSIFY.cmd, '--tmp', 'prebuilt:' + tmp_vol]

    if src_format:
        cmd.extend(("--format", src_format))

    if dst_format:
        cmd.extend(("--convert", dst_format))

    cmd.extend((src_vol, dst_vol))

    rc, _, err = utils.execCmd(cmd, deathSignal=signal.SIGKILL)

    if rc != 0:
        raise Error(rc, err)
