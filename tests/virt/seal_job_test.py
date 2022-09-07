# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import
from __future__ import division

import io
import os

from vdsm import jobs
from vdsm import virtsysprep
from vdsm.common import response
from vdsm.virt.jobs import seal
from vdsm.virt import utils

from testlib import make_uuid
from testlib import namedTemporaryDir
from testlib import recorded
from testlib import VdsmTestCase
from monkeypatch import MonkeyPatch, MonkeyPatchScope


BLANK_UUID = '00000000-0000-0000-0000-000000000000'
FAKE_VIRTSYSPREP = utils.LibguestfsCommand(
    os.path.abspath('fake-virt-sysprep'))
TEARDOWN_ERROR_IMAGE_ID = make_uuid()


def _vol_path(base, domainId, poolId, imageId, ext='.img'):
    return os.path.join(base, '-'.join((poolId, domainId, imageId)) + ext)


class FakeIRS(object):
    def __init__(self, image_path_base):
        self._image_path_base = image_path_base

    @recorded
    def prepareImage(self, domainId, poolId, imageId, volumeId,
                     allowIllegal=False):
        imagepath = _vol_path(self._image_path_base, domainId, poolId, imageId)
        with io.open(imagepath, 'w'):
            pass
        return response.success(path=imagepath)

    @recorded
    def teardownImage(self, domainId, poolId, imageId):
        if imageId == TEARDOWN_ERROR_IMAGE_ID:
            return response.error('teardownError')

        imagepath = _vol_path(self._image_path_base, domainId, poolId, imageId)
        resultpath = _vol_path(self._image_path_base, domainId, poolId,
                               imageId, ext='.res')
        os.rename(imagepath, resultpath)
        return response.success()


class FakeNotifier(object):
    def notify(self, *args, **kwargs):
        pass


class SealJobTest(VdsmTestCase):

    @classmethod
    def setUpClass(cls):
        jobs.start(None, FakeNotifier())

    @classmethod
    def tearDownClass(cls):
        jobs.stop()

    @MonkeyPatch(virtsysprep, '_VIRTSYSPREP', FAKE_VIRTSYSPREP)
    def test_job(self):
        job_id = make_uuid()
        sp_id = make_uuid()
        sd_id = make_uuid()
        img0_id = make_uuid()
        img1_id = make_uuid()
        vol0_id = make_uuid()
        vol1_id = make_uuid()
        images = [
            {'sd_id': sd_id, 'img_id': img0_id, 'vol_id': vol0_id},
            {'sd_id': sd_id, 'img_id': img1_id, 'vol_id': vol1_id},
        ]

        expected = [
            ('prepareImage', (sd_id, sp_id, img0_id, vol0_id),
             {'allowIllegal': True}),
            ('prepareImage', (sd_id, sp_id, img1_id, vol1_id),
             {'allowIllegal': True}),
            ('teardownImage', (sd_id, sp_id, img1_id), {}),
            ('teardownImage', (sd_id, sp_id, img0_id), {}),
        ]
        with namedTemporaryDir() as base:
            irs = FakeIRS(base)

            with MonkeyPatchScope([
                (utils, '_COMMANDS_LOG_DIR', base)
            ]):
                job = seal.Job(BLANK_UUID, job_id, sp_id, images, irs)
                job.autodelete = False
                job.run()

            assert jobs.STATUS.DONE == job.status
            assert expected == irs.__calls__

            for image in images:
                resultpath = _vol_path(base, image['sd_id'], sp_id,
                                       image['img_id'], ext='.res')
                with open(resultpath) as f:
                    data = f.read()
                    assert data == 'fake-virt-sysprep was here'

    @MonkeyPatch(virtsysprep, '_VIRTSYSPREP', FAKE_VIRTSYSPREP)
    def test_teardown_failure(self):
        job_id = make_uuid()
        sp_id = make_uuid()
        sd_id = make_uuid()
        img0_id = make_uuid()
        img1_id = TEARDOWN_ERROR_IMAGE_ID
        vol0_id = make_uuid()
        vol1_id = make_uuid()
        images = [
            {'sd_id': sd_id, 'img_id': img0_id, 'vol_id': vol0_id},
            {'sd_id': sd_id, 'img_id': img1_id, 'vol_id': vol1_id},
        ]

        expected = [
            ('prepareImage', (sd_id, sp_id, img0_id, vol0_id),
             {'allowIllegal': True}),
            ('prepareImage', (sd_id, sp_id, img1_id, vol1_id),
             {'allowIllegal': True}),
            ('teardownImage', (sd_id, sp_id, img1_id), {}),
            ('teardownImage', (sd_id, sp_id, img0_id), {}),
        ]

        with namedTemporaryDir() as base:
            irs = FakeIRS(base)

            with MonkeyPatchScope([
                (utils, '_COMMANDS_LOG_DIR', base)
            ]):
                job = seal.Job(BLANK_UUID, job_id, sp_id, images, irs)
                job.autodelete = False
                job.run()

            assert jobs.STATUS.FAILED == job.status
            assert expected == irs.__calls__
