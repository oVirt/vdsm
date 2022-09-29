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
from vdsm.common import exception
from vdsm.common import cmdutils
from vdsm.common.units import MiB
from vdsm.storage import blockVolume
from vdsm.storage import constants as sc
from vdsm.storage import exception as se
from vdsm.storage import guarded
from vdsm.storage import qemuimg
from vdsm.storage.sdm import volume_info
from vdsm.storage.sdm.api import add_bitmap


def failure(*args, **kwargs):
    raise cmdutils.Error("code", "out", "err", "Fail amend")


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
def test_add_bitmap(fake_scheduler, env_type):
    job_id = make_uuid()
    bitmap = "bitmap"

    with make_env(env_type, sc.name2type('cow')) as env:
        env_vol = env.chain[0]
        generation = env_vol.getMetaParam(sc.GENERATION)
        vol = dict(endpoint_type='div', sd_id=env_vol.sdUUID,
                   img_id=env_vol.imgUUID, vol_id=env_vol.volUUID,
                   generation=generation)

        job = add_bitmap.Job(job_id, 0, vol, bitmap)
        job.run()

        assert jobs.STATUS.DONE == job.status
        vol_info = qemuimg.info(env_vol.getVolumePath())
        qcow2_data = vol_info["format-specific"]["data"]
        assert len(qcow2_data["bitmaps"]) == 1
        assert qcow2_data["bitmaps"][0]["name"] == bitmap
        assert env_vol.getMetaParam(sc.GENERATION) == generation + 1


@pytest.mark.parametrize("env_type", ["file", "block"])
def test_vol_type_not_qcow(fake_scheduler, env_type):
    job_id = make_uuid()
    bitmap = "bitmap"

    with make_env(env_type, sc.name2type('raw')) as env:
        env_vol = env.chain[0]
        generation = env_vol.getMetaParam(sc.GENERATION)
        vol = dict(endpoint_type='div', sd_id=env_vol.sdUUID,
                   img_id=env_vol.imgUUID, vol_id=env_vol.volUUID,
                   generation=generation)

        job = add_bitmap.Job(job_id, 0, vol, bitmap)
        job.run()

        assert job.status == jobs.STATUS.FAILED
        assert type(job.error) == se.GeneralException
        assert env_vol.getLegality() == sc.LEGAL_VOL
        assert env_vol.getMetaParam(sc.GENERATION) == generation


@pytest.mark.parametrize("env_type", ["file", "block"])
def test_qemu_bitmap_add_failure(fake_scheduler, monkeypatch, env_type):
    monkeypatch.setattr(qemuimg, "bitmap_add", failure)
    job_id = make_uuid()

    with make_env(env_type, sc.name2type('cow')) as env:
        env_vol = env.chain[0]
        generation = env_vol.getMetaParam(sc.GENERATION)
        vol = dict(endpoint_type='div', sd_id=env_vol.sdUUID,
                   img_id=env_vol.imgUUID, vol_id=env_vol.volUUID,
                   generation=generation)

        job = add_bitmap.Job(job_id, 0, vol, "bitmap")
        job.run()

        assert job.status == jobs.STATUS.FAILED
        assert type(job.error) == exception.AddBitmapError
        assert env_vol.getLegality() == sc.LEGAL_VOL
        assert env_vol.getMetaParam(sc.GENERATION) == generation


@pytest.mark.parametrize("env_type", ["file", "block"])
def test_bitmap_already_exists(fake_scheduler, env_type):
    job_id = make_uuid()
    bitmap = "bitmap"

    with make_env(env_type, sc.name2type('cow')) as env:
        env_vol = env.chain[0]
        op = qemuimg.bitmap_add(env_vol.getVolumePath(), bitmap)
        op.run()

        generation = env_vol.getMetaParam(sc.GENERATION)
        vol = dict(endpoint_type='div', sd_id=env_vol.sdUUID,
                   img_id=env_vol.imgUUID, vol_id=env_vol.volUUID,
                   generation=generation)

        job = add_bitmap.Job(job_id, 0, vol, bitmap)
        job.run()

        assert job.status == jobs.STATUS.FAILED
        assert type(job.error) == se.GeneralException
        assert env_vol.getLegality() == sc.LEGAL_VOL
        assert env_vol.getMetaParam(sc.GENERATION) == generation
