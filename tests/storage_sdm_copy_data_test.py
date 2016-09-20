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

import uuid
from contextlib import contextmanager

from fakelib import FakeScheduler
from monkeypatch import MonkeyPatchScope
from storagefakelib import FakeResourceManager
from storagetestlib import fake_env
from storagetestlib import make_qemu_chain, write_qemu_chain, verify_qemu_chain
from storagetestlib import ChainVerificationError
from testlib import VdsmTestCase, expandPermutations, permutations
from testlib import wait_for_job

from vdsm import jobs
from vdsm import qemuimg
from vdsm.storage import constants as sc
from vdsm.storage import guarded
from vdsm.storage import workarounds

from storage import blockVolume, sd, volume
from storage import resourceManager

import storage.sdm.api.copy_data


class fake_guarded_context(object):

    def __init__(self):
        self.locks = None

    def __call__(self, locks):
        self.locks = locks
        return self

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        pass


@expandPermutations
class TestCopyDataDIV(VdsmTestCase):
    DEFAULT_SIZE = 1048576

    def setUp(self):
        self.scheduler = FakeScheduler()
        jobs.start(self.scheduler)

    def tearDown(self):
        jobs._clear()

    @contextmanager
    def get_vols(self, storage_type, src_fmt, dst_fmt, chain_length=1,
                 size=DEFAULT_SIZE):
        with fake_env(storage_type) as env:
            rm = FakeResourceManager()
            with MonkeyPatchScope([
                (guarded, 'context', fake_guarded_context()),
                (storage.sdm.api.copy_data, 'sdCache', env.sdcache),
                (blockVolume, 'rmanager', rm),
            ]):
                src_vols = make_qemu_chain(env, size, src_fmt, chain_length)
                dst_vols = make_qemu_chain(env, size, dst_fmt, chain_length)
                yield (src_vols, dst_vols)

    def make_volume(self, env, img_id, vol_id, parent_vol_id, vol_fmt):
        if parent_vol_id != sc.BLANK_UUID:
            vol_fmt = sc.COW_FORMAT
        env.make_volume(self.DEFAULT_SIZE, img_id, vol_id,
                        parent_vol_id=parent_vol_id, vol_format=vol_fmt)
        vol = env.sd_manifest.produceVolume(img_id, vol_id)
        if vol_fmt == sc.COW_FORMAT:
            backing = parent_vol_id if parent_vol_id != sc.BLANK_UUID else None
            qemuimg.create(vol.volumePath, size=self.DEFAULT_SIZE,
                           format=qemuimg.FORMAT.QCOW2, backing=backing)
        return vol

    def expected_locks(self, src_vol, dst_vol):
        src_img_ns = sd.getNamespace(sc.IMAGE_NAMESPACE, src_vol.sdUUID)
        dst_img_ns = sd.getNamespace(sc.IMAGE_NAMESPACE, dst_vol.sdUUID)
        ret = [
            # Domain lock for each volume
            resourceManager.ResourceManagerLock(
                sc.STORAGE, src_vol.sdUUID, resourceManager.LockType.shared),
            resourceManager.ResourceManagerLock(
                sc.STORAGE, dst_vol.sdUUID, resourceManager.LockType.shared),
            # Image lock for each volume, exclusive for the destination
            resourceManager.ResourceManagerLock(
                src_img_ns, src_vol.imgUUID, resourceManager.LockType.shared),
            resourceManager.ResourceManagerLock(
                dst_img_ns, dst_vol.imgUUID,
                resourceManager.LockType.exclusive),
            # Volume lease for the destination volume
            volume.VolumeLease(
                0, dst_vol.sdUUID, dst_vol.imgUUID, dst_vol.volUUID)
        ]
        return ret

    @permutations((
        ('file', 'raw', 'raw'),
        ('file', 'raw', 'cow'),
        ('file', 'cow', 'raw'),
        ('file', 'cow', 'cow'),
        ('block', 'raw', 'raw'),
        ('block', 'raw', 'cow'),
        ('block', 'cow', 'raw'),
        ('block', 'cow', 'cow'),
    ))
    def test_intra_domain_copy(self, env_type, src_fmt, dst_fmt):
        src_fmt = sc.name2type(src_fmt)
        dst_fmt = sc.name2type(dst_fmt)
        job_id = str(uuid.uuid4())

        with self.get_vols(env_type, src_fmt, dst_fmt) as (src_chain,
                                                           dst_chain):
            src_vol = src_chain[0]
            dst_vol = dst_chain[0]
            write_qemu_chain(src_chain)
            self.assertRaises(ChainVerificationError,
                              verify_qemu_chain, dst_chain)

            source = dict(endpoint_type='div', sd_id=src_vol.sdUUID,
                          img_id=src_vol.imgUUID, vol_id=src_vol.volUUID)
            dest = dict(endpoint_type='div', sd_id=dst_vol.sdUUID,
                        img_id=dst_vol.imgUUID, vol_id=dst_vol.volUUID)
            job = storage.sdm.api.copy_data.Job(job_id, 0, source, dest)

            job.run()
            wait_for_job(job)
            self.assertEqual(sorted(self.expected_locks(src_vol, dst_vol)),
                             sorted(guarded.context.locks))

            self.assertEqual(jobs.STATUS.DONE, job.status)
            self.assertEqual(100.0, job.progress)
            self.assertNotIn('error', job.info())
            verify_qemu_chain(dst_chain)
            self.assertEqual(sc.fmt2str(dst_fmt),
                             qemuimg.info(dst_vol.volumePath)['format'])

    @permutations((
        ('file', 'raw', 'raw', (0, 1)),
        ('file', 'raw', 'raw', (1, 0)),
        ('block', 'raw', 'raw', (0, 1)),
        ('block', 'raw', 'raw', (1, 0)),
    ))
    def test_volume_chain_copy(self, env_type, src_fmt, dst_fmt, copy_seq):
        src_fmt = sc.name2type(src_fmt)
        dst_fmt = sc.name2type(dst_fmt)
        nr_vols = len(copy_seq)
        with self.get_vols(env_type, src_fmt, dst_fmt,
                           chain_length=nr_vols) as (src_chain,
                                                     dst_chain):
            write_qemu_chain(src_chain)
            for index in copy_seq:
                job_id = str(uuid.uuid4())
                src_vol = src_chain[index]
                dst_vol = dst_chain[index]
                source = dict(endpoint_type='div', sd_id=src_vol.sdUUID,
                              img_id=src_vol.imgUUID, vol_id=src_vol.volUUID)
                dest = dict(endpoint_type='div', sd_id=dst_vol.sdUUID,
                            img_id=dst_vol.imgUUID, vol_id=dst_vol.volUUID)
                job = storage.sdm.api.copy_data.Job(job_id, 0, source, dest)
                job.run()
                wait_for_job(job)
                self.assertEqual(sorted(self.expected_locks(src_vol, dst_vol)),
                                 sorted(guarded.context.locks))
            verify_qemu_chain(dst_chain)

    def test_bad_vm_configuration_volume(self):
        """
        When copying a volume containing VM configuration information the
        volume format may be set incorrectly due to an old bug.  Check that the
        workaround we have in place allows the copy to proceed without error.
        """
        job_id = str(uuid.uuid4())
        vm_conf_size = workarounds.VM_CONF_SIZE_BLK * sc.BLOCK_SIZE
        vm_conf_data = "VM Configuration"

        with self.get_vols('file', sc.COW_FORMAT, sc.COW_FORMAT,
                           size=vm_conf_size) as (src_chain, dst_chain):
            src_vol = src_chain[0]
            dst_vol = dst_chain[0]

            # Corrupt the COW volume by writing raw data.  This simulates how
            # these "problem" volumes were created in the first place.
            with open(src_vol.getVolumePath(), "w") as f:
                f.write(vm_conf_data)

            source = dict(endpoint_type='div', sd_id=src_vol.sdUUID,
                          img_id=src_vol.imgUUID, vol_id=src_vol.volUUID)
            dest = dict(endpoint_type='div', sd_id=dst_vol.sdUUID,
                        img_id=dst_vol.imgUUID, vol_id=dst_vol.volUUID)
            job = storage.sdm.api.copy_data.Job(job_id, 0, source, dest)
            job.run()
            wait_for_job(job)
            self.assertEqual(jobs.STATUS.DONE, job.status)

            # Verify that the copy succeeded
            with open(dst_vol.getVolumePath(), "r") as f:
                # Qemu pads the file to a 1k boundary with null bytes
                self.assertTrue(f.read().startswith(vm_conf_data))

    # TODO: Missing tests:
    # Copy between 2 different domains
    # Abort before copy
    # Abort during copy
