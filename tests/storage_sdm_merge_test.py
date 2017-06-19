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

from fakelib import FakeNotifier
from fakelib import FakeScheduler
from monkeypatch import MonkeyPatchScope
from storagefakelib import FakeResourceManager
from storagetestlib import fake_env
from storagefakelib import fake_guarded_context
from storagetestlib import make_qemu_chain
from storagetestlib import qemu_pattern_verify, write_qemu_chain
from testlib import expandPermutations, make_uuid, permutations
from testlib import VdsmTestCase
from testlib import wait_for_job

from vdsm import jobs
from vdsm import qemuimg
from vdsm.storage import constants as sc
from vdsm.storage import exception as se
from vdsm.storage import guarded
from vdsm.storage import resourceManager as rm

from storage import image
from storage import merge
from storage import blockVolume, volume

import storage.sdm.api.merge


class FakeImage(object):
    def __init__(self, repoPath):
        pass


@expandPermutations
class TestMergeSubchain(VdsmTestCase):

    def setUp(self):
        self.scheduler = FakeScheduler()
        self.notifier = FakeNotifier()
        jobs.start(self.scheduler, self.notifier)

    def tearDown(self):
        jobs._clear()

    @contextmanager
    def make_env(self, sd_type, chain_len=2):
        size = 1048576
        base_fmt = sc.RAW_FORMAT
        with fake_env(sd_type) as env:
            rm = FakeResourceManager()
            with MonkeyPatchScope([
                (guarded, 'context', fake_guarded_context()),
                (image, 'sdCache', env.sdcache),
                (merge, 'sdCache', env.sdcache),
                (blockVolume, 'rm', rm),
                (image, 'Image', FakeImage),
            ]):
                env.chain = make_qemu_chain(env, size, base_fmt, chain_len)

                def fake_chain(sdUUID, imgUUID, volUUID=None):
                    return env.chain
                image.Image.getChain = fake_chain

                yield env

    @permutations([
        # sd_type, chain_len, base_index, top_index
        ('file', 2, 0, 1),
        ('block', 2, 0, 1),
        ('file', 3, 1, 2),
        ('block', 3, 1, 2),
    ])
    def test_merge_subchain(self, sd_type, chain_len, base_index, top_index):
        job_id = make_uuid()
        with self.make_env(sd_type=sd_type, chain_len=chain_len) as env:
            write_qemu_chain(env.chain)
            base_vol = env.chain[base_index]
            top_vol = env.chain[top_index]

            subchain_info = dict(sd_id=base_vol.sdUUID,
                                 img_id=base_vol.imgUUID,
                                 base_id=base_vol.volUUID,
                                 top_id=top_vol.volUUID,
                                 base_generation=0)
            subchain = merge.SubchainInfo(subchain_info, 0)
            job = storage.sdm.api.merge.Job(job_id, subchain)
            job.run()
            wait_for_job(job)
            self.assertEqual(job.status, jobs.STATUS.DONE)

            # Verify that the chain data was merged
            for i in range(base_index, top_index + 1):
                offset = i * 1024
                pattern = 0xf0 + i
                # We expect to read all data from top
                qemu_pattern_verify(top_vol.volumePath, qemuimg.FORMAT.QCOW2,
                                    offset=offset, len=1024, pattern=pattern)
                # And base, since top was merged into base
                qemu_pattern_verify(base_vol.volumePath,
                                    sc.fmt2str(base_vol.getFormat()),
                                    offset=offset, len=1024, pattern=pattern)

            self.assertEqual(sorted(self.expected_locks(base_vol)),
                             sorted(guarded.context.locks))

            self.assertEqual(base_vol.getLegality(), sc.LEGAL_VOL)
            self.assertEqual(base_vol.getMetaParam(sc.GENERATION), 1)

    @permutations([
        # volume
        ('base',),
        ('top',),
    ])
    def test_merge_illegal_volume(self, volume):
        job_id = make_uuid()
        with self.make_env(sd_type='block', chain_len=2) as env:
            write_qemu_chain(env.chain)
            base_vol = env.chain[0]
            top_vol = env.chain[1]
            if volume == 'base':
                base_vol.setLegality(sc.ILLEGAL_VOL)
            else:
                top_vol.setLegality(sc.ILLEGAL_VOL)

            subchain_info = dict(sd_id=base_vol.sdUUID,
                                 img_id=base_vol.imgUUID,
                                 base_id=base_vol.volUUID,
                                 top_id=top_vol.volUUID,
                                 base_generation=0)
            subchain = merge.SubchainInfo(subchain_info, 0)
            job = storage.sdm.api.merge.Job(job_id, subchain)
            job.run()
            self.assertEqual(job.status, jobs.STATUS.FAILED)
            self.assertEqual(type(job.error), se.prepareIllegalVolumeError)

    def expected_locks(self, base_vol):
        img_ns = rm.getNamespace(sc.IMAGE_NAMESPACE, base_vol.sdUUID)
        ret = [
            # Domain lock
            rm.ResourceManagerLock(sc.STORAGE, base_vol.sdUUID, rm.SHARED),
            # Image lock
            rm.ResourceManagerLock(img_ns, base_vol.imgUUID, rm.EXCLUSIVE),
            # Volume lease
            volume.VolumeLease(
                0, base_vol.sdUUID, base_vol.imgUUID, base_vol.volUUID)
        ]
        return ret

    def test_subchain_validation(self):
        job_id = make_uuid()
        with self.make_env(sd_type='file', chain_len=2) as env:
            write_qemu_chain(env.chain)
            base_index = 0
            top_index = 1
            base_vol = env.chain[base_index]
            base_vol.setLegality(sc.ILLEGAL_VOL)
            top_vol = env.chain[top_index]
            subchain_info = dict(sd_id=top_vol.sdUUID,
                                 img_id=top_vol.imgUUID,
                                 base_id=base_vol.imgUUID,
                                 top_id=top_vol.volUUID,
                                 base_generation=0)
            subchain = merge.SubchainInfo(subchain_info, 0)

            def fail():
                raise se.VolumeIsNotInChain(None, None, None)

            # We already tested that subchain validate does the right thing,
            # here we test that this job care to call subchain validate.
            subchain.validate = fail
            job = storage.sdm.api.merge.Job(job_id, subchain)
            job.run()
            wait_for_job(job)
            self.assertEqual(job.status, jobs.STATUS.FAILED)
            self.assertEqual(type(job.error), se.VolumeIsNotInChain)

            # Check that validate is called *before* attempting - verify that
            # the chain data was *not* merged
            offset = base_index * 1024
            pattern = 0xf0 + base_index
            qemu_pattern_verify(base_vol.volumePath, qemuimg.FORMAT.RAW,
                                offset=offset, len=1024, pattern=pattern)
            self.assertEqual(base_vol.getMetaParam(sc.GENERATION), 0)
