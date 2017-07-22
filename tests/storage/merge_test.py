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
from collections import namedtuple
from functools import partial

from monkeypatch import MonkeyPatchScope

from storage.storagefakelib import (
    FakeResourceManager,
    fake_guarded_context,
)

from storage.storagetestlib import (
    FakeVolume,
    fake_env,
    make_qemu_chain,
    qemu_pattern_verify,
    qemu_pattern_write,
)

from testValidation import brokentest
from testlib import make_uuid
from testlib import expandPermutations, permutations
from testlib import VdsmTestCase

from vdsm import cmdutils
from vdsm.storage import blockVolume
from vdsm.storage import constants as sc
from vdsm.storage import exception as se
from vdsm.storage import fileVolume
from vdsm.storage import guarded
from vdsm.storage import image
from vdsm.storage import merge
from vdsm.storage import qemuimg
from vdsm.storage import resourceManager as rm
from vdsm.storage import volume

MB = 1024 ** 2
GB = 1024 ** 3


# XXX: Ideally we wouldn't fake these methods but the originals are defined in
# the Volume class and use SPM rollbacks so we cannot use them.
def fake_blockVolume_extendSize(env, vol_instance, new_size_blk):
    new_size = new_size_blk * sc.BLOCK_SIZE
    new_size_mb = (new_size + MB - 1) / MB
    env.lvm.extendLV(env.sd_manifest.sdUUID, vol_instance.volUUID, new_size_mb)
    vol_instance.setSize(new_size_blk)


def fake_fileVolume_extendSize(env, vol_instance, new_size_blk):
    new_size = new_size_blk * sc.BLOCK_SIZE
    vol_path = vol_instance.getVolumePath()
    env.sd_manifest.oop.truncateFile(vol_path, new_size)
    vol_instance.setSize(new_size_blk)


Volume = namedtuple("Volume", "format,virtual,physical")
Expected = namedtuple("Expected", "virtual,physical")


@contextmanager
def make_env(env_type, base, top):
    img_id = make_uuid()
    base_id = make_uuid()
    top_id = make_uuid()

    if env_type == 'block' and base.format == 'raw':
        prealloc = sc.PREALLOCATED_VOL
    else:
        prealloc = sc.SPARSE_VOL

    with fake_env(env_type) as env:
        env.make_volume(base.virtual * GB, img_id, base_id,
                        vol_format=sc.name2type(base.format),
                        prealloc=prealloc)
        env.make_volume(top.virtual * GB, img_id, top_id,
                        parent_vol_id=base_id,
                        vol_format=sc.COW_FORMAT)
        env.subchain = merge.SubchainInfo(
            dict(sd_id=env.sd_manifest.sdUUID, img_id=img_id,
                 base_id=base_id, top_id=top_id), 0)

        if env_type == 'block':
            # Simulate allocation by adjusting the LV sizes
            env.lvm.extendLV(env.sd_manifest.sdUUID, base_id,
                             base.physical * GB / MB)
            env.lvm.extendLV(env.sd_manifest.sdUUID, top_id,
                             top.physical * GB / MB)

        rm = FakeResourceManager()
        with MonkeyPatchScope([
            (guarded, 'context', fake_guarded_context()),
            (merge, 'sdCache', env.sdcache),
            (blockVolume, 'rm', rm),
            (blockVolume, 'sdCache', env.sdcache),
            (image.Image, 'getChain', lambda self, sdUUID, imgUUID:
                [env.subchain.base_vol, env.subchain.top_vol]),
            (blockVolume.BlockVolume, 'extendSize',
                partial(fake_blockVolume_extendSize, env)),
            (fileVolume.FileVolume, 'extendSize',
                partial(fake_fileVolume_extendSize, env)),
        ]):
            yield env


class FakeImage(object):

    def __init__(self, repoPath):
        pass


@expandPermutations
class TestSubchainInfo(VdsmTestCase):

    # TODO: use one make_env for all tests?
    @contextmanager
    def make_env(self, sd_type='file', format='raw', chain_len=2,
                 shared=False):
        size = 1048576
        base_fmt = sc.name2type(format)
        with fake_env(sd_type) as env:
            rm = FakeResourceManager()
            with MonkeyPatchScope([
                (guarded, 'context', fake_guarded_context()),
                (merge, 'sdCache', env.sdcache),
                (blockVolume, 'rm', rm),
            ]):
                env.chain = make_qemu_chain(env, size, base_fmt, chain_len)

                def fake_chain(self, sdUUID, imgUUID, volUUID=None):
                    return env.chain

                image.Image.getChain = fake_chain

                yield env

    def test_legal_chain(self):
        with self.make_env() as env:
            base_vol = env.chain[0]
            top_vol = env.chain[1]
            subchain_info = dict(sd_id=base_vol.sdUUID,
                                 img_id=base_vol.imgUUID,
                                 base_id=base_vol.volUUID,
                                 top_id=top_vol.volUUID,
                                 base_generation=0)

            subchain = merge.SubchainInfo(subchain_info, 0)
            # Next subchain.validate() should pass without exceptions
            subchain.validate()

    def test_validate_base_is_not_in_chain(self):
        with self.make_env() as env:
            top_vol = env.chain[1]
            subchain_info = dict(sd_id=top_vol.sdUUID,
                                 img_id=top_vol.imgUUID,
                                 base_id=make_uuid(),
                                 top_id=top_vol.volUUID,
                                 base_generation=0)

            subchain = merge.SubchainInfo(subchain_info, 0)
            self.assertRaises(se.VolumeIsNotInChain, subchain.validate)

    def test_validate_top_is_not_in_chain(self):
        with self.make_env() as env:
            base_vol = env.chain[0]
            subchain_info = dict(sd_id=base_vol.sdUUID,
                                 img_id=base_vol.imgUUID,
                                 base_id=base_vol.volUUID,
                                 top_id=make_uuid(),
                                 base_generation=0)

            subchain = merge.SubchainInfo(subchain_info, 0)
            self.assertRaises(se.VolumeIsNotInChain, subchain.validate)

    def test_validate_vol_is_not_base_parent(self):
        with self.make_env(chain_len=3) as env:
            base_vol = env.chain[0]
            top_vol = env.chain[2]
            subchain_info = dict(sd_id=top_vol.sdUUID,
                                 img_id=top_vol.imgUUID,
                                 base_id=base_vol.volUUID,
                                 top_id=top_vol.volUUID,
                                 base_generation=0)

            subchain = merge.SubchainInfo(subchain_info, 0)
            self.assertRaises(se.WrongParentVolume, subchain.validate)

    @permutations((
        # shared volume
        (0,),
        (1,),
    ))
    def test_validate_vol_is_not_shared(self, shared_vol):
        with self.make_env(chain_len=3, shared=True) as env:
            base_vol = env.chain[0]
            top_vol = env.chain[1]
            env.chain[shared_vol].setShared()
            subchain_info = dict(sd_id=top_vol.sdUUID,
                                 img_id=top_vol.imgUUID,
                                 base_id=base_vol.volUUID,
                                 top_id=top_vol.volUUID,
                                 base_generation=0)

            subchain = merge.SubchainInfo(subchain_info, 0)
            self.assertRaises(se.SharedVolumeNonWritable, subchain.validate)


@expandPermutations
class TestPrepareMerge(VdsmTestCase):

    @permutations((
        # No capacity update, no allocation update
        (Volume('raw', 1, 1), Volume('cow', 1, 1), Expected(1, 1)),
        # No capacity update, increase LV size
        (Volume('cow', 10, 2), Volume('cow', 10, 2), Expected(10, 5)),
        # Update capacity and increase LV size
        (Volume('cow', 3, 1), Volume('cow', 5, 1), Expected(5, 3)),
    ))
    def test_block_cow(self, base, top, expected):
        with make_env('block', base, top) as env:
            merge.prepare(env.subchain)
            self.assertEqual(sorted(self.expected_locks(env.subchain)),
                             sorted(guarded.context.locks))
            base_vol = env.subchain.base_vol
            self.assertEqual(sc.LEGAL_VOL, base_vol.getLegality())
            new_base_size = base_vol.getSize() * sc.BLOCK_SIZE
            new_base_alloc = env.sd_manifest.getVSize(base_vol.imgUUID,
                                                      base_vol.volUUID)
            self.assertEqual(expected.virtual * GB, new_base_size)
            self.assertEqual(expected.physical * GB, new_base_alloc)

    @brokentest("Looks like it is impossible to create a domain object in "
                "the tests")
    @permutations((
        # Update capacity and fully allocate LV
        (Volume('raw', 1, 1), Volume('cow', 2, 1), Expected(2, 2)),
    ))
    def test_block_raw(self, base, top, expected):
        with make_env('block', base, top) as env:
            merge.prepare(env.subchain)
            self.assertEqual(sorted(self.expected_locks(env.subchain)),
                             sorted(guarded.context.locks))
            base_vol = env.subchain.base_vol
            self.assertEqual(sc.LEGAL_VOL, base_vol.getLegality())
            new_base_size = base_vol.getSize() * sc.BLOCK_SIZE
            new_base_alloc = env.sd_manifest.getVSize(base_vol.imgUUID,
                                                      base_vol.volUUID)
            self.assertEqual(expected.virtual * GB, new_base_size)
            self.assertEqual(expected.physical * GB, new_base_alloc)

    @permutations((
        (Volume('cow', 1, 0), Volume('cow', 1, 0), Expected(1, 0)),
        (Volume('cow', 1, 0), Volume('cow', 2, 0), Expected(2, 0)),
    ))
    def test_file_cow(self, base, top, expected):
        with make_env('file', base, top) as env:
            merge.prepare(env.subchain)
            base_vol = env.subchain.base_vol
            self.assertEqual(sc.LEGAL_VOL, base_vol.getLegality())
            new_base_size = base_vol.getSize() * sc.BLOCK_SIZE
            self.assertEqual(expected.virtual * GB, new_base_size)

    @brokentest("Looks like it is impossible to create a domain object in "
                "the tests")
    @permutations((
        (Volume('raw', 1, 0), Volume('cow', 2, 0), Expected(2, 0)),
    ))
    def test_file_raw(self, base, top, expected):
        with make_env('file', base, top) as env:
            merge.prepare(env.subchain)
            base_vol = env.subchain.base_vol
            self.assertEqual(sc.LEGAL_VOL, base_vol.getLegality())
            new_base_size = base_vol.getSize() * sc.BLOCK_SIZE
            self.assertEqual(expected.virtual * GB, new_base_size)

    def expected_locks(self, subchain):
        img_ns = rm.getNamespace(sc.IMAGE_NAMESPACE, subchain.sd_id)
        return [
            rm.ResourceManagerLock(sc.STORAGE, subchain.sd_id, rm.SHARED),
            rm.ResourceManagerLock(img_ns, subchain.img_id, rm.EXCLUSIVE),
            volume.VolumeLease(subchain.host_id, subchain.sd_id,
                               subchain.img_id, subchain.base_id)
        ]

    # TODO: Once BZ 1411103 is fixed, add unit tests for preparing the chain
    # required for prepare step:
    # 1. Test a chain of 2 volumes
    # 2. Chain that has a shared volume (to simulate cloning a VM from a
    #    template)


class FakeSyncVolumeChain(object):

    def __call__(self, sd_id, img_id, vol_id, actual_chain):
        self.sd_id = sd_id
        self.img_id = img_id
        self.vol_id = vol_id
        self.actual_chain = actual_chain


@expandPermutations
class TestFinalizeMerge(VdsmTestCase):

    # TODO: use one make_env for all tests?
    @contextmanager
    def make_env(self, sd_type='block', format='raw', chain_len=2):
        size = 1048576
        base_fmt = sc.name2type(format)
        with fake_env(sd_type) as env:
            rm = FakeResourceManager()
            with MonkeyPatchScope([
                (guarded, 'context', fake_guarded_context()),
                (merge, 'sdCache', env.sdcache),
                (blockVolume, 'rm', rm),
                (image, 'Image', FakeImage),
            ]):
                env.chain = make_qemu_chain(env, size, base_fmt, chain_len)

                volumes = {(vol.imgUUID, vol.volUUID): FakeVolume()
                           for vol in env.chain}
                env.sdcache.domains[env.sd_manifest.sdUUID].volumes = volumes

                def fake_chain(self, sdUUID, imgUUID, volUUID=None):
                    return env.chain

                image.Image.getChain = fake_chain
                image.Image.syncVolumeChain = FakeSyncVolumeChain()

                yield env

    # TODO: Once BZ 1411103 is fixed, add unit tests for preparing the chain
    # required for finalize step:
    # 1. Test a chain of 2 volumes
    # 2. Test a c hain of more than 2 volumes and verify that top's parent is
    #    prepared

    @permutations([
        # sd_type, chain_len, base_index, top_index
        ('file', 2, 0, 1),
        ('block', 2, 0, 1),
        ('file', 4, 1, 2),
        ('block', 4, 1, 2),
    ])
    def test_finalize(self, sd_type, chain_len, base_index, top_index):
        with self.make_env(sd_type=sd_type, chain_len=chain_len) as env:
            base_vol = env.chain[base_index]
            top_vol = env.chain[top_index]
            subchain_info = dict(sd_id=base_vol.sdUUID,
                                 img_id=base_vol.imgUUID,
                                 base_id=base_vol.volUUID,
                                 top_id=top_vol.volUUID,
                                 base_generation=0)
            subchain = merge.SubchainInfo(subchain_info, 0)

            merge.finalize(subchain)

            # If top has a child, the child must now be rebased on base.
            if top_vol is not env.chain[-1]:
                child_vol = env.chain[top_index + 1]
                info = qemuimg.info(child_vol.volumePath)
                self.assertEqual(info['backingfile'],
                                 volume.getBackingVolumePath(subchain.img_id,
                                                             subchain.base_id))

            # verify syncVolumeChain arguments
            self.check_sync_volume_chain(subchain, env.chain[-1].volUUID)
            new_chain = [vol.volUUID for vol in env.chain]
            new_chain.remove(top_vol.volUUID)
            self.assertEqual(image.Image.syncVolumeChain.actual_chain,
                             new_chain)

            self.assertEqual(base_vol.getLegality(), sc.LEGAL_VOL)

    @permutations([
        # volume
        ('base',),
        ('top',),
    ])
    def test_finalize_illegal_volume(self, volume):
        with self.make_env(sd_type='block', format='cow', chain_len=4) as env:
            base_vol = env.chain[1]
            top_vol = env.chain[2]
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

            with self.assertRaises(se.prepareIllegalVolumeError):
                merge.finalize(subchain)

    def test_qemuimg_rebase_failed(self):
        with self.make_env(sd_type='file', chain_len=4) as env:
            base_vol = env.chain[1]
            top_vol = env.chain[2]
            subchain_info = dict(sd_id=base_vol.sdUUID,
                                 img_id=base_vol.imgUUID,
                                 base_id=base_vol.volUUID,
                                 top_id=top_vol.volUUID,
                                 base_generation=0)
            subchain = merge.SubchainInfo(subchain_info, 0)

            with MonkeyPatchScope([
                (qemuimg._qemuimg, '_cmd', '/usr/bin/false')
            ]):

                with self.assertRaises(cmdutils.Error):
                    merge.finalize(subchain)

            self.assertEqual(subchain.top_vol.getLegality(), sc.LEGAL_VOL)
            self.assertEqual(subchain.top_vol.getParent(), base_vol.volUUID)

    def test_rollback_volume_legallity_failed(self):
        with self.make_env(sd_type='block', chain_len=4) as env:
            base_vol = env.chain[1]
            top_vol = env.chain[2]
            subchain_info = dict(sd_id=base_vol.sdUUID,
                                 img_id=base_vol.imgUUID,
                                 base_id=base_vol.volUUID,
                                 top_id=top_vol.volUUID,
                                 base_generation=0)
            subchain = merge.SubchainInfo(subchain_info, 0)

            def setLegality(self, legality):
                if legality == sc.LEGAL_VOL:
                    raise RuntimeError("Rollback volume legality failed")
                self.setMetaParam(sc.LEGALITY, legality)

            with MonkeyPatchScope([
                (qemuimg._qemuimg, '_cmd', '/usr/bin/false'),
                (volume.VolumeManifest, 'setLegality', setLegality),
            ]):
                with self.assertRaises(cmdutils.Error):
                    merge.finalize(subchain)

            self.assertEqual(subchain.top_vol.getLegality(), sc.ILLEGAL_VOL)

    def test_reduce_chunked(self):
        with self.make_env(sd_type='block', format='cow', chain_len=4) as env:
            base_vol = env.chain[1]
            top_vol = env.chain[2]
            subchain_info = dict(sd_id=base_vol.sdUUID,
                                 img_id=base_vol.imgUUID,
                                 base_id=base_vol.volUUID,
                                 top_id=top_vol.volUUID,
                                 base_generation=0)
            subchain = merge.SubchainInfo(subchain_info, 0)

            merge.finalize(subchain)

            fake_sd = env.sdcache.domains[env.sd_manifest.sdUUID]
            fake_base_vol = fake_sd.produceVolume(subchain.img_id,
                                                  subchain.base_id)

            self.assertEqual(len(fake_base_vol.__calls__), 1)
            optimal_size = base_vol.optimal_size() // sc.BLOCK_SIZE
            self.assertEqual(fake_base_vol.__calls__[0],
                             ('reduce', (optimal_size,), {}))

    def test_reduce_not_chunked(self):
        with self.make_env(sd_type='file', format='cow', chain_len=4) as env:
            base_vol = env.chain[1]
            top_vol = env.chain[2]
            subchain_info = dict(sd_id=base_vol.sdUUID,
                                 img_id=base_vol.imgUUID,
                                 base_id=base_vol.volUUID,
                                 top_id=top_vol.volUUID,
                                 base_generation=0)
            subchain = merge.SubchainInfo(subchain_info, 0)

            merge.finalize(subchain)

            fake_sd = env.sdcache.domains[env.sd_manifest.sdUUID]
            fake_base_vol = fake_sd.produceVolume(subchain.img_id,
                                                  subchain.base_id)

            calls = getattr(fake_base_vol, "__calls__", {})
            # Verify that 'calls' is empty which means that 'reduce' wasn't
            # called
            self.assertEqual(len(calls), 0)

    def test_reduce_failure(self):
        with self.make_env(sd_type='block', format='cow', chain_len=4) as env:
            base_vol = env.chain[0]
            top_vol = env.chain[1]
            subchain_info = dict(sd_id=base_vol.sdUUID,
                                 img_id=base_vol.imgUUID,
                                 base_id=base_vol.volUUID,
                                 top_id=top_vol.volUUID,
                                 base_generation=0)
            subchain = merge.SubchainInfo(subchain_info, 0)

            fake_sd = env.sdcache.domains[env.sd_manifest.sdUUID]
            fake_base_vol = fake_sd.produceVolume(subchain.img_id,
                                                  subchain.base_id)
            fake_base_vol.errors["reduce"] = se.LogicalVolumeExtendError(
                "vgname", "lvname", base_vol.optimal_size())

            with self.assertRaises(se.LogicalVolumeExtendError):
                merge.finalize(subchain)

            # verify syncVolumeChain arguments
            self.check_sync_volume_chain(subchain, env.chain[-1].volUUID)

    @permutations([
        # base_fmt
        ('raw',),
        ('cow',),
    ])
    def test_chain_after_finalize(self, base_fmt):
        with self.make_env(format=base_fmt, chain_len=3) as env:
            base_vol = env.chain[0]
            # We write data to the base and will read it from the child volume
            # to verify that the chain is valid after qemu-rebase.
            offset = 0
            pattern = 0xf0
            length = 1024
            qemu_pattern_write(base_vol.volumePath,
                               sc.fmt2str(base_vol.getFormat()),
                               offset=offset,
                               len=length, pattern=pattern)

            top_vol = env.chain[1]
            child_vol = env.chain[2]

            subchain_info = dict(sd_id=base_vol.sdUUID,
                                 img_id=base_vol.imgUUID,
                                 base_id=base_vol.volUUID,
                                 top_id=top_vol.volUUID,
                                 base_generation=0)
            subchain = merge.SubchainInfo(subchain_info, 0)

            merge.finalize(subchain)

            qemu_pattern_verify(child_vol.volumePath,
                                sc.fmt2str(child_vol.getFormat()),
                                offset=offset,
                                len=length, pattern=pattern)

    def check_sync_volume_chain(self, subchain, removed_vol_id):
        sync = image.Image.syncVolumeChain
        self.assertEqual(sync.sd_id, subchain.sd_id)
        self.assertEqual(sync.img_id, subchain.img_id)
        self.assertEqual(sync.vol_id, removed_vol_id)
