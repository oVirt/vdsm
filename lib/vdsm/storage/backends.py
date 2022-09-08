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
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA
#
# Refer to the README and COPYING files for full details of the license
#

from __future__ import absolute_import
from __future__ import division

import sanlock
import six

from . import blockSD
from . import constants as sc
from . import glusterSD
from . import localFsSD
from . import nfsSD


_BACKENDS = {
    "FCP": blockSD.BlockStorageDomain,
    "GLUSTERFS": glusterSD.GlusterStorageDomain,
    "ISCSI": blockSD.BlockStorageDomain,
    "LOCALFS": localFsSD.LocalFsStorageDomain,
    "NFS": nfsSD.NfsStorageDomain,
    "POSIXFS": nfsSD.NfsStorageDomain,
}


def supported_block_size():
    # TODO: needed only for 4.3, we require sanlock 3.7.3.
    try:
        have_4k = sc.BLOCK_SIZE_4K in sanlock.SECTOR_SIZE
    except ModuleNotFoundError:
        have_4k = False

    res = {}
    for name, backend in six.iteritems(_BACKENDS):
        if have_4k:
            res[name] = backend.supported_block_size
        else:
            res[name] = (sc.BLOCK_SIZE_512,)

    return res
