#
# Copyright 2021 Red Hat, Inc.
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
import subprocess

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
from vdsm.storage.sdm.api import remove_bitmap

from . marks import requires_bitmaps_support


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


@requires_bitmaps_support
@pytest.mark.parametrize("env_type", ["file", "block"])
def test_add_remove_bitmap(fake_scheduler, env_type):
    bitmap1 = "bitmap1"
    bitmap2 = "bitmap2"

    with make_env(env_type, sc.name2type('cow')) as env:
        top_vol = env.chain[0]

        # Add bitmaps to the volume
        for bitmap in [bitmap1, bitmap2]:
            op = qemuimg.bitmap_add(top_vol.getVolumePath(), bitmap)
            op.run()

        # Remove one of the created bitmap
        generation = top_vol.getMetaParam(sc.GENERATION)
        vol = {
            'endpoint_type': 'div',
            'sd_id': top_vol.sdUUID,
            'img_id': top_vol.imgUUID,
            'vol_id': top_vol.volUUID,
            'generation': generation
        }
        job = remove_bitmap.Job(make_uuid(), 0, vol, bitmap1)
        job.run()

        assert jobs.STATUS.DONE == job.status
        vol_info = qemuimg.info(top_vol.getVolumePath())
        bitmaps = [b["name"] for b in
                   vol_info["format-specific"]["data"].get("bitmaps", [])]
        assert bitmap1 not in bitmaps and bitmap2 in bitmaps
        assert top_vol.getMetaParam(sc.GENERATION) == generation + 1


@requires_bitmaps_support
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

        # Remove bitmap failed for RAW volume
        job = remove_bitmap.Job(make_uuid(), 0, vol, "bitmap")
        job.run()

        assert job.status == jobs.STATUS.FAILED
        assert type(job.error) == se.GeneralException
        assert top_vol.getLegality() == sc.LEGAL_VOL
        assert top_vol.getMetaParam(sc.GENERATION) == generation


@requires_bitmaps_support
@pytest.mark.parametrize("env_type", ["file", "block"])
def test_remove_bitmap_non_leaf_vol(fake_scheduler, env_type):
    bitmap1 = "bitmap1"
    bitmap2 = "bitmap2"

    with make_env(env_type, sc.name2type('cow'), chain_length=2) as env:
        base_vol = env.chain[0]

        # Add bitmaps to the volume
        for bitmap in [bitmap1, bitmap2]:
            op = qemuimg.bitmap_add(base_vol.getVolumePath(), bitmap)
            op.run()

        # Remove one of the created bitmap
        generation = base_vol.getMetaParam(sc.GENERATION)
        vol = {
            'endpoint_type': 'div',
            'sd_id': base_vol.sdUUID,
            'img_id': base_vol.imgUUID,
            'vol_id': base_vol.volUUID,
            'generation': generation
        }
        job = remove_bitmap.Job(make_uuid(), 0, vol, bitmap1)
        job.run()

        assert jobs.STATUS.DONE == job.status
        vol_info = qemuimg.info(base_vol.getVolumePath())
        bitmaps = [b["name"] for b in
                   vol_info["format-specific"]["data"].get("bitmaps", [])]
        assert bitmap1 not in bitmaps and bitmap2 in bitmaps
        assert base_vol.getMetaParam(sc.GENERATION) == generation + 1


@requires_bitmaps_support
@pytest.mark.parametrize("env_type", ["file", "block"])
def test_remove_missing_bitmap(fake_scheduler, env_type):
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
        job = remove_bitmap.Job(make_uuid(), 0, vol, "bitmap")
        job.run()

        assert jobs.STATUS.DONE == job.status
        vol_info = qemuimg.info(top_vol.getVolumePath())
        bitmaps = vol_info["format-specific"]["data"].get("bitmaps", [])
        assert not bitmaps
        assert top_vol.getMetaParam(sc.GENERATION) == generation + 1


@requires_bitmaps_support
@pytest.mark.parametrize("env_type", ["file", "block"])
def test_remove_inactive_bitmap(fake_scheduler, env_type):
    bitmap = "bitmap"

    with make_env(env_type, sc.name2type('cow')) as env:
        base_vol = env.chain[0]

        # Add inactive bitmap to base volume
        op = qemuimg.bitmap_add(
            base_vol.getVolumePath(),
            bitmap,
            enable=False
        )
        op.run()

        generation = base_vol.getMetaParam(sc.GENERATION)
        vol = {
            'endpoint_type': 'div',
            'sd_id': base_vol.sdUUID,
            'img_id': base_vol.imgUUID,
            'vol_id': base_vol.volUUID,
            'generation': generation
        }
        job = remove_bitmap.Job(make_uuid(), 0, vol, bitmap)
        job.run()

        assert jobs.STATUS.DONE == job.status
        vol_info = qemuimg.info(base_vol.getVolumePath())
        bitmaps = vol_info["format-specific"]["data"].get("bitmaps", [])
        assert not bitmaps
        assert base_vol.getMetaParam(sc.GENERATION) == generation + 1


@requires_bitmaps_support
@pytest.mark.parametrize("env_type", ["file", "block"])
def test_remove_invalid_bitmap(fake_scheduler, env_type):
    bitmap = "bitmap"

    with make_env(env_type, sc.name2type('cow')) as env:
        base_vol = env.chain[0]

        # Add bitmap to base volume
        op = qemuimg.bitmap_add(
            base_vol.getVolumePath(),
            bitmap,
        )
        op.run()

        # Simulate qemu crash, leaving bitmaps with the "in-use"
        # flag by opening the image for writing and killing the process.
        subprocess.run(
            ["qemu-io", "-c", "sigraise 9", base_vol.getVolumePath()])

        generation = base_vol.getMetaParam(sc.GENERATION)
        vol = {
            'endpoint_type': 'div',
            'sd_id': base_vol.sdUUID,
            'img_id': base_vol.imgUUID,
            'vol_id': base_vol.volUUID,
            'generation': generation
        }
        job = remove_bitmap.Job(make_uuid(), 0, vol, bitmap)
        job.run()

        assert jobs.STATUS.DONE == job.status
        vol_info = qemuimg.info(base_vol.getVolumePath())
        bitmaps = vol_info["format-specific"]["data"].get("bitmaps", [])
        assert not bitmaps
        assert base_vol.getMetaParam(sc.GENERATION) == generation + 1
