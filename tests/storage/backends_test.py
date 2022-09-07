# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import
from __future__ import division

import pytest

from vdsm.storage import backends
from vdsm.storage import blockSD
from vdsm.storage import constants as sc
from vdsm.storage import glusterSD
from vdsm.storage import localFsSD
from vdsm.storage import nfsSD

sanlock = pytest.importorskip(
    modname='sanlock',
    reason="sanlock is not available")


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
