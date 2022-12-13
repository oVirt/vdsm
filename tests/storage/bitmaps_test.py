# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

import os

import pytest

from collections import namedtuple

from . import qemuio

from vdsm.common import cmdutils
from vdsm.common import exception
from vdsm.common.units import MiB

from vdsm.storage import bitmaps
from vdsm.storage import qemuimg


def qemuimg_failure(*args, **kwargs):
    raise cmdutils.Error("code", "out", "err", b"qemuimg failure")


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
    assert info['format-specific']['data']['bitmaps'] == [
        {
            "flags": ["auto"],
            "name": bitmap,
            "granularity": 65536
        },
    ]


def test_no_bitmaps_to_add(vol_chain):
    # Add bitmaps from base volume to top volume
    bitmaps.add_bitmaps(vol_chain.base_vol, vol_chain.top_vol)

    info = qemuimg.info(vol_chain.top_vol)
    assert 'bitmaps' not in info['format-specific']['data']


def test_add_bitmap_failed(monkeypatch, vol_chain):
    # Add new bitmap to base volume
    op = qemuimg.bitmap_add(vol_chain.base_vol, 'bitmap')
    op.run()

    monkeypatch.setattr(qemuimg, "bitmap_add", qemuimg_failure)
    with pytest.raises(exception.AddBitmapError):
        bitmaps.add_bitmaps(vol_chain.base_vol, vol_chain.top_vol)


# merge_bitmaps tests

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
    assert info['format-specific']['data']['bitmaps'] == [
        {
            "flags": ["auto"],
            "name": bitmap,
            "granularity": 65536
        },
    ]


def test_no_bitmaps_to_merge(vol_chain):
    # Merge bitmaps from base volume to top volume
    bitmaps.merge_bitmaps(vol_chain.base_vol, vol_chain.top_vol)

    info = qemuimg.info(vol_chain.base_vol)
    assert 'bitmaps' not in info['format-specific']['data']


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
    assert info['format-specific']['data']['bitmaps'] == [
        {
            "flags": ['auto'],
            "name": bitmap,
            "granularity": 65536
        },
    ]


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
    assert 'bitmaps' not in info['format-specific']['data']


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
        vol_chain.base_vol,
        base_parent_vol,
        format=qemuimg.FORMAT.QCOW2,
        backingFormat=qemuimg.FORMAT.QCOW2,
        unsafe=True)
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
    assert 'bitmaps' not in info['format-specific']['data']


def test_remove_bitmap_failed(monkeypatch, tmp_mount, vol_chain):
    bitmap = 'bitmap'
    # Add new bitmap to base parent volume
    op = qemuimg.bitmap_add(vol_chain.top_vol, bitmap)
    op.run()

    monkeypatch.setattr(qemuimg, "bitmap_remove", qemuimg_failure)
    with pytest.raises(exception.RemoveBitmapError):
        bitmaps.remove_bitmap(vol_chain.top_vol, bitmap)


def test_remove_non_existing_bitmap_succeed(tmp_mount, vol_chain):
    # try to remove a non-existing bitmap from top_vol
    bitmaps.remove_bitmap(vol_chain.top_vol, 'bitmap')


def test_prune_stale_bitmaps(tmp_mount, vol_chain):
    # Add valid bitmap to volumes
    bitmap = 'valid-bitmap'
    qemuimg.bitmap_add(vol_chain.base_vol, bitmap).run()
    qemuimg.bitmap_add(vol_chain.top_vol, bitmap).run()

    # Add stale bitmaps to base volume
    for i in range(5):
        qemuimg.bitmap_add(vol_chain.top_vol, f"stale-bitmap-{i}").run()

    bitmaps.prune_bitmaps(vol_chain.base_vol, vol_chain.top_vol)

    info = qemuimg.info(vol_chain.base_vol)
    assert info['format-specific']['data']['bitmaps'] == [
        {
            "flags": ['auto'],
            "name": bitmap,
            "granularity": 65536
        },
    ]


def test_prune_disabled_bitmaps(tmp_mount, vol_chain):
    # Add valid bitmap to volumes
    bitmap = 'valid-bitmap'
    qemuimg.bitmap_add(vol_chain.base_vol, bitmap).run()
    qemuimg.bitmap_add(vol_chain.top_vol, bitmap).run()

    # Add disabled bitmaps to top volume
    for i in range(5):
        bitmap_disabled = f"disabled-bitmap-{i}"
        qemuimg.bitmap_add(vol_chain.base_vol, bitmap_disabled).run()
        qemuimg.bitmap_add(
            vol_chain.top_vol, bitmap_disabled, enable=False).run()

    bitmaps.prune_bitmaps(vol_chain.base_vol, vol_chain.top_vol)

    info = qemuimg.info(vol_chain.base_vol)
    assert info['format-specific']['data']['bitmaps'] == [
        {
            "flags": ['auto'],
            "name": bitmap,
            "granularity": 65536
        },
    ]


def test_prune_in_use_bitmaps(tmp_mount, vol_chain):
    # Add inconsistent "in-use" bitmaps to volumes
    for i in range(5):
        bitmap_in_use = f"in-use-bitmap-{i}"
        qemuimg.bitmap_add(vol_chain.base_vol, bitmap_in_use).run()
        qemuimg.bitmap_add(vol_chain.top_vol, bitmap_in_use).run()
    qemuio.abort(vol_chain.top_vol)

    # Add valid bitmap to volumes
    bitmap = 'valid-bitmap'
    qemuimg.bitmap_add(vol_chain.base_vol, bitmap).run()
    qemuimg.bitmap_add(vol_chain.top_vol, bitmap).run()

    bitmaps.prune_bitmaps(vol_chain.base_vol, vol_chain.top_vol)

    info = qemuimg.info(vol_chain.base_vol)
    assert info['format-specific']['data']['bitmaps'] == [
        {
            "flags": ['auto'],
            "name": bitmap,
            "granularity": 65536
        },
    ]


def test_clear_bitmaps(tmp_mount, vol_chain):
    bitmap_1 = 'bitmap_1'
    bitmap_2 = 'bitmap_2'

    # Add new bitmaps to top volume
    for bitmap in [bitmap_1, bitmap_2]:
        op = qemuimg.bitmap_add(vol_chain.top_vol, bitmap)
        op.run()

    # Clear top volume bitmaps
    bitmaps.clear_bitmaps(vol_chain.top_vol)

    info = qemuimg.info(vol_chain.top_vol)
    vol_bitmaps = info["format-specific"]["data"].get("bitmaps", [])
    assert not vol_bitmaps


def test_clear_bitmaps_failed(monkeypatch, tmp_mount, vol_chain):
    # Add new bitmap to top volume
    op = qemuimg.bitmap_add(vol_chain.top_vol, 'bitmap')
    op.run()

    monkeypatch.setattr(qemuimg, "bitmap_remove", qemuimg_failure)
    with pytest.raises(exception.RemoveBitmapError):
        bitmaps.clear_bitmaps(vol_chain.top_vol)
