#
# Copyright 2016-2017 Red Hat, Inc.
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

from contextlib import contextmanager

from fakelib import FakeNotifier
from fakelib import FakeScheduler
from monkeypatch import MonkeyPatchScope, MonkeyPatch

from storage.storagefakelib import (
    FakeResourceManager,
    fake_guarded_context,
)

from storage.storagetestlib import (
    fake_env,
    make_qemu_chain,
)

from testlib import make_uuid
from testlib import VdsmTestCase, expandPermutations, permutations
from testlib import wait_for_job

from vdsm import cmdutils
from vdsm import jobs
from vdsm.storage import blockVolume
from vdsm.storage import constants as sc
from vdsm.storage import exception as se
from vdsm.storage import guarded
from vdsm.storage import qemuimg
from vdsm.storage.sdm import volume_info
from vdsm.storage.sdm.api import amend_volume, copy_data


def failure(*args, **kwargs):
    raise cmdutils.Error("code", "out", "err", "Fail amend")


@expandPermutations
class TestAmendVolume(VdsmTestCase):
    DEFAULT_SIZE = 1048576

    def setUp(self):
        self.scheduler = FakeScheduler()
        self.notifier = FakeNotifier()
        jobs.start(self.scheduler, self.notifier)

    def tearDown(self):
        jobs._clear()

    @contextmanager
    def make_env(self, storage_type, fmt, chain_length=1,
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

    @permutations((('file',), ('block',)))
    def test_amend(self, env_type):
        fmt = sc.name2type('cow')
        job_id = make_uuid()
        with self.make_env(env_type, fmt, sd_version=4,
                           qcow2_compat='0.10') as env:
            env_vol = env.chain[0]
            generation = env_vol.getMetaParam(sc.GENERATION)
            self.assertEqual('0.10', env_vol.getQemuImageInfo()['compat'])
            vol = dict(endpoint_type='div', sd_id=env_vol.sdUUID,
                       img_id=env_vol.imgUUID, vol_id=env_vol.volUUID,
                       generation=generation)
            qcow2_attr = dict(compat='1.1')
            job = amend_volume.Job(job_id, 0, vol, qcow2_attr)
            job.run()
            wait_for_job(job)
            self.assertEqual(jobs.STATUS.DONE, job.status)
            self.assertEqual('1.1', env_vol.getQemuImageInfo()['compat'])
            self.assertEqual(generation + 1,
                             env_vol.getMetaParam(sc.GENERATION))

    @permutations((('file',), ('block',)))
    def test_vol_type_not_qcow(self, env_type):
        fmt = sc.name2type('raw')
        job_id = make_uuid()
        with self.make_env(env_type, fmt, sd_version=4) as env:
            env_vol = env.chain[0]
            generation = env_vol.getMetaParam(sc.GENERATION)
            vol = dict(endpoint_type='div', sd_id=env_vol.sdUUID,
                       img_id=env_vol.imgUUID, vol_id=env_vol.volUUID,
                       generation=generation)
            qcow2_attr = dict(compat='1.1')
            job = amend_volume.Job(job_id, 0, vol, qcow2_attr)
            job.run()
            wait_for_job(job)
            self.assertEqual(jobs.STATUS.FAILED, job.status)
            self.assertEqual(type(job.error), se.GeneralException)
            self.assertEqual(sc.LEGAL_VOL, env_vol.getLegality())
            self.assertEqual(generation, env_vol.getMetaParam(sc.GENERATION))

    @MonkeyPatch(qemuimg, 'amend', failure)
    @permutations((('file',), ('block',)))
    def test_qemu_amend_failure(self, env_type):
        fmt = sc.name2type('raw')
        job_id = make_uuid()
        with self.make_env(env_type, fmt, sd_version=4) as env:
            env_vol = env.chain[0]
            generation = env_vol.getMetaParam(sc.GENERATION)
            vol = dict(endpoint_type='div', sd_id=env_vol.sdUUID,
                       img_id=env_vol.imgUUID, vol_id=env_vol.volUUID,
                       generation=generation)
            qcow2_attr = dict(compat='1.1')
            job = amend_volume.Job(job_id, 0, vol, qcow2_attr)
            job.run()
            wait_for_job(job)
            self.assertEqual(jobs.STATUS.FAILED, job.status)
            self.assertEqual(type(job.error), se.GeneralException)
            self.assertEqual(sc.LEGAL_VOL, env_vol.getLegality())
            self.assertEqual(generation, env_vol.getMetaParam(sc.GENERATION))

    @permutations((('file',), ('block',)))
    def test_sd_version_no_support_compat(self, env_type):
        fmt = sc.name2type('cow')
        job_id = make_uuid()
        with self.make_env(env_type, fmt, sd_version=3) as env:
            env_vol = env.chain[0]
            generation = env_vol.getMetaParam(sc.GENERATION)
            vol = dict(endpoint_type='div', sd_id=env_vol.sdUUID,
                       img_id=env_vol.imgUUID, vol_id=env_vol.volUUID,
                       generation=generation)
            qcow2_attr = dict(compat='1.1')
            job = amend_volume.Job(job_id, 0, vol, qcow2_attr)
            job.run()
            wait_for_job(job)
            self.assertEqual(jobs.STATUS.FAILED, job.status)
            self.assertEqual(type(job.error), se.GeneralException)
            self.assertEqual(sc.LEGAL_VOL, env_vol.getLegality())
            self.assertEqual(generation, env_vol.getMetaParam(sc.GENERATION))
