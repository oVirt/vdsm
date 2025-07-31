# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import
from __future__ import division

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
from vdsm.storage.sdm.api import amend_volume, copy_data


def failure(*args, **kwargs):
    raise cmdutils.Error("code", "out", "err", "Fail amend")


DEFAULT_SIZE = MiB


@contextmanager
def make_env(storage_type, fmt, chain_length=1,
             size=DEFAULT_SIZE, sd_version=3, qcow2_compat='0.10'):
    with fake_env(storage_type, sd_version=sd_version) as env:
        rm = FakeResourceManager()
        with MonkeyPatchScope([
            (guarded, 'context', fake_guarded_context()),
            (amend_volume, 'sdCache', env.sdcache),
            (copy_data, 'sdCache', env.sdcache),
            (volume_info, 'sdCache', env.sdcache),
            (blockVolume, 'rm', rm),
        ]):
            env.chain = make_qemu_chain(env, size, fmt, chain_length,
                                        qcow2_compat=qcow2_compat)
            yield env


@pytest.mark.parametrize("env_type", ["file", "block"])
def test_amend(fake_scheduler, env_type):
    fmt = sc.name2type('cow')
    job_id = make_uuid()
    with make_env(env_type, fmt, sd_version=4,
                  qcow2_compat='0.10') as env:
        env_vol = env.chain[0]
        generation = env_vol.getMetaParam(sc.GENERATION)
        assert env_vol.getQemuImageInfo()['compat'] == '0.10'
        vol = dict(endpoint_type='div', sd_id=env_vol.sdUUID,
                   img_id=env_vol.imgUUID, vol_id=env_vol.volUUID,
                   generation=generation)
        qcow2_attr = dict(compat='1.1')
        job = amend_volume.Job(job_id, 0, vol, qcow2_attr)
        job.run()
        assert jobs.STATUS.DONE == job.status
        assert env_vol.getQemuImageInfo()['compat'] == '1.1'
        assert env_vol.getMetaParam(sc.GENERATION) == generation + 1


@pytest.mark.parametrize("env_type", ["file", "block"])
def test_vol_type_not_qcow(fake_scheduler, env_type):
    fmt = sc.name2type('raw')
    job_id = make_uuid()
    with make_env(env_type, fmt, sd_version=4) as env:
        env_vol = env.chain[0]
        generation = env_vol.getMetaParam(sc.GENERATION)
        vol = dict(endpoint_type='div', sd_id=env_vol.sdUUID,
                   img_id=env_vol.imgUUID, vol_id=env_vol.volUUID,
                   generation=generation)
        qcow2_attr = dict(compat='1.1')
        job = amend_volume.Job(job_id, 0, vol, qcow2_attr)
        job.run()
        assert job.status == jobs.STATUS.FAILED
        assert isinstance(job.error, se.GeneralException)
        assert env_vol.getLegality() == sc.LEGAL_VOL
        assert env_vol.getMetaParam(sc.GENERATION) == generation


@pytest.mark.parametrize("env_type", ["file", "block"])
def test_qemu_amend_failure(fake_scheduler, monkeypatch, env_type):
    monkeypatch.setattr(qemuimg, "amend", failure)
    fmt = sc.name2type('raw')
    job_id = make_uuid()
    with make_env(env_type, fmt, sd_version=4) as env:
        env_vol = env.chain[0]
        generation = env_vol.getMetaParam(sc.GENERATION)
        vol = dict(endpoint_type='div', sd_id=env_vol.sdUUID,
                   img_id=env_vol.imgUUID, vol_id=env_vol.volUUID,
                   generation=generation)
        qcow2_attr = dict(compat='1.1')
        job = amend_volume.Job(job_id, 0, vol, qcow2_attr)
        job.run()
        assert job.status == jobs.STATUS.FAILED
        assert isinstance(job.error, se.GeneralException)
        assert env_vol.getLegality() == sc.LEGAL_VOL
        assert env_vol.getMetaParam(sc.GENERATION) == generation


@pytest.mark.parametrize("env_type", ["file", "block"])
def test_sd_version_no_support_compat(fake_scheduler, env_type):
    fmt = sc.name2type('cow')
    job_id = make_uuid()
    with make_env(env_type, fmt, sd_version=3) as env:
        env_vol = env.chain[0]
        generation = env_vol.getMetaParam(sc.GENERATION)
        vol = dict(endpoint_type='div', sd_id=env_vol.sdUUID,
                   img_id=env_vol.imgUUID, vol_id=env_vol.volUUID,
                   generation=generation)
        qcow2_attr = dict(compat='1.1')
        job = amend_volume.Job(job_id, 0, vol, qcow2_attr)
        job.run()
        assert job.status == jobs.STATUS.FAILED
        assert isinstance(job.error, se.GeneralException)
        assert env_vol.getLegality() == sc.LEGAL_VOL
        assert env_vol.getMetaParam(sc.GENERATION) == generation
