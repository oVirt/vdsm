# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import
from __future__ import division

import os
import uuid

import pytest

from vdsm.common.units import MiB
from vdsm.storage import constants as sc
from vdsm.storage import exception as se
from vdsm.storage import clusterlock
from vdsm.storage import sd

from testlib import recorded
from testlib import make_uuid

from storage.storagetestlib import (
    fake_block_env,
    fake_file_env,
    make_file_volume,
)


# We want to create volumes larger than the minimum block volume size
# (currently 128 MiB).
VOLSIZE = 256 * MiB


@pytest.fixture(params=[
    'server:/path',
    '192.168.200.2:/path',
    '[201::1]:/path'
])
def file_env(request):
    with fake_file_env(remote_path=request.param) as env:
        yield env


class ManifestMixin(object):

    def test_init_failure_raises(self, monkeypatch, file_env):
        def fail(*a, **kw):
            raise RuntimeError("injected failure")

        monkeypatch.setattr(clusterlock.SANLock, 'initLock', fail)
        with pytest.raises(RuntimeError):
            file_env.sd_manifest.initDomainLock()


class TestFileManifest(ManifestMixin):

    def setup_method(self):
        self.img_id = str(uuid.uuid4())
        self.vol_id = str(uuid.uuid4())

    def test_get_monitoring_path(self, file_env):
        assert (file_env.sd_manifest.metafile ==
                file_env.sd_manifest.getMonitoringPath())

    def test_getvsize(self, file_env):
        make_file_volume(file_env.sd_manifest, VOLSIZE,
                         self.img_id, self.vol_id)
        assert VOLSIZE == file_env.sd_manifest.getVSize(
            self.img_id, self.vol_id)

    def test_getvallocsize(self, file_env):
        make_file_volume(file_env.sd_manifest, VOLSIZE,
                         self.img_id, self.vol_id)
        vol = file_env.sd_manifest.produceVolume(self.img_id, self.vol_id)
        allocated = os.stat(vol.getVolumePath()).st_blocks * 512
        assert allocated <= file_env.sd_manifest.getVAllocSize(
            self.img_id, self.vol_id)

    def test_getisodomainimagesdir(self, file_env):
        isopath = os.path.join(file_env.sd_manifest.domaindir,
                               sd.DOMAIN_IMAGES, sd.ISO_IMAGE_UUID)
        assert isopath == file_env.sd_manifest.getIsoDomainImagesDir()

    def test_getmdpath(self, file_env):
        sd_manifest = file_env.sd_manifest
        mdpath = os.path.join(sd_manifest.domaindir, sd.DOMAIN_META_DATA)
        assert mdpath == file_env.sd_manifest.getMDPath()

    def test_getmetaparam(self, file_env):
        sd_manifest = file_env.sd_manifest
        assert (sd_manifest.sdUUID ==
                sd_manifest.getMetaParam(sd.DMDK_SDUUID))

    def test_getallimages(self, file_env):
        assert set() == file_env.sd_manifest.getAllImages()
        img_id = str(uuid.uuid4())
        vol_id = str(uuid.uuid4())
        make_file_volume(file_env.sd_manifest, VOLSIZE, img_id, vol_id)
        assert img_id in file_env.sd_manifest.getAllImages()

    def test_purgeimage_race(self, file_env):
        sd_id = file_env.sd_manifest.sdUUID
        img_id = str(uuid.uuid4())
        vol_id = str(uuid.uuid4())
        make_file_volume(file_env.sd_manifest, VOLSIZE, img_id, vol_id)

        file_env.sd_manifest.deleteImage(sd_id, img_id, None)
        # Simulate StorageDomain.imageGarbageCollector by removing the
        # deleted image directory.
        deleted_dir = file_env.sd_manifest.getDeletedImagePath(img_id)
        file_env.sd_manifest.oop.fileUtils.cleanupdir(deleted_dir)
        # purgeImage should not raise if the image was already removed
        file_env.sd_manifest.purgeImage(sd_id, img_id, [vol_id], False)


class TestBlockManifest(ManifestMixin):
    env = fake_block_env

    def test_get_monitoring_path(self):
        with self.env() as env:
            md_lv_path = env.lvm.lvPath(env.sd_manifest.sdUUID, sd.METADATA)
            assert md_lv_path == env.sd_manifest.getMonitoringPath()

    def test_getvsize_active_lv(self):
        # Tests the path when the device file is present
        with self.env() as env:
            vg_name = env.sd_manifest.sdUUID
            lv_name = str(uuid.uuid4())
            env.lvm.createLV(vg_name, lv_name, VOLSIZE // MiB)
            env.lvm.fake_lv_symlink_create(vg_name, lv_name)
            assert VOLSIZE == env.sd_manifest.getVSize('<imgUUID>', lv_name)

    def test_getvsize_inactive_lv(self):
        # Tests the path when the device file is not present
        with self.env() as env:
            lv_name = str(uuid.uuid4())
            env.lvm.createLV(env.sd_manifest.sdUUID, lv_name, VOLSIZE // MiB)
            assert VOLSIZE == env.sd_manifest.getVSize('<imgUUID>', lv_name)

    def test_getmetaparam(self):
        with self.env() as env:
            assert (env.sd_manifest.sdUUID ==
                    env.sd_manifest.getMetaParam(sd.DMDK_SDUUID))


class TestBlockDomainMetadataSlot:

    # Note: the first 4 slots (0-3) are reserved for domain metadata in V3,4
    @pytest.mark.parametrize("used_slots, free_slot", [
        [[], 4],
        [[4], 5],
        [[5], 4],
        [[4, 6], 5],
        [[4, 7], 5],
    ])
    @pytest.mark.parametrize("sd_version", [3, 4])
    def test_metaslot_selection_v4(self, used_slots, free_slot, sd_version):
        self._metaslot_selection(used_slots, free_slot, sd_version)

    @pytest.mark.parametrize("used_slots, free_slot", [
        [[], 1],
        [[1], 2],
        [[2], 1],
        [[1, 3], 2],
        [[1, 4], 2],
    ])
    def test_metaslot_selection_v5(self, used_slots, free_slot):
        self._metaslot_selection(used_slots, free_slot, 5)

    def _metaslot_selection(self, used_slots, free_slot, sd_version):
        with fake_block_env(sd_version=sd_version) as env:
            for offset in used_slots:
                lv = make_uuid()
                sduuid = env.sd_manifest.sdUUID
                env.lvm.createLV(sduuid, lv, VOLSIZE // MiB)
                tag = sc.TAG_PREFIX_MD + str(offset)
                env.lvm.changeLVsTags(sduuid, (lv,), addTags=(tag,))
            with env.sd_manifest.acquireVolumeMetadataSlot(None) as mdSlot:
                assert mdSlot == free_slot

    @pytest.mark.parametrize("sd_version", [3, 4, 5])
    def test_metaslot_lock(self, sd_version):
        with fake_block_env(sd_version=sd_version) as env:
            with env.sd_manifest.acquireVolumeMetadataSlot(None):
                acquired = env.sd_manifest._lvTagMetaSlotLock.acquire(False)
                assert not acquired


class StorageDomainManifest(sd.StorageDomainManifest):
    def __init__(self):
        pass

    @recorded
    def acquireDomainLock(self, host_id):
        pass

    @recorded
    def releaseDomainLock(self):
        pass

    @recorded
    def dummy(self):
        pass


class TestDomainLock():

    def test_domainlock_contextmanager(self):
        expected_calls = [("acquireDomainLock", (1,), {}),
                          ("dummy", (), {}),
                          ("releaseDomainLock", (), {})]
        manifest = StorageDomainManifest()
        with manifest.domain_lock(1):
            manifest.dummy()
        assert manifest.__calls__ == expected_calls

    def test_domainlock_contextmanager_exception(self):
        class InjectedFailure(Exception):
            pass

        expected_calls = [("acquireDomainLock", (1,), {}),
                          ("releaseDomainLock", (), {})]
        manifest = StorageDomainManifest()
        with pytest.raises(InjectedFailure):
            with manifest.domain_lock(1):
                raise InjectedFailure()
        assert manifest.__calls__ == expected_calls


class FakeStorageDomainManifest(StorageDomainManifest):
    def __init__(self):
        pass


class TestCreateVolumeParams:

    @pytest.mark.parametrize("vol_format", sc.VOL_FORMAT)
    def test_valid_format(self, vol_format):
        dom = FakeStorageDomainManifest()
        dom.validateCreateVolumeParams(vol_format, sc.BLANK_UUID)

    def test_invalid_format(self):
        dom = FakeStorageDomainManifest()
        with pytest.raises(se.IncorrectFormat):
            dom.validateCreateVolumeParams(-1, sc.BLANK_UUID)

    @pytest.mark.parametrize("disk_type", sc.VOL_DISKTYPE)
    def test_valid_type(self, disk_type):
        dom = FakeStorageDomainManifest()
        dom.validateCreateVolumeParams(
            sc.RAW_FORMAT, sc.BLANK_UUID, diskType=disk_type)

    def test_invalid_type(self):
        dom = FakeStorageDomainManifest()
        with pytest.raises(se.InvalidParameterException):
            dom.validateCreateVolumeParams(
                sc.RAW_FORMAT, sc.BLANK_UUID, diskType="FAIL")

    def test_invalid_parent(self):
        dom = FakeStorageDomainManifest()
        with pytest.raises(se.IncorrectFormat):
            dom.validateCreateVolumeParams(
                sc.RAW_FORMAT, "11111111-1111-1111-1111-11111111111")

    @pytest.mark.parametrize("preallocate", sc.VOL_TYPE)
    def test_valid_preallocate(self, preallocate):
        dom = FakeStorageDomainManifest()
        dom.validateCreateVolumeParams(
            sc.RAW_FORMAT, sc.BLANK_UUID, preallocate=preallocate)

    def test_invalid_preallocate(self):
        dom = FakeStorageDomainManifest()
        with pytest.raises(se.IncorrectType):
            dom.validateCreateVolumeParams(
                sc.RAW_FORMAT, sc.BLANK_UUID, preallocate=-1)
