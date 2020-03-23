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

import threading
import uuid

from contextlib import contextmanager

import pytest

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
    verify_qemu_chain,
    write_qemu_chain,
)

from . import qemuio

from testValidation import broken_on_ci
from testlib import make_uuid
from testlib import VdsmTestCase, expandPermutations, permutations
from testlib import start_thread

from vdsm import jobs
from vdsm.common import exception
from vdsm.common.units import MiB, GiB
from vdsm.storage import blockVolume
from vdsm.storage import constants as sc
from vdsm.storage import exception as se
from vdsm.storage import guarded
from vdsm.storage import qemuimg
from vdsm.storage import resourceManager as rm
from vdsm.storage import volume
from vdsm.storage import workarounds
from vdsm.storage.sdm.api import copy_data

DEFAULT_SIZE = MiB

xfail_block = pytest.mark.xfail(
    reason="Doesn't work when -n flag is used by qemu-img")


@expandPermutations
class TestCopyDataDIV(VdsmTestCase):

    def setUp(self):
        self.scheduler = FakeScheduler()
        self.notifier = FakeNotifier()
        jobs.start(self.scheduler, self.notifier)

    def tearDown(self):
        jobs._clear()

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
        with make_env(env_type, src_fmt, dst_fmt,
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
                job = copy_data.Job(job_id, 0, source, dest)
                job.run()
                self.assertEqual(sorted(expected_locks(src_vol, dst_vol)),
                                 sorted(guarded.context.locks))
            verify_qemu_chain(env.dst_chain)

    def test_preallocated_file_volume_copy(self):
        job_id = make_uuid()

        with make_env('file', sc.RAW_FORMAT, sc.RAW_FORMAT,
                      prealloc=sc.PREALLOCATED_VOL) as env:
            write_qemu_chain(env.src_chain)
            src_vol = env.src_chain[0]
            dst_vol = env.dst_chain[0]

            source = dict(endpoint_type='div', sd_id=src_vol.sdUUID,
                          img_id=src_vol.imgUUID, vol_id=src_vol.volUUID)
            dest = dict(endpoint_type='div', sd_id=dst_vol.sdUUID,
                        img_id=dst_vol.imgUUID, vol_id=dst_vol.volUUID)
            job = copy_data.Job(job_id, 0, source, dest)
            job.run()
            self.assertEqual(
                qemuimg.info(dst_vol.volumePath)['virtualsize'],
                qemuimg.info(dst_vol.volumePath)['actualsize'])

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

        with make_env(env_type, src_fmt, dst_fmt, sd_version=sd_version,
                      src_qcow2_compat=qcow2_compat) as env:
            src_vol = env.src_chain[0]
            dst_vol = env.dst_chain[0]
            source = dict(endpoint_type='div', sd_id=src_vol.sdUUID,
                          img_id=src_vol.imgUUID, vol_id=src_vol.volUUID)
            dest = dict(endpoint_type='div', sd_id=dst_vol.sdUUID,
                        img_id=dst_vol.imgUUID, vol_id=dst_vol.volUUID)
            job = copy_data.Job(job_id, 0, source, dest)

            job.run()

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

        vm_conf_data = "VM Configuration".ljust(512)

        with make_env('file', sc.COW_FORMAT, sc.COW_FORMAT,
                      size=workarounds.VM_CONF_SIZE) as env:
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
            job = copy_data.Job(job_id, 0, source, dest)
            job.run()
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
        with make_env(env_type, fmt, fmt) as env:
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
                job = copy_data.Job(job_id, 0, source, dest)
                job.run()

            self.assertEqual(final_status, job.status)
            self.assertEqual(final_legality, dst_vol.getLegality())
            self.assertEqual(final_gen, dst_vol.getMetaParam(sc.GENERATION))

    @broken_on_ci("depends on slave's storage operation time")
    @permutations((('file',), ('block',)))
    def test_abort_during_copy(self, env_type):
        fmt = sc.RAW_FORMAT
        with make_env(env_type, fmt, fmt) as env:
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
                job = copy_data.Job(job_id, 0, source, dest)
                t = start_thread(job.run)
                if not fake_convert.ready_event.wait(1):
                    raise RuntimeError("Timeout waiting for thread")
                job.abort()
                t.join(1)
                if t.is_alive():
                    raise RuntimeError("Timeout waiting for thread")
                self.assertEqual(jobs.STATUS.ABORTED, job.status)
                self.assertEqual(sc.ILLEGAL_VOL, dst_vol.getLegality())
                self.assertEqual(gen_id, dst_vol.getMetaParam(sc.GENERATION))

    def test_wrong_generation(self):
        fmt = sc.RAW_FORMAT
        with make_env('block', fmt, fmt) as env:
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
            job = copy_data.Job(job_id, 0, source, dest)
            job.run()
            self.assertEqual(jobs.STATUS.FAILED, job.status)
            self.assertEqual(se.GenerationMismatch.code, job.error.code)
            self.assertEqual(sc.LEGAL_VOL, dst_vol.getLegality())
            self.assertEqual(generation, dst_vol.getMetaParam(sc.GENERATION))

    # TODO: Missing tests:
    # Copy between 2 different domains


@pytest.mark.parametrize("env_type,src_fmt,dst_fmt", [
    pytest.param('file', 'raw', 'raw'),
    pytest.param('file', 'raw', 'cow'),
    pytest.param('file', 'cow', 'raw'),
    pytest.param('file', 'cow', 'cow'),
    pytest.param('block', 'raw', 'raw'),
    pytest.param('block', 'raw', 'cow', marks=xfail_block),
    pytest.param('block', 'cow', 'raw'),
    pytest.param('block', 'cow', 'cow'),
])
def test_intra_domain_copy(env_type, src_fmt, dst_fmt):
    src_fmt = sc.name2type(src_fmt)
    dst_fmt = sc.name2type(dst_fmt)
    job_id = make_uuid()

    with make_env(env_type, src_fmt, dst_fmt) as env:
        src_vol = env.src_chain[0]
        dst_vol = env.dst_chain[0]
        write_qemu_chain(env.src_chain)
        with pytest.raises(qemuio.VerificationError):
            verify_qemu_chain(env.dst_chain)

        source = dict(endpoint_type='div', sd_id=src_vol.sdUUID,
                      img_id=src_vol.imgUUID, vol_id=src_vol.volUUID)
        dest = dict(endpoint_type='div', sd_id=dst_vol.sdUUID,
                    img_id=dst_vol.imgUUID, vol_id=dst_vol.volUUID)
        job = copy_data.Job(job_id, 0, source, dest)

        job.run()
        assert (sorted(expected_locks(src_vol, dst_vol)) ==
                sorted(guarded.context.locks))

        assert jobs.STATUS.DONE == job.status
        assert 100.0 == job.progress
        assert 'error' not in job.info()
        verify_qemu_chain(env.dst_chain)
        assert (sc.fmt2str(dst_fmt) == qemuimg.info(
            dst_vol.volumePath)['format'])


@pytest.mark.parametrize(
    "dest_format", [sc.COW_FORMAT, sc.RAW_FORMAT])
def test_copy_data_collapse(
        tmpdir, tmp_repo, fake_access, fake_rescan,
        tmp_db, fake_task, fake_scheduler, monkeypatch,
        dest_format):
    dom = tmp_repo.create_localfs_domain(
        name="domain",
        version=5)

    chain_size = 3
    volumes = create_chain(dom, chain_size)
    dest_img_id = str(uuid.uuid4())
    dest_vol_id = str(uuid.uuid4())

    length = MiB

    # Write some data to each layer
    for i, vol in enumerate(volumes):
        qemuio.write_pattern(
            vol.getVolumePath(),
            sc.fmt2str(vol.getFormat()),
            offset=(i * length))

    # The last volume in the chain is the leaf
    source_leaf_vol = volumes[-1]
    dest_vol = create_volume(
        dom,
        dest_img_id,
        dest_vol_id,
        dest_format)

    source = dict(
        endpoint_type='div',
        sd_id=source_leaf_vol.sdUUID,
        img_id=source_leaf_vol.imgUUID,
        vol_id=source_leaf_vol.volUUID)
    dest = dict(
        endpoint_type='div',
        sd_id=source_leaf_vol.sdUUID,
        img_id=dest_img_id,
        vol_id=dest_vol_id)

    # Run copy_data from the source chain to dest_vol, essentially
    # executing qemu-img convert
    job = copy_data.Job(str(uuid.uuid4()), 0, source, dest)
    monkeypatch.setattr(guarded, 'context', fake_guarded_context())
    job.run()

    # verify the data written to the source chain is available on the
    # collapsed target volume
    for i in range(chain_size):
        qemuio.verify_pattern(dest_vol.getVolumePath(),
                              sc.fmt2str(dest_vol.getFormat()),
                              offset=(i * length))


def create_volume(
        dom, imgUUID, volUUID, srcImgUUID=sc.BLANK_UUID,
        srcVolUUID=sc.BLANK_UUID, volFormat=sc.COW_FORMAT,
        capacity=GiB):
    dom.createVolume(
        imgUUID=imgUUID,
        capacity=capacity,
        volFormat=volFormat,
        preallocate=sc.SPARSE_VOL,
        diskType='DATA',
        volUUID=volUUID,
        desc="test_volume",
        srcImgUUID=srcImgUUID,
        srcVolUUID=srcVolUUID)

    return dom.produceVolume(imgUUID, volUUID)


def create_chain(dom, chain_size=2):
    volumes = []
    img_id = str(uuid.uuid4())
    parent_vol_id = sc.BLANK_UUID
    vol_format = sc.RAW_FORMAT

    for vol_index in range(chain_size):
        vol_id = str(uuid.uuid4())
        vol = create_volume(
            dom,
            img_id,
            vol_id,
            img_id,
            parent_vol_id,
            vol_format)
        volumes.append(vol)
        vol_format = sc.COW_FORMAT
        parent_vol_id = vol_id

    return volumes


@contextmanager
def make_env(storage_type, src_fmt, dst_fmt, chain_length=1,
             size=DEFAULT_SIZE, sd_version=3,
             src_qcow2_compat='0.10', prealloc=sc.SPARSE_VOL):
    with fake_env(storage_type, sd_version=sd_version) as env:
        rm = FakeResourceManager()
        with MonkeyPatchScope([
            (guarded, 'context', fake_guarded_context()),
            (copy_data, 'sdCache', env.sdcache),
            (blockVolume, 'rm', rm),
        ]):
            # Create existing volume - may use compat 0.10 or 1.1.
            src_vols = make_qemu_chain(env, size, src_fmt, chain_length,
                                       qcow2_compat=src_qcow2_compat,
                                       prealloc=prealloc)
            # New volumes are always created using the domain
            # prefered format.
            sd_compat = env.sd_manifest.qcow2_compat()
            dst_vols = make_qemu_chain(env, size, dst_fmt, chain_length,
                                       qcow2_compat=sd_compat,
                                       prealloc=prealloc)
            env.src_chain = src_vols
            env.dst_chain = dst_vols
            yield env


def expected_locks(src_vol, dst_vol):
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

    def run(self):
        self.ready_event.set()
        if self.error:
            raise self.error()
        if self.wait_for_abort:
            if not self.abort_event.wait(1):
                raise RuntimeError("Timeout waiting to finish, broken test?")
            # We must raise here like the real class so the calling code knows
            # the "command" was interrupted.
            raise exception.ActionStopped()
