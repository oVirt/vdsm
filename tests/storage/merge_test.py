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
from collections import namedtuple

import pytest

from _pytest.monkeypatch import MonkeyPatch

from storage.storagefakelib import (
    FakeResourceManager,
    fake_guarded_context,
)

from storage.storagetestlib import (
    FakeVolume,
    fake_env,
    make_qemu_chain,
)

from . import qemuio

from testlib import make_config
from testlib import make_uuid

from vdsm.common import cmdutils
from vdsm.common.units import KiB, MiB, GiB
from vdsm.storage import blockVolume
from vdsm.storage import constants as sc
from vdsm.storage import exception as se
from vdsm.storage import guarded
from vdsm.storage import image
from vdsm.storage import merge
from vdsm.storage import operation
from vdsm.storage import qemuimg
from vdsm.storage import resourceManager as rm
from vdsm.storage import volume

CONFIG = make_config([
    ('irs', 'volume_utilization_chunk_mb', '1024'),
    ('irs', 'volume_utilizzation_precent', '50'),
])

Volume = namedtuple("Volume", "format,virtual,physical,leaf")
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
        with MonkeyPatch().context() as mp:
            mp.setattr(guarded, 'context', fake_guarded_context())
            mp.setattr(merge, 'sdCache', env.sdcache)
            mp.setattr(blockVolume, "config", CONFIG)
            mp.setattr(blockVolume, 'rm', FakeResourceManager())
            mp.setattr(blockVolume, 'sdCache', env.sdcache)
            mp.setattr(
                image.Image, 'getChain',
                lambda self, sdUUID, imgUUID:
                    [env.subchain.base_vol, env.subchain.top_vol])

            env.make_volume(
                base.virtual * GiB, img_id, base_id,
                vol_format=sc.name2type(base.format),
                prealloc=prealloc,
                vol_type=sc.INTERNAL_VOL)

            env.make_volume(
                top.virtual * GiB, img_id, top_id,
                parent_vol_id=base_id,
                vol_format=sc.COW_FORMAT,
                vol_type=sc.LEAF_VOL if top.leaf else sc.INTERNAL_VOL)

            env.subchain = merge.SubchainInfo(
                dict(sd_id=env.sd_manifest.sdUUID, img_id=img_id,
                     base_id=base_id, top_id=top_id), 0)

            if env_type == 'block':
                # Simulate allocation by adjusting the LV sizes
                env.lvm.extendLV(env.sd_manifest.sdUUID, base_id,
                                 base.physical * GiB // MiB)
                env.lvm.extendLV(env.sd_manifest.sdUUID, top_id,
                                 top.physical * GiB // MiB)

            yield env


class FakeImage(object):

    def __init__(self, repoPath):
        pass


class TestSubchainInfo:

    # TODO: use one make_env for all tests?
    @contextmanager
    def make_env(self, sd_type='file', format='raw', chain_len=2,
                 shared=False):
        size = MiB
        base_fmt = sc.name2type(format)
        with fake_env(sd_type) as env:
            with MonkeyPatch().context() as mp:
                mp.setattr(guarded, 'context', fake_guarded_context())
                mp.setattr(merge, 'sdCache', env.sdcache)
                mp.setattr(blockVolume, 'rm', FakeResourceManager())

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
            with pytest.raises(se.VolumeIsNotInChain):
                subchain.validate()

    def test_validate_top_is_not_in_chain(self):
        with self.make_env() as env:
            base_vol = env.chain[0]
            subchain_info = dict(sd_id=base_vol.sdUUID,
                                 img_id=base_vol.imgUUID,
                                 base_id=base_vol.volUUID,
                                 top_id=make_uuid(),
                                 base_generation=0)

            subchain = merge.SubchainInfo(subchain_info, 0)
            with pytest.raises(se.VolumeIsNotInChain):
                subchain.validate()

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
            with pytest.raises(se.WrongParentVolume):
                subchain.validate()

    @pytest.mark.parametrize("shared_vol", [0, 1])
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
            with pytest.raises(se.SharedVolumeNonWritable):
                subchain.validate()


class TestPrepareMerge:

    @pytest.mark.parametrize("base, top, expected", [
        # Active merge, no capacity update, no allocation update
        (
            Volume(format='raw', virtual=1, physical=1, leaf=False),
            Volume(format='cow', virtual=1, physical=1, leaf=True),
            Expected(virtual=1024, physical=1024),
        ),
        # Active merge, no capacity update, increase LV size adding one chunk
        # of free space.
        (
            Volume(format='cow', virtual=10, physical=2, leaf=False),
            Volume(format='cow', virtual=10, physical=2, leaf=True),
            Expected(virtual=10240, physical=3200),
        ),
        # Internal merge, no capacity update, no LV extend.
        (
            Volume(format='cow', virtual=10, physical=2, leaf=False),
            Volume(format='cow', virtual=10, physical=2, leaf=False),
            Expected(virtual=10240, physical=2176),
        ),
        # Active merge, update capacity and increase LV size adding one chunk
        # of free space.
        (
            Volume(format='cow', virtual=3, physical=1, leaf=False),
            Volume(format='cow', virtual=5, physical=1, leaf=True),
            Expected(virtual=5120, physical=3200),
        ),
    ])
    def test_block_cow(self, monkeypatch, base, top, expected):
        with make_env('block', base, top) as env:
            base_vol = env.subchain.base_vol
            top_vol = env.subchain.top_vol

            def fake_measure(image, format=None, output_format=None,
                             backing=True, is_block=False, base=None,
                             unsafe=False):
                # Make sure we are called with the right arguments.
                assert image == top_vol.getVolumePath()
                assert format == qemuimg.FORMAT.QCOW2
                assert output_format == qemuimg.FORMAT.QCOW2
                assert backing
                assert is_block
                assert base == base_vol.getVolumePath()
                assert not unsafe

                # Return fake response.
                return {"required": 2048 * MiB, "bitmaps": 1 * MiB}

            monkeypatch.setattr(qemuimg, "measure", fake_measure)

            merge.prepare(env.subchain)

            assert self.expected_locks(env.subchain) == guarded.context.locks
            assert sc.LEGAL_VOL == base_vol.getLegality()
            assert expected.virtual * MiB == base_vol.getCapacity()
            new_size = env.sd_manifest.getVSize(
                base_vol.imgUUID, base_vol.volUUID)
            assert expected.physical * MiB == new_size

    @pytest.mark.xfail(reason="cannot create a domain object in the tests")
    @pytest.mark.parametrize("base, top, expected", [
        # Update capacity and fully allocate LV
        (
            Volume(format='raw', virtual=1, physical=1, leaf=False),
            Volume(format='cow', virtual=2, physical=1, leaf=True),
            Expected(virtual=2, physical=2),
        ),
    ])
    def test_block_raw(self, base, top, expected):
        with make_env('block', base, top) as env:
            merge.prepare(env.subchain)
            assert self.expected_locks(env.subchain) == guarded.context.locks
            base_vol = env.subchain.base_vol
            assert sc.LEGAL_VOL == base_vol.getLegality()
            new_base_size = base_vol.getCapacity()
            new_base_alloc = env.sd_manifest.getVSize(base_vol.imgUUID,
                                                      base_vol.volUUID)
            assert expected.virtual * GiB == new_base_size
            assert expected.physical * GiB == new_base_alloc

    @pytest.mark.parametrize("base, top, expected", [
        (
            Volume(format='cow', virtual=1, physical=0, leaf=False),
            Volume(format='cow', virtual=1, physical=0, leaf=True),
            Expected(virtual=1, physical=0),
        ),
        (
            Volume(format='cow', virtual=1, physical=0, leaf=False),
            Volume(format='cow', virtual=2, physical=0, leaf=True),
            Expected(virtual=2, physical=0),
        ),
    ])
    def test_file_cow(self, base, top, expected):
        with make_env('file', base, top) as env:
            merge.prepare(env.subchain)
            base_vol = env.subchain.base_vol
            assert sc.LEGAL_VOL == base_vol.getLegality()
            new_base_size = base_vol.getCapacity()
            assert expected.virtual * GiB == new_base_size

    @pytest.mark.xfail(reason="cannot create a domain object in the tests")
    @pytest.mark.parametrize("base, top, expected", [
        (
            Volume(format='raw', virtual=1, physical=0, leaf=False),
            Volume(format='cow', virtual=2, physical=0, leaf=True),
            Expected(virtual=2, physical=0),
        ),
    ])
    def test_file_raw(self, base, top, expected):
        with make_env('file', base, top) as env:
            merge.prepare(env.subchain)
            base_vol = env.subchain.base_vol
            assert sc.LEGAL_VOL == base_vol.getLegality()
            new_base_size = base_vol.getCapacity()
            assert expected.virtual * GiB == new_base_size

    def expected_locks(self, subchain):
        img_ns = rm.getNamespace(sc.IMAGE_NAMESPACE, subchain.sd_id)
        return [
            rm.Lock(sc.STORAGE, subchain.sd_id, rm.SHARED),
            rm.Lock(img_ns, subchain.img_id, rm.EXCLUSIVE),
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


class TestFinalizeMerge:

    # TODO: use one make_env for all tests?
    @contextmanager
    def make_env(
            self,
            sd_type='block',
            format='raw',
            prealloc=sc.SPARSE_VOL,
            chain_len=2):
        size = 2 * GiB
        base_fmt = sc.name2type(format)
        with fake_env(sd_type) as env:
            with MonkeyPatch().context() as mp:
                mp.setattr(guarded, 'context', fake_guarded_context())
                mp.setattr(merge, 'sdCache', env.sdcache)
                mp.setattr(blockVolume, 'rm', FakeResourceManager())
                mp.setattr(image, 'Image', FakeImage)

                env.chain = make_qemu_chain(
                    env, size, base_fmt, chain_len, prealloc=prealloc)

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

    @pytest.mark.parametrize("sd_type, chain_len, base_index, top_index", [
        pytest.param('file', 2, 0, 1),
        pytest.param('block', 2, 0, 1),
        pytest.param('file', 4, 1, 2),
        pytest.param('block', 4, 1, 2),
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
                backing_file = volume.getBackingVolumePath(
                    subchain.img_id, subchain.base_id)
                assert info['backing-filename'] == backing_file

            # verify syncVolumeChain arguments
            self.check_sync_volume_chain(subchain, env.chain[-1].volUUID)
            new_chain = [vol.volUUID for vol in env.chain]
            new_chain.remove(top_vol.volUUID)
            assert image.Image.syncVolumeChain.actual_chain == new_chain

            assert base_vol.getLegality() == sc.LEGAL_VOL

    @pytest.mark.parametrize("volume", ["base", "top"])
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

            with pytest.raises(se.prepareIllegalVolumeError):
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

            with MonkeyPatch().context() as mp:
                mp.setattr(qemuimg._qemuimg, '_cmd', '/usr/bin/false')

                with pytest.raises(cmdutils.Error):
                    merge.finalize(subchain)

            assert subchain.top_vol.getLegality() == sc.LEGAL_VOL
            assert subchain.top_vol.getParent() == base_vol.volUUID

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

            with MonkeyPatch().context() as mp:
                def failing_rebase(*args, **kw):
                    return operation.Command("/usr/bin/false")

                mp.setattr(qemuimg, 'rebase', failing_rebase)
                mp.setattr(volume.VolumeManifest, 'setLegality', setLegality)
                with pytest.raises(cmdutils.Error):
                    merge.finalize(subchain)

            assert subchain.top_vol.getLegality() == sc.ILLEGAL_VOL

    def test_reduce_chunked_internal(self):
        with self.make_env(sd_type='block', format='cow', chain_len=4) as env:
            base_vol = env.chain[1]
            top_vol = env.chain[2]
            assert not top_vol.isLeaf()
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

            assert fake_base_vol.__calls__ == [
                ('reduce', (base_vol.optimal_size(),), {}),
            ]

    def test_reduce_chunked_leaf(self):
        with self.make_env(sd_type='block', format='cow', chain_len=3) as env:
            base_vol = env.chain[1]
            top_vol = env.chain[2]
            assert top_vol.isLeaf()
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

            assert fake_base_vol.__calls__ == [
                ('reduce', (base_vol.optimal_size(as_leaf=True),), {}),
            ]

    @pytest.mark.parametrize("sd_type, format, prealloc", [
        # Not chunked, reduce not called
        pytest.param('file', 'cow', sc.SPARSE_VOL),
        # Preallocated, reduce not called
        pytest.param('block', 'cow', sc.PREALLOCATED_VOL),
    ])
    def test_reduce_skipped(self, sd_type, format, prealloc):
        with self.make_env(
                sd_type=sd_type,
                format=format,
                prealloc=prealloc,
                chain_len=4) as env:
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
            assert len(calls) == 0

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
                "cmd", 5, ["out"], ["err"])

            with pytest.raises(se.LogicalVolumeExtendError):
                merge.finalize(subchain)

            # verify syncVolumeChain arguments
            self.check_sync_volume_chain(subchain, env.chain[-1].volUUID)

    @pytest.mark.parametrize("base_fmt", ["raw", "cow"])
    def test_chain_after_finalize(self, base_fmt):
        with self.make_env(format=base_fmt, chain_len=3) as env:
            base_vol = env.chain[0]
            # We write data to the base and will read it from the child volume
            # to verify that the chain is valid after qemu-rebase.
            offset = 0
            pattern = 0xf0
            length = KiB
            qemuio.write_pattern(
                base_vol.volumePath,
                sc.fmt2str(base_vol.getFormat()),
                offset=offset,
                len=length,
                pattern=pattern)

            top_vol = env.chain[1]
            child_vol = env.chain[2]

            subchain_info = dict(sd_id=base_vol.sdUUID,
                                 img_id=base_vol.imgUUID,
                                 base_id=base_vol.volUUID,
                                 top_id=top_vol.volUUID,
                                 base_generation=0)
            subchain = merge.SubchainInfo(subchain_info, 0)

            merge.finalize(subchain)

            qemuio.verify_pattern(
                child_vol.volumePath,
                sc.fmt2str(child_vol.getFormat()),
                offset=offset,
                len=length,
                pattern=pattern)

    def check_sync_volume_chain(self, subchain, removed_vol_id):
        sync = image.Image.syncVolumeChain
        assert sync.sd_id == subchain.sd_id
        assert sync.img_id == subchain.img_id
        assert sync.vol_id == removed_vol_id
