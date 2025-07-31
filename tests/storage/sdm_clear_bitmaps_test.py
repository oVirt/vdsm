# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from contextlib import contextmanager

import pytest

from monkeypatch import MonkeyPatchScope

from storage.storagefakelib import (
    FakeResourceManager,
    fake_guarded_context,
)

from storage.storagetestlib import (
    fake_env,
    make_qemu_chain,
)

from testlib import make_uuid

from vdsm import jobs
from vdsm.common import cmdutils
from vdsm.common.units import MiB
from vdsm.storage import blockVolume
from vdsm.storage import constants as sc
from vdsm.storage import exception as se
from vdsm.storage import guarded
from vdsm.storage import qemuimg
from vdsm.storage.sdm import volume_info
from vdsm.storage.sdm.api import clear_bitmaps


def failure(*args, **kwargs):
    raise cmdutils.Error("code", "out", "err", "Fail bitmap operation")


DEFAULT_SIZE = MiB


@contextmanager
def make_env(storage_type, fmt, chain_length=1,
             size=DEFAULT_SIZE, sd_version=5, qcow2_compat='1.1'):
    with fake_env(storage_type, sd_version=sd_version) as env:
        rm = FakeResourceManager()
        with MonkeyPatchScope([
            (guarded, 'context', fake_guarded_context()),
            (volume_info, 'sdCache', env.sdcache),
            (blockVolume, 'rm', rm),
        ]):
            env.chain = make_qemu_chain(env, size, fmt, chain_length,
                                        qcow2_compat=qcow2_compat)
            yield env


@pytest.mark.parametrize("env_type", ["file", "block"])
def test_clear_bitmaps(fake_scheduler, env_type):
    with make_env(env_type, sc.name2type('cow')) as env:
        top_vol = env.chain[0]
        # Add new bitmaps to top volume
        for bitmap in ['bitmap_1', 'bitmap_2']:
            op = qemuimg.bitmap_add(top_vol.getVolumePath(), bitmap)
            op.run()

        # Clear the created bitmap
        generation = top_vol.getMetaParam(sc.GENERATION)
        vol = {
            'endpoint_type': 'div',
            'sd_id': top_vol.sdUUID,
            'img_id': top_vol.imgUUID,
            'vol_id': top_vol.volUUID,
            'generation': generation
        }
        job = clear_bitmaps.Job(make_uuid(), 0, vol)
        job.run()

        assert jobs.STATUS.DONE == job.status
        vol_info = qemuimg.info(top_vol.getVolumePath())
        assert "bitmaps" not in vol_info["format-specific"]["data"]
        assert top_vol.getMetaParam(sc.GENERATION) == generation + 1

        qemuInfo = top_vol.getQemuImageInfo()
        assert 'bitmaps' not in qemuInfo


@pytest.mark.parametrize("env_type", ["file", "block"])
def test_clear_invalid_bitmaps(fake_scheduler, env_type):
    with make_env(env_type, sc.name2type('cow')) as env:
        top_vol = env.chain[0]
        # Add new invalid bitmaps to top volume
        for bitmap in ['bitmap_1', 'bitmap_2']:
            op = qemuimg.bitmap_add(
                top_vol.getVolumePath(),
                bitmap,
                enable=False
            )
            op.run()

        # Clear the created bitmap
        generation = top_vol.getMetaParam(sc.GENERATION)
        vol = {
            'endpoint_type': 'div',
            'sd_id': top_vol.sdUUID,
            'img_id': top_vol.imgUUID,
            'vol_id': top_vol.volUUID,
            'generation': generation
        }
        job = clear_bitmaps.Job(make_uuid(), 0, vol)
        job.run()

        assert jobs.STATUS.DONE == job.status
        vol_info = qemuimg.info(top_vol.getVolumePath())
        assert "bitmaps" not in vol_info["format-specific"]["data"]
        assert top_vol.getMetaParam(sc.GENERATION) == generation + 1

        qemuInfo = top_vol.getQemuImageInfo()
        assert 'bitmaps' not in qemuInfo


@pytest.mark.parametrize("env_type", ["file", "block"])
def test_vol_type_not_qcow(fake_scheduler, env_type):
    with make_env(env_type, sc.name2type('raw')) as env:
        top_vol = env.chain[0]
        generation = top_vol.getMetaParam(sc.GENERATION)
        vol = {
            'endpoint_type': 'div',
            'sd_id': top_vol.sdUUID,
            'img_id': top_vol.imgUUID,
            'vol_id': top_vol.volUUID,
            'generation': generation
        }

        # Clear bitmap failed for RAW volume
        job = clear_bitmaps.Job(make_uuid(), 0, vol)
        job.run()

        assert job.status == jobs.STATUS.FAILED
        assert isinstance(job.error, se.UnsupportedOperation)
        assert top_vol.getLegality() == sc.LEGAL_VOL
        assert top_vol.getMetaParam(sc.GENERATION) == generation


@pytest.mark.parametrize("env_type", ["file", "block"])
def test_shared_vol(fake_scheduler, env_type):
    with make_env(env_type, sc.name2type('raw')) as env:
        top_vol = env.chain[0]
        top_vol.setShared()
        generation = top_vol.getMetaParam(sc.GENERATION)
        vol = {
            'endpoint_type': 'div',
            'sd_id': top_vol.sdUUID,
            'img_id': top_vol.imgUUID,
            'vol_id': top_vol.volUUID,
            'generation': generation
        }

        # Clear bitmap failed for SHARED volume
        job = clear_bitmaps.Job(make_uuid(), 0, vol)
        job.run()

        assert job.status == jobs.STATUS.FAILED
        assert isinstance(job.error, se.UnsupportedOperation)
        assert top_vol.getLegality() == sc.LEGAL_VOL
        assert top_vol.getMetaParam(sc.GENERATION) == generation


@pytest.mark.parametrize("env_type", ["file", "block"])
def test_clear_missing_bitmaps(fake_scheduler, env_type):
    with make_env(env_type, sc.name2type('cow')) as env:
        top_vol = env.chain[0]
        generation = top_vol.getMetaParam(sc.GENERATION)
        vol = {
            'endpoint_type': 'div',
            'sd_id': top_vol.sdUUID,
            'img_id': top_vol.imgUUID,
            'vol_id': top_vol.volUUID,
            'generation': generation
        }
        job = clear_bitmaps.Job(make_uuid(), 0, vol)
        job.run()

        assert jobs.STATUS.DONE == job.status
        vol_info = qemuimg.info(top_vol.getVolumePath())
        bitmaps = vol_info["format-specific"]["data"].get("bitmaps", [])
        assert not bitmaps
        assert top_vol.getMetaParam(sc.GENERATION) == generation + 1

        qemuInfo = top_vol.getQemuImageInfo()
        assert 'bitmaps' not in qemuInfo


@pytest.mark.parametrize("env_type", ["file", "block"])
def test_clear_bitmaps_from_vol_chain(fake_scheduler, env_type):
    bitmap1 = "bitmap1"
    bitmap2 = "bitmap2"

    with make_env(env_type, sc.name2type('cow'), chain_length=3) as env:
        # Add the bitmap to all the volumes in the chain
        for vol in env.chain:
            op = qemuimg.bitmap_add(vol.getVolumePath(), bitmap1)
            op.run()
            op = qemuimg.bitmap_add(vol.getVolumePath(), bitmap2)
            op.run()

        # Clear all the bitmaps from the leaf volume
        leaf_vol = env.chain[2]
        generation = leaf_vol.getMetaParam(sc.GENERATION)
        vol = {
            'endpoint_type': 'div',
            'sd_id': leaf_vol.sdUUID,
            'img_id': leaf_vol.imgUUID,
            'vol_id': leaf_vol.volUUID,
            'generation': generation
        }

        job = clear_bitmaps.Job(make_uuid(), 0, vol)
        job.run()

        assert jobs.STATUS.DONE == job.status
        # Validate that all the bitmaps was removed from the leaf volume
        vol_info = qemuimg.info(leaf_vol.getVolumePath())
        bitmaps = vol_info["format-specific"]["data"].get("bitmaps", [])
        assert not bitmaps
        assert leaf_vol.getMetaParam(sc.GENERATION) == generation + 1

        qemuInfo = leaf_vol.getQemuImageInfo()
        assert 'bitmaps' not in qemuInfo

        # Clear all the bitmaps from an internal volume
        internal_vol = env.chain[1]
        generation = internal_vol.getMetaParam(sc.GENERATION)
        vol = {
            'endpoint_type': 'div',
            'sd_id': internal_vol.sdUUID,
            'img_id': internal_vol.imgUUID,
            'vol_id': internal_vol.volUUID,
            'generation': generation
        }

        job = clear_bitmaps.Job(make_uuid(), 0, vol)
        job.run()

        assert jobs.STATUS.DONE == job.status
        # Validate that all the bitmaps was removed from the internal volume
        vol_info = qemuimg.info(internal_vol.getVolumePath())
        bitmaps = vol_info["format-specific"]["data"].get("bitmaps", [])
        assert not bitmaps
        assert internal_vol.getMetaParam(sc.GENERATION) == generation + 1

        qemuInfo = internal_vol.getQemuImageInfo()
        assert 'bitmaps' not in qemuInfo
