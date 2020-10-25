# Copyright 2020 Red Hat, Inc.
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

import os

import pytest

from collections import namedtuple

from vdsm.common import cmdutils
from vdsm.common import exception
from vdsm.common.units import MiB

from vdsm.storage import bitmaps
from vdsm.storage import qemuimg

from . marks import (
    requires_bitmaps_merge_support,
    requires_bitmaps_support,
)


def qemuimg_failure(*args, **kwargs):
    raise cmdutils.Error("code", "out", "err", "qemuimg failure")


Volumes = namedtuple("Volumes", "base_vol, top_vol")


@pytest.fixture
def vol_chain(tmp_mount):
    virtual_size = MiB

    # Create base volume
    base_vol = os.path.join(tmp_mount.path, 'base.img')
    op = qemuimg.create(
        base_vol,
        size=virtual_size,
        format=qemuimg.FORMAT.QCOW2,
        qcow2Compat='1.1'
    )
    op.run()

    # Create top volume
    top_vol = os.path.join(tmp_mount.path, 'top.img')
    op = qemuimg.create(
        top_vol,
        size=virtual_size,
        format=qemuimg.FORMAT.QCOW2,
        qcow2Compat='1.1',
        backing=base_vol,
        backingFormat='qcow2'
    )
    op.run()

    return Volumes(base_vol, top_vol)


# add_bitmaps tests

@requires_bitmaps_support
def test_add_only_valid_bitmaps(vol_chain):
    bitmap = 'bitmap'

    # Add new bitmap to base volume
    op = qemuimg.bitmap_add(vol_chain.base_vol, bitmap)
    op.run()

    # Add invalid bitmap to base volume
    op = qemuimg.bitmap_add(
        vol_chain.base_vol,
        'disabled',
        enable=False
    )
    op.run()

    # Add bitmaps from base volume to top volume
    bitmaps.add_bitmaps(vol_chain.base_vol, vol_chain.top_vol)

    info = qemuimg.info(vol_chain.top_vol)
    assert info['bitmaps'] == [
        {
            "flags": ["auto"],
            "name": bitmap,
            "granularity": 65536
        },
    ]


@requires_bitmaps_support
def test_no_bitmaps_to_add(vol_chain):
    # Add bitmaps from base volume to top volume
    bitmaps.add_bitmaps(vol_chain.base_vol, vol_chain.top_vol)

    info = qemuimg.info(vol_chain.top_vol)
    assert 'bitmaps' not in info


@requires_bitmaps_support
def test_add_bitmap_failed(monkeypatch, vol_chain):
    # Add new bitmap to base volume
    op = qemuimg.bitmap_add(vol_chain.base_vol, 'bitmap')
    op.run()

    monkeypatch.setattr(qemuimg, "bitmap_add", qemuimg_failure)
    with pytest.raises(exception.AddBitmapError):
        bitmaps.add_bitmaps(vol_chain.base_vol, vol_chain.top_vol)


# merge_bitmaps tests

@requires_bitmaps_merge_support
def test_merge_only_valid_bitmaps(vol_chain):
    bitmap = 'bitmap'

    # Add new bitmap to base volume
    op = qemuimg.bitmap_add(vol_chain.base_vol, bitmap)
    op.run()

    # Add invalid bitmap to top volume
    op = qemuimg.bitmap_add(
        vol_chain.top_vol,
        'disabled',
        enable=False
    )
    op.run()

    # Add new bitmap to top volume
    op = qemuimg.bitmap_add(vol_chain.top_vol, bitmap)
    op.run()

    # merge bitmaps from top volume to base volume
    bitmaps.merge_bitmaps(vol_chain.base_vol, vol_chain.top_vol)

    info = qemuimg.info(vol_chain.base_vol)
    assert info['bitmaps'] == [
        {
            "flags": ["auto"],
            "name": bitmap,
            "granularity": 65536
        },
    ]


@requires_bitmaps_support
def test_no_bitmaps_to_merge(vol_chain):
    # Merge bitmaps from base volume to top volume
    bitmaps.merge_bitmaps(vol_chain.base_vol, vol_chain.top_vol)

    info = qemuimg.info(vol_chain.base_vol)
    assert 'bitmaps' not in info


@requires_bitmaps_support
def test_merge_bitmaps_failed(monkeypatch, vol_chain):
    bitmap = 'bitmap'

    # Add new bitmap to top volume
    op = qemuimg.bitmap_add(vol_chain.top_vol, bitmap)
    op.run()

    monkeypatch.setattr(qemuimg, "bitmap_merge", qemuimg_failure)
    with pytest.raises(exception.MergeBitmapError):
        bitmaps.merge_bitmaps(vol_chain.base_vol, vol_chain.top_vol)

    info = qemuimg.info(vol_chain.base_vol)
    # TODO: test that this bitmap is empty
    assert info['bitmaps'] == [
        {
            "flags": ['auto'],
            "name": bitmap,
            "granularity": 65536
        },
    ]


@requires_bitmaps_support
def test_merge_bitmaps_failed_to_add_bitmap(
        monkeypatch, vol_chain):
    bitmap = 'bitmap'

    # Add new bitmap to top volume
    op = qemuimg.bitmap_add(vol_chain.top_vol, bitmap)
    op.run()

    monkeypatch.setattr(qemuimg, "bitmap_add", qemuimg_failure)
    with pytest.raises(exception.AddBitmapError):
        bitmaps.merge_bitmaps(vol_chain.base_vol, vol_chain.top_vol)

    info = qemuimg.info(vol_chain.base_vol)
    assert 'bitmaps' not in info


@requires_bitmaps_support
def test_skip_holes_during_merge_bitmaps(tmp_mount, vol_chain):
    virtual_size = MiB
    bitmap = 'bitmap'

    # Create base parent volume
    base_parent_vol = os.path.join(tmp_mount.path, 'base_parent.img')
    op = qemuimg.create(
        base_parent_vol,
        size=virtual_size,
        format=qemuimg.FORMAT.QCOW2,
        qcow2Compat='1.1'
    )
    op.run()

    # Rebase the volume chain on top of base parent volume
    op = qemuimg.rebase(
        vol_chain.base_vol, base_parent_vol, unsafe=True)
    op.run()

    # Add new bitmap to base parent volume
    op = qemuimg.bitmap_add(base_parent_vol, bitmap)
    op.run()
    # Add new bitmap to top volume, base volume is missing that
    # bitmap so there is a hole
    op = qemuimg.bitmap_add(vol_chain.top_vol, bitmap)
    op.run()

    bitmaps.merge_bitmaps(
        vol_chain.base_vol, vol_chain.top_vol,
        base_parent_path=base_parent_vol)

    info = qemuimg.info(vol_chain.base_vol)
    assert 'bitmaps' not in info
