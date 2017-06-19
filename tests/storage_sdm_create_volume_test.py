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

from monkeypatch import MonkeyPatchScope
from storagefakelib import FakeResourceManager
from testlib import make_uuid
from testlib import VdsmTestCase, recorded, expandPermutations, permutations
from testlib import wait_for_job

from vdsm.common import exception
from vdsm import jobs
from vdsm.storage import constants as sc
from vdsm.storage import exception as se
from vdsm.storage import resourceManager as rm

from storage import fileVolume

import storage.sdm.api.create_volume


class FakeDomainManifest(object):
    def __init__(self, sd_id):
        self.sdUUID = sd_id

    def validateCreateVolumeParams(self, *args):
        pass

    @recorded
    def acquireDomainLock(self, host_id):
        pass

    @recorded
    def releaseDomainLock(self):
        pass

    @contextmanager
    def domain_lock(self, host_id):
        self.acquireDomainLock(host_id)
        try:
            yield
        finally:
            self.releaseDomainLock()

    def getVolumeClass(self):
        return fileVolume.FileVolumeManifest

    def get_volume_artifacts(self, img_id, vol_id):
        return FakeVolumeArtifacts(self, img_id, vol_id)


class FakeVolumeArtifacts(object):
    def __init__(self, dom_manifest, img_id, vol_id):
        self.dom_manifest = dom_manifest
        self.img_id = img_id
        self.vol_id = vol_id

    # TODO: record these calls and verify them in the tests.

    def create(self, *args):
        pass

    def commit(self):
        pass


def _get_vol_info():
    return dict(sd_id=make_uuid(), img_id=make_uuid(),
                vol_id=make_uuid(), virtual_size=2048,
                vol_format='RAW', disk_type='SYSTEM')


class CreateVolumeTests(VdsmTestCase):

    def _get_args(self):
        job_id = make_uuid()
        host_id = 1
        sd_manifest = FakeDomainManifest(make_uuid())
        vol_info = _get_vol_info()
        vol_info_obj = storage.sdm.api.create_volume.CreateVolumeInfo(vol_info)
        return dict(job_id=job_id, host_id=host_id, sd_manifest=sd_manifest,
                    vol_info=vol_info_obj)

    @contextmanager
    def _fake_env(self):
        self.rm = FakeResourceManager()
        with MonkeyPatchScope([(storage.sdm.api.create_volume, 'rm',
                                self.rm)]):
            yield

    def test_create_volume(self):
        args = self._get_args()
        job = storage.sdm.api.create_volume.Job(**args)

        with self._fake_env():
            job.run()
        wait_for_job(job)
        self.assertEqual(jobs.STATUS.DONE, job.status)
        self.assertIsNone(job.progress)
        self.assertNotIn('error', job.info())

        # Verify that the domain lock was acquired and released
        self.assertEqual([('acquireDomainLock', (1,), {}),
                          ('releaseDomainLock', (), {})],
                         args['sd_manifest'].__calls__)

        # Verify that the image resource was locked and released
        image_ns = rm.getNamespace(sc.IMAGE_NAMESPACE, job.sd_manifest.sdUUID)
        rm_args = (image_ns, job.vol_info.img_id, rm.EXCLUSIVE)
        self.assertEqual([('acquireResource', rm_args, {}),
                          ('releaseResource', rm_args, {})],
                         self.rm.__calls__)

    def test_create_volume_domainlock_contended(self):
        def error(*args):
            raise se.AcquireLockFailure('id', 'rc', 'out', 'err')

        args = self._get_args()
        args['sd_manifest'].acquireDomainLock = error
        job = storage.sdm.api.create_volume.Job(**args)
        job.run()
        wait_for_job(job)
        self.assertEqual(jobs.STATUS.FAILED, job.status)
        self.assertEqual(se.AcquireLockFailure.code,
                         job.info()['error']['code'])


class CreateVolumeInfoTests(VdsmTestCase):

    def test_missing_parameter(self):
        info = _get_vol_info()
        del info['sd_id']
        self.assertRaises(exception.MissingParameter,
                          storage.sdm.api.create_volume.CreateVolumeInfo, info)

    def test_default_parameter(self):
        info = _get_vol_info()
        info_obj = storage.sdm.api.create_volume.CreateVolumeInfo(info)
        self.assertEqual('', info_obj.description)
        self.assertEqual(None, info_obj.initial_size)
        self.assertIsNone(info_obj.parent)

    def test_bad_enum_value(self):
        info = _get_vol_info()
        info['vol_format'] = 'foo'
        self.assertRaises(se.InvalidParameterException,
                          storage.sdm.api.create_volume.CreateVolumeInfo, info)


@expandPermutations
class ParentVolumeInfoTests(VdsmTestCase):

    @permutations([
        [{}], [{'vol_id': 'foo'}], [{'img_id': 'bar'}],
    ])
    def test_incomplete_params_raises(self, params):
        self.assertRaises(
            exception.MissingParameter,
            storage.sdm.api.create_volume.ParentVolumeInfo, params)

    def test_complete_params(self):
        params = dict(vol_id='foo', img_id='bar')
        obj = storage.sdm.api.create_volume.ParentVolumeInfo(params)
        self.assertEqual(params['vol_id'], obj.vol_id)
        self.assertEqual(params['img_id'], obj.img_id)
