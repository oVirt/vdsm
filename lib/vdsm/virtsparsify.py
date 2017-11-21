#
# Copyright 2014-2017 Red Hat, Inc.
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

from vdsm.common import cmdutils
from vdsm.common import commands
from vdsm.common.cmdutils import CommandPath

# Fedora, EL6
_VIRTSPARSIFY = CommandPath("virt-sparsify",
                            "/usr/bin/virt-sparsify",)


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

    rc, out, err = commands.execCmd(cmd)

    if rc != 0:
        raise cmdutils.Error(cmd, rc, out, err)


def sparsify_inplace(vol_path):
    """
    Sparsify the volume in place
    (without copying from an input disk to an output disk)

    :param vol_path: path to the volume
    """
    cmd = [_VIRTSPARSIFY.cmd, '--machine-readable', '--in-place', vol_path]

    rc, out, err = commands.execCmd(cmd)

    if rc != 0:
        raise cmdutils.Error(cmd, rc, out, err)
