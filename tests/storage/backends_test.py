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

from vdsm.storage import backends
from vdsm.storage import blockSD
from vdsm.storage import constants as sc
from vdsm.storage import glusterSD
from vdsm.storage import localFsSD
from vdsm.storage import nfsSD

from . marks import requires_sanlock


@requires_sanlock
def test_supported_block_size_new_sanlock(monkeypatch):
    monkeypatch.setattr(
        sanlock, "SECTOR_SIZE", (sc.BLOCK_SIZE_512, sc.BLOCK_SIZE_4K))
    assert backends.supported_block_size() == {
        "FCP": blockSD.BlockStorageDomain.supported_block_size,
        "GLUSTERFS": glusterSD.GlusterStorageDomain.supported_block_size,
        "ISCSI": blockSD.BlockStorageDomain.supported_block_size,
        "LOCALFS": localFsSD.LocalFsStorageDomain.supported_block_size,
        "NFS": nfsSD.NfsStorageDomain.supported_block_size,
        "POSIXFS": nfsSD.NfsStorageDomain.supported_block_size,
    }


@requires_sanlock
def test_supported_block_size_old_sanlock(monkeypatch):
    monkeypatch.setattr(sanlock, "SECTOR_SIZE", (sc.BLOCK_SIZE_512,))
    assert backends.supported_block_size() == {
        "FCP": (sc.BLOCK_SIZE_512,),
        "GLUSTERFS": (sc.BLOCK_SIZE_512,),
        "ISCSI": (sc.BLOCK_SIZE_512,),
        "LOCALFS": (sc.BLOCK_SIZE_512,),
        "NFS": (sc.BLOCK_SIZE_512,),
        "POSIXFS": (sc.BLOCK_SIZE_512,),
    }
