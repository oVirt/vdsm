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

import threading
from contextlib import contextmanager

from fakelib import FakeNotifier
from fakelib import FakeScheduler
from monkeypatch import MonkeyPatchScope
from storagefakelib import FakeResourceManager
from storagefakelib import fake_guarded_context
from storagetestlib import fake_env
from storagetestlib import make_qemu_chain, write_qemu_chain, verify_qemu_chain
from storagetestlib import ChainVerificationError
from testValidation import broken_on_ci
from testlib import make_uuid
from testlib import VdsmTestCase, expandPermutations, permutations
from testlib import start_thread
from testlib import wait_for_job

from vdsm import jobs
from vdsm import qemuimg
from vdsm.common import exception
from vdsm.storage import constants as sc
from vdsm.storage import exception as se
from vdsm.storage import guarded
from vdsm.storage import resourceManager as rm
from vdsm.storage import workarounds

from storage import blockVolume, volume

import storage.sdm.api.copy_data


@expandPermutations
class TestCopyDataDIV(VdsmTestCase):
    DEFAULT_SIZE = 1048576

    def setUp(self):
        self.scheduler = FakeScheduler()
        self.notifier = FakeNotifier()
        jobs.start(self.scheduler, self.notifier)

    def tearDown(self):
        jobs._clear()

    @contextmanager
    def make_env(self, storage_type, src_fmt, dst_fmt, chain_length=1,
                 size=DEFAULT_SIZE, sd_version=3, src_qcow2_compat='0.10'):
        with fake_env(storage_type, sd_version=sd_version) as env:
            rm = FakeResourceManager()
            with MonkeyPatchScope([
                (guarded, 'context', fake_guarded_context()),
                (storage.sdm.api.copy_data, 'sdCache', env.sdcache),
                (blockVolume, 'rm', rm),
            ]):
                # Create existing volume - may use compat 0.10 or 1.1.
                src_vols = make_qemu_chain(env, size, src_fmt, chain_length,
                                           qcow2_compat=src_qcow2_compat)
                # New volumes are always created using the domain
                # prefered format.
                sd_compat = env.sd_manifest.qcow2_compat()
                dst_vols = make_qemu_chain(env, size, dst_fmt, chain_length,
                                           qcow2_compat=sd_compat)
                env.src_chain = src_vols
                env.dst_chain = dst_vols
                yield env

    def expected_locks(self, src_vol, dst_vol):
        src_img_ns = rm.getNamespace(sc.IMAGE_NAMESPACE, src_vol.sdUUID)
        dst_img_ns = rm.getNamespace(sc.IMAGE_NAMESPACE, dst_vol.sdUUID)
        ret = [
            # Domain lock for each volume
            rm.ResourceManagerLock(sc.STORAGE, src_vol.sdUUID, rm.SHARED),
            rm.ResourceManagerLock(sc.STORAGE, dst_vol.sdUUID, rm.SHARED),
            # Image lock for each volume, exclusive for the destination
            rm.ResourceManagerLock(src_img_ns, src_vol.imgUUID, rm.SHARED),
            rm.ResourceManagerLock(dst_img_ns, dst_vol.imgUUID, rm.EXCLUSIVE),
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
        job_id = make_uuid()

        with self.make_env(env_type, src_fmt, dst_fmt) as env:
            src_vol = env.src_chain[0]
            dst_vol = env.dst_chain[0]
            write_qemu_chain(env.src_chain)
            self.assertRaises(ChainVerificationError,
                              verify_qemu_chain, env.dst_chain)

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
            verify_qemu_chain(env.dst_chain)
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
        with self.make_env(env_type, src_fmt, dst_fmt,
                           chain_length=nr_vols) as env:
            write_qemu_chain(env.src_chain)
            for index in copy_seq:
                job_id = make_uuid()
                src_vol = env.src_chain[index]
                dst_vol = env.dst_chain[index]
                source = dict(endpoint_type='div', sd_id=src_vol.sdUUID,
                              img_id=src_vol.imgUUID, vol_id=src_vol.volUUID)
                dest = dict(endpoint_type='div', sd_id=dst_vol.sdUUID,
                            img_id=dst_vol.imgUUID, vol_id=dst_vol.volUUID)
                job = storage.sdm.api.copy_data.Job(job_id, 0, source, dest)
                job.run()
                wait_for_job(job)
                self.assertEqual(sorted(self.expected_locks(src_vol, dst_vol)),
                                 sorted(guarded.context.locks))
            verify_qemu_chain(env.dst_chain)

    @permutations((
        # env_type, src_compat, sd_version
        # Old storage domain, we supported only 0.10
        ('file', '0.10', 3),
        ('block', '0.10', 3),
        # New domain old volume
        ('file', '0.10', 4),
        ('block', '0.10', 4),
        # New domain, new volumes
        ('file', '1.1', 4),
        ('block', '1.1', 4),
    ))
    def test_qcow2_compat(self, env_type, qcow2_compat, sd_version):
        src_fmt = sc.name2type("cow")
        dst_fmt = sc.name2type("cow")
        job_id = make_uuid()

        with self.make_env(env_type, src_fmt, dst_fmt, sd_version=sd_version,
                           src_qcow2_compat=qcow2_compat) as env:
            src_vol = env.src_chain[0]
            dst_vol = env.dst_chain[0]
            source = dict(endpoint_type='div', sd_id=src_vol.sdUUID,
                          img_id=src_vol.imgUUID, vol_id=src_vol.volUUID)
            dest = dict(endpoint_type='div', sd_id=dst_vol.sdUUID,
                        img_id=dst_vol.imgUUID, vol_id=dst_vol.volUUID)
            job = storage.sdm.api.copy_data.Job(job_id, 0, source, dest)

            job.run()
            wait_for_job(job)

            actual_compat = qemuimg.info(dst_vol.volumePath)['compat']
            self.assertEqual(actual_compat, env.sd_manifest.qcow2_compat())

    # TODO: Missing tests:
    # We should a test of copying from old domain (version=3)
    # to a new domain (domain=4) or the opposite (from 4 to 3),
    # but we still don't have infrastracture for this yet.

    def test_bad_vm_configuration_volume(self):
        """
        When copying a volume containing VM configuration information the
        volume format may be set incorrectly due to an old bug.  Check that the
        workaround we have in place allows the copy to proceed without error.
        """
        job_id = make_uuid()
        vm_conf_size = workarounds.VM_CONF_SIZE_BLK * sc.BLOCK_SIZE
        vm_conf_data = "VM Configuration"

        with self.make_env('file', sc.COW_FORMAT, sc.COW_FORMAT,
                           size=vm_conf_size) as env:
            src_vol = env.src_chain[0]
            dst_vol = env.dst_chain[0]

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

    @permutations((
        ('file', None, sc.LEGAL_VOL, jobs.STATUS.DONE, 1),
        ('file', RuntimeError, sc.ILLEGAL_VOL, jobs.STATUS.FAILED, 0),
        ('block', None, sc.LEGAL_VOL, jobs.STATUS.DONE, 1),
        ('block', RuntimeError, sc.ILLEGAL_VOL, jobs.STATUS.FAILED, 0),
    ))
    def test_volume_operation(self, env_type, error,
                              final_legality, final_status, final_gen):
        job_id = make_uuid()
        fmt = sc.RAW_FORMAT
        with self.make_env(env_type, fmt, fmt) as env:
            src_vol = env.src_chain[0]
            dst_vol = env.dst_chain[0]

            self.assertEqual(sc.LEGAL_VOL, dst_vol.getLegality())
            source = dict(endpoint_type='div', sd_id=src_vol.sdUUID,
                          img_id=src_vol.imgUUID, vol_id=src_vol.volUUID,
                          generation=0)
            dest = dict(endpoint_type='div', sd_id=dst_vol.sdUUID,
                        img_id=dst_vol.imgUUID, vol_id=dst_vol.volUUID,
                        generation=0)

            fake_convert = FakeQemuConvertChecker(src_vol, dst_vol,
                                                  error=error)
            with MonkeyPatchScope([(qemuimg, 'convert', fake_convert)]):
                job = storage.sdm.api.copy_data.Job(job_id, 0, source, dest)
                job.run()

            self.assertEqual(final_status, job.status)
            self.assertEqual(final_legality, dst_vol.getLegality())
            self.assertEqual(final_gen, dst_vol.getMetaParam(sc.GENERATION))

    @broken_on_ci("depends on slave's storage operation time")
    @permutations((('file',), ('block',)))
    def test_abort_during_copy(self, env_type):
        fmt = sc.RAW_FORMAT
        with self.make_env(env_type, fmt, fmt) as env:
            src_vol = env.src_chain[0]
            dst_vol = env.dst_chain[0]
            gen_id = dst_vol.getMetaParam(sc.GENERATION)
            source = dict(endpoint_type='div', sd_id=src_vol.sdUUID,
                          img_id=src_vol.imgUUID, vol_id=src_vol.volUUID,
                          generation=0)
            dest = dict(endpoint_type='div', sd_id=dst_vol.sdUUID,
                        img_id=dst_vol.imgUUID, vol_id=dst_vol.volUUID,
                        generation=gen_id)
            fake_convert = FakeQemuConvertChecker(src_vol, dst_vol,
                                                  wait_for_abort=True)
            with MonkeyPatchScope([(qemuimg, 'convert', fake_convert)]):
                job_id = make_uuid()
                job = storage.sdm.api.copy_data.Job(job_id, 0, source, dest)
                t = start_thread(job.run)
                if not fake_convert.ready_event.wait(1):
                    raise RuntimeError("Timeout waiting for thread")
                job.abort()
                t.join(1)
                if t.isAlive():
                    raise RuntimeError("Timeout waiting for thread")
                self.assertEqual(jobs.STATUS.ABORTED, job.status)
                self.assertEqual(sc.ILLEGAL_VOL, dst_vol.getLegality())
                self.assertEqual(gen_id, dst_vol.getMetaParam(sc.GENERATION))

    def test_wrong_generation(self):
        fmt = sc.RAW_FORMAT
        with self.make_env('block', fmt, fmt) as env:
            src_vol = env.src_chain[0]
            dst_vol = env.dst_chain[0]
            generation = dst_vol.getMetaParam(sc.GENERATION)
            source = dict(endpoint_type='div', sd_id=src_vol.sdUUID,
                          img_id=src_vol.imgUUID, vol_id=src_vol.volUUID,
                          generation=0)
            dest = dict(endpoint_type='div', sd_id=dst_vol.sdUUID,
                        img_id=dst_vol.imgUUID, vol_id=dst_vol.volUUID,
                        generation=generation + 1)
            job_id = make_uuid()
            job = storage.sdm.api.copy_data.Job(job_id, 0, source, dest)
            job.run()
            self.assertEqual(jobs.STATUS.FAILED, job.status)
            self.assertEqual(se.GenerationMismatch.code, job.error.code)
            self.assertEqual(sc.LEGAL_VOL, dst_vol.getLegality())
            self.assertEqual(generation, dst_vol.getMetaParam(sc.GENERATION))

    # TODO: Missing tests:
    # Copy between 2 different domains


class FakeQemuConvertChecker(object):
    def __init__(self, src_vol, dst_vol, error=None, wait_for_abort=False):
        self.src_vol = src_vol
        self.dst_vol = dst_vol
        self.error = error
        self.wait_for_abort = wait_for_abort
        self.ready_event = threading.Event()

    def __call__(self, *args, **kwargs):
        assert sc.LEGAL_VOL == self.src_vol.getLegality()
        assert sc.ILLEGAL_VOL == self.dst_vol.getLegality()
        return FakeQemuImgOperation(self.ready_event, self.wait_for_abort,
                                    self.error)


class FakeQemuImgOperation(object):
    def __init__(self, ready_event, wait_for_abort, error):
        self.ready_event = ready_event
        self.wait_for_abort = wait_for_abort
        self.error = error
        self.abort_event = threading.Event()

    def abort(self):
        self.abort_event.set()

    def wait_for_completion(self):
        self.ready_event.set()
        if self.error:
            raise self.error()
        if self.wait_for_abort:
            if not self.abort_event.wait(1):
                raise RuntimeError("Timeout waiting to finish, broken test?")
            # We must raise here like the real class so the calling code knows
            # the "command" was interrupted.
            raise exception.ActionStopped()

    def close(self):
        pass
