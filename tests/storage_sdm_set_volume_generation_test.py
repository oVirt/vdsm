#
# Copyright 2016 Red Hat, Inc.
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

from fakelib import FakeScheduler
from monkeypatch import MonkeyPatchScope
from storagefakelib import fake_guarded_context
from storagetestlib import fake_env
from storagetestlib import make_qemu_chain
from testlib import make_uuid
from testlib import VdsmTestCase, expandPermutations, permutations
from testlib import wait_for_job

from vdsm import jobs
from vdsm.storage import constants as sc
from vdsm.storage import exception as se
from vdsm.storage import guarded

import storage.sdm.api.set_volume_generation


@expandPermutations
class TestSetVolumeGeneration(VdsmTestCase):
    SIZE = 1024 * 1024

    def setUp(self):
        self.scheduler = FakeScheduler()
        jobs.start(self.scheduler)

    def tearDown(self):
        jobs._clear()

    @contextmanager
    def get_vol(self, storage_type):
        with fake_env(storage_type) as env:
            with MonkeyPatchScope([
                (guarded, 'context', fake_guarded_context()),
                (storage.sdm.api.copy_data, 'sdCache', env.sdcache),
            ]):
                vols = make_qemu_chain(env, self.SIZE, sc.RAW_FORMAT, 1)
                yield vols[0]

    def run_job(self, storage_type, cur_gen):
        with self.get_vol(storage_type) as vol:
            job_id = make_uuid()
            info = dict(endpoint_type='div', sd_id=vol.sdUUID,
                        img_id=vol.imgUUID, vol_id=vol.volUUID,
                        generation=cur_gen)
            job = storage.sdm.api.set_volume_generation.Job(job_id, 0, info, 1)
            job.run()
        wait_for_job(job)
        return job

    @permutations((('file',), ('block',)))
    def test_success(self, storage_type):
        job = self.run_job(storage_type, 0)
        self.assertEqual(jobs.STATUS.DONE, job.status)

    @permutations((('file',), ('block',)))
    def test_generation_mismatch(self, storage_type):
        job = self.run_job(storage_type, 100)
        self.assertEqual(jobs.STATUS.FAILED, job.status)
        self.assertEqual(se.GenerationMismatch.code, job.error.code)
