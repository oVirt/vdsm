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
from __future__ import division

from contextlib import contextmanager

from fakelib import FakeNotifier
from fakelib import FakeScheduler
from monkeypatch import MonkeyPatchScope

from storage.storagefakelib import (
    FakeResourceManager,
    fake_guarded_context,
)

from storage.storagetestlib import (
    fake_env,
    make_qemu_chain,
    write_qemu_chain,
)

from . marks import requires_bitmaps_merge_support
from . qemuio import verify_pattern

from testlib import expandPermutations, make_uuid, permutations
from testlib import VdsmTestCase

from vdsm import jobs
from vdsm.common.units import KiB, MiB
from vdsm.storage import blockVolume
from vdsm.storage import constants as sc
from vdsm.storage import exception as se
from vdsm.storage import guarded
from vdsm.storage import image
from vdsm.storage import merge
from vdsm.storage import qemuimg
from vdsm.storage import resourceManager as rm
from vdsm.storage import volume
from vdsm.storage.sdm.api import merge as api_merge


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
    def make_env(
            self, sd_type, chain_len=2,
            base_format=sc.RAW_FORMAT, qcow2_compat='0.10'):
        size = MiB
        base_fmt = base_format
        with fake_env(sd_type) as env:
            rm = FakeResourceManager()
            with MonkeyPatchScope([
                (guarded, 'context', fake_guarded_context()),
                (image, 'sdCache', env.sdcache),
                (merge, 'sdCache', env.sdcache),
                (blockVolume, 'rm', rm),
                (image, 'Image', FakeImage),
            ]):
                env.chain = make_qemu_chain(
                    env,
                    size,
                    base_fmt,
                    chain_len,
                    qcow2_compat=qcow2_compat)

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
            job = api_merge.Job(job_id, subchain)
            job.run()
            self.assertEqual(job.status, jobs.STATUS.DONE)

            # Verify that the chain data was merged
            for i in range(base_index, top_index + 1):
                offset = i * KiB
                pattern = 0xf0 + i

                # We expect to read all data from top
                verify_pattern(
                    top_vol.volumePath,
                    qemuimg.FORMAT.QCOW2,
                    offset=offset,
                    len=KiB,
                    pattern=pattern)

                # And base, since top was merged into base
                verify_pattern(
                    base_vol.volumePath,
                    sc.fmt2str(base_vol.getFormat()),
                    offset=offset,
                    len=KiB,
                    pattern=pattern)

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
            job = api_merge.Job(job_id, subchain)
            job.run()
            self.assertEqual(job.status, jobs.STATUS.FAILED)
            self.assertEqual(type(job.error), se.prepareIllegalVolumeError)

    def expected_locks(self, base_vol):
        img_ns = rm.getNamespace(sc.IMAGE_NAMESPACE, base_vol.sdUUID)
        ret = [
            # Domain lock
            rm.Lock(sc.STORAGE, base_vol.sdUUID, rm.SHARED),
            # Image lock
            rm.Lock(img_ns, base_vol.imgUUID, rm.EXCLUSIVE),
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
            job = api_merge.Job(job_id, subchain)
            job.run()
            self.assertEqual(job.status, jobs.STATUS.FAILED)
            self.assertEqual(type(job.error), se.VolumeIsNotInChain)

            # Check that validate is called *before* attempting - verify that
            # the chain data was *not* merged
            offset = base_index * KiB
            pattern = 0xf0 + base_index
            verify_pattern(base_vol.volumePath, qemuimg.FORMAT.RAW,
                           offset=offset, len=KiB, pattern=pattern)
            self.assertEqual(base_vol.getMetaParam(sc.GENERATION), 0)

    @requires_bitmaps_merge_support
    @permutations([
        # sd_type, chain_len, base_index, top_index
        ('file', 4, 0, 1),
        ('block', 2, 0, 1),
        ('file', 3, 1, 2),
        ('block', 3, 1, 2),
    ])
    def test_merge_subchain_with_bitmaps(
            self, sd_type, chain_len, base_index, top_index):
        job_id = make_uuid()
        bitmap1_name = 'bitmap1'
        bitmap2_name = 'bitmap2'
        with self.make_env(
                sd_type=sd_type,
                chain_len=chain_len,
                base_format=sc.COW_FORMAT,
                qcow2_compat='1.1') as env:
            base_vol = env.chain[base_index]
            top_vol = env.chain[top_index]
            # Add new bitmap to base_vol and top_vol
            for vol in [base_vol, top_vol]:
                op = qemuimg.bitmap_add(
                    vol.getVolumePath(),
                    bitmap1_name,
                )
                op.run()
            # Add another bitmap to top_vol only
            # to test add + merge
            op = qemuimg.bitmap_add(
                top_vol.getVolumePath(),
                bitmap2_name,
            )
            op.run()

            # Writing data to the chain to modify the bitmaps
            write_qemu_chain(env.chain)

            subchain_info = dict(sd_id=base_vol.sdUUID,
                                 img_id=base_vol.imgUUID,
                                 base_id=base_vol.volUUID,
                                 top_id=top_vol.volUUID,
                                 base_generation=0)
            subchain = merge.SubchainInfo(subchain_info, 0)

            job = api_merge.Job(job_id, subchain, merge_bitmaps=True)
            job.run()

            self.assertEqual(job.status, jobs.STATUS.DONE)

            info = qemuimg.info(base_vol.getVolumePath())
            # TODO: we should improve this test by adding a
            # a verification to the extents that are reported
            # by qemu-nbd.
            assert info['bitmaps'] == [
                {
                    "flags": ["auto"],
                    "name": bitmap1_name,
                    "granularity": 65536
                },
                {
                    "flags": ["auto"],
                    "name": bitmap2_name,
                    "granularity": 65536
                },
            ]
