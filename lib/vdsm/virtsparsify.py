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

from vdsm.common import commands
from vdsm.common.cmdutils import CommandPath

# Fedora, EL6
_VIRTSPARSIFY = CommandPath("virt-sparsify",
                            "/usr/bin/virt-sparsify",)


def sparsify_inplace(vol_path):
    """
    Sparsify the volume in place
    (without copying from an input disk to an output disk)

    :param vol_path: path to the volume
    """
    cmd = [_VIRTSPARSIFY.cmd, '--machine-readable', '--in-place', vol_path]

    commands.run(cmd)
