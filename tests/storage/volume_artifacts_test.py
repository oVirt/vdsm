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

import os

from monkeypatch import MonkeyPatchScope
from testlib import make_uuid
from testlib import VdsmTestCase
from testlib import permutations, expandPermutations
from testValidation import brokentest

from storage.storagetestlib import (
    fake_block_env,
    fake_file_env,
)

from vdsm.config import config
from vdsm.storage import blockVolume
from vdsm.storage import constants as sc
from vdsm.storage import exception as se
from vdsm.storage import fileVolume
from vdsm.storage import image
from vdsm.storage import misc
from vdsm.storage import qemuimg
from vdsm.storage import sd
from vdsm.storage.sdm.api import create_volume
from vdsm.storage.volumemetadata import VolumeMetadata


class ExpectedFailure(Exception):
    pass


def failure(*args, **kwargs):
    raise ExpectedFailure()


MB = 1024 ** 2
VOL_SIZE = 1073741824
BLOCK_INITIAL_CHUNK_SIZE = MB * config.getint("irs",
                                              "volume_utilization_chunk_mb")
BASE_PARAMS = {
    sc.RAW_FORMAT: (VOL_SIZE, sc.RAW_FORMAT,
                    image.SYSTEM_DISK_TYPE, 'raw_volume'),
    sc.COW_FORMAT: (VOL_SIZE, sc.COW_FORMAT,
                    image.SYSTEM_DISK_TYPE, 'cow_volume')
}


@expandPermutations
class VolumeArtifactsTestsMixin(object):

    def setUp(self):
        self.img_id = make_uuid()
        self.vol_id = make_uuid()

    def test_state_missing(self):
        with self.fake_env() as env:
            artifacts = env.sd_manifest.get_volume_artifacts(
                self.img_id, self.vol_id)
            self.assertFalse(artifacts.is_garbage())
            self.assertFalse(artifacts.is_image())
            self.assertRaises(AssertionError,
                              self.validate_artifacts, artifacts, env)

    def test_state_garbage_volatile_image_dir(self):
        with self.fake_env() as env:
            artifacts = env.sd_manifest.get_volume_artifacts(
                self.img_id, self.vol_id)
            artifacts.create(*BASE_PARAMS[sc.RAW_FORMAT])
            self.assertTrue(artifacts.is_garbage())
            self.assertFalse(artifacts.is_image())
            self.validate_artifacts(artifacts, env)

    def test_state_garbage_create_raises(self):
        with self.fake_env() as env:
            artifacts = env.sd_manifest.get_volume_artifacts(
                self.img_id, self.vol_id)
            artifacts.create(*BASE_PARAMS[sc.RAW_FORMAT])
            self.assertRaises(se.DomainHasGarbage, artifacts.create,
                              *BASE_PARAMS[sc.RAW_FORMAT])

    def test_state_image(self):
        with self.fake_env() as env:
            artifacts = env.sd_manifest.get_volume_artifacts(
                self.img_id, self.vol_id)
            artifacts.create(*BASE_PARAMS[sc.RAW_FORMAT])
            artifacts.commit()
            self.assertFalse(artifacts.is_garbage())
            self.assertTrue(artifacts.is_image())

    def test_create_additional_vol_missing_parent_id(self):
        with self.fake_env() as env:
            first = env.sd_manifest.get_volume_artifacts(
                self.img_id, self.vol_id)
            first.create(*BASE_PARAMS[sc.RAW_FORMAT])
            first.commit()
            second = env.sd_manifest.get_volume_artifacts(
                self.img_id, make_uuid())
            self.assertRaises(se.InvalidParameterException,
                              second.create, *BASE_PARAMS[sc.COW_FORMAT])

    def test_create_additional_raw_vol(self):
        with self.fake_env() as env:
            first = env.sd_manifest.get_volume_artifacts(
                self.img_id, self.vol_id)
            first.create(*BASE_PARAMS[sc.RAW_FORMAT])
            first.commit()
            second = env.sd_manifest.get_volume_artifacts(
                self.img_id, make_uuid())
            self.assertRaises(se.InvalidParameterException, second.create,
                              *BASE_PARAMS[sc.RAW_FORMAT])

    @brokentest("Broken until parent volume support is added")
    def test_create_same_volume_in_image(self):
        with self.fake_env() as env:
            artifacts = env.sd_manifest.get_volume_artifacts(
                self.img_id, self.vol_id)
            artifacts.create(*BASE_PARAMS[sc.RAW_FORMAT])
            artifacts.commit()
            artifacts = env.sd_manifest.get_volume_artifacts(
                self.img_id, self.vol_id)
            parent = create_volume.ParentVolumeInfo(
                dict(img_id=self.img_id, vol_id=self.vol_id))
            params = BASE_PARAMS[sc.COW_FORMAT] + (parent,)

            # Until COW and parent support are added, the call to create will
            # raise NotImplementedError
            self.assertRaises(se.VolumeAlreadyExists,
                              artifacts.create, *params)

    def test_new_image_create_and_commit(self):
        with self.fake_env() as env:
            artifacts = env.sd_manifest.get_volume_artifacts(
                self.img_id, self.vol_id)
            size, vol_format, disk_type, desc = BASE_PARAMS[sc.RAW_FORMAT]
            artifacts.create(size, vol_format, disk_type, desc)
            artifacts.commit()
            vol = env.sd_manifest.produceVolume(self.img_id, self.vol_id)
            self.assertEqual(sc.type2name(sc.LEAF_VOL), vol.getVolType())
            self.assertEqual(desc, vol.getDescription())
            self.assertEqual(sc.LEGAL_VOL, vol.getLegality())
            self.assertEqual(size / sc.BLOCK_SIZE, vol.getSize())
            self.assertEqual(size, os.stat(artifacts.volume_path).st_size)
            self.assertEqual(vol_format, vol.getFormat())
            self.assertEqual(str(disk_type), vol.getDiskType())

    @permutations([[sc.RAW_FORMAT, 'raw'], [sc.COW_FORMAT, 'qcow2']])
    def test_qemuimg_info(self, vol_format, qemu_format):
        with self.fake_env() as env:
            artifacts = env.sd_manifest.get_volume_artifacts(
                self.img_id, self.vol_id)
            size, vol_format, disk_type, desc = BASE_PARAMS[vol_format]
            artifacts.create(size, vol_format, disk_type, desc)
            artifacts.commit()
            info = qemuimg.info(artifacts.volume_path)
            self.assertEqual(qemu_format, info['format'])
            self.assertEqual(size, info['virtualsize'])
            self.assertNotIn('backingfile', info)

    def test_unaligned_size_raises(self):
        with fake_block_env() as env:
            artifacts = env.sd_manifest.get_volume_artifacts(
                self.img_id, self.vol_id)
            size, vol_format, disk_type, desc = BASE_PARAMS[sc.RAW_FORMAT]
            size = MB + 1
            self.assertRaises(se.InvalidParameterException,
                              artifacts.create, size, vol_format, disk_type,
                              desc)

    # Artifacts visibility

    def test_getallvolumes(self):
        # Artifacts must not be recognized as volumes until commit is called.
        with self.fake_env() as env:
            self.assertEqual({}, env.sd_manifest.getAllVolumes())
            artifacts = env.sd_manifest.get_volume_artifacts(
                self.img_id, self.vol_id)
            artifacts.create(*BASE_PARAMS[sc.RAW_FORMAT])
            self.assertEqual({}, env.sd_manifest.getAllVolumes())
            artifacts.commit()
            self.assertIn(self.vol_id, env.sd_manifest.getAllVolumes())

    def validate_domain_has_garbage(self, sd_manifest):
        # Checks that existing garbage on the storage domain prevents creation
        # of these artifacts again.
        artifacts = sd_manifest.get_volume_artifacts(
            self.img_id, self.vol_id)
        self.assertRaises(se.DomainHasGarbage, artifacts.create,
                          *BASE_PARAMS[sc.RAW_FORMAT])


@expandPermutations
class TestFileVolumeArtifacts(VolumeArtifactsTestsMixin, VdsmTestCase):

    def fake_env(self):
        return fake_file_env()

    @permutations([[sc.RAW_FORMAT], [sc.COW_FORMAT]])
    def test_volume_preallocation(self, vol_format):
        with self.fake_env() as env:
            artifacts = env.sd_manifest.get_volume_artifacts(
                self.img_id, self.vol_id)
            artifacts.create(*BASE_PARAMS[vol_format])
            artifacts.commit()
            vol = env.sd_manifest.produceVolume(self.img_id, self.vol_id)
            self.assertEqual(sc.SPARSE_VOL, vol.getType())

    def test_new_image_create_metadata_failure(self):
        # If we fail before the metadata is created we will have an empty
        # image directory with a garbage collection prefix left behind
        with self.fake_env() as env:
            artifacts = env.sd_manifest.get_volume_artifacts(
                self.img_id, self.vol_id)
            with MonkeyPatchScope([
                [VolumeMetadata, '__init__', failure]
            ]):
                self.assertRaises(ExpectedFailure, artifacts.create,
                                  *BASE_PARAMS[sc.RAW_FORMAT])
            self.validate_new_image_path(artifacts)
            self.validate_domain_has_garbage(env.sd_manifest)

    def test_new_image_create_lease_failure(self):
        # If we fail before the lease is created we will have a garbage image
        # directory containing a metadata file with the .artifact extension
        with self.fake_env() as env:
            artifacts = env.sd_manifest.get_volume_artifacts(
                self.img_id, self.vol_id)
            with MonkeyPatchScope([
                [fileVolume.FileVolumeManifest, 'newVolumeLease', failure]
            ]):
                self.assertRaises(ExpectedFailure, artifacts.create,
                                  *BASE_PARAMS[sc.RAW_FORMAT])
            self.validate_new_image_path(artifacts, has_md=True)
            self.validate_domain_has_garbage(env.sd_manifest)

    def test_new_image_create_container_failure(self):
        # If we fail before the container is created we will have a garbage
        # image directory containing artifact metadata and a lease file.
        with self.fake_env() as env:
            artifacts = env.sd_manifest.get_volume_artifacts(
                self.img_id, self.vol_id)
            # We cannot MonkeyPatch the underlying function 'truncateFile'
            # because it is also used for lease creation and would cause a
            # premature failure.  Instead, we'll replace a function in the
            # FileVolumeArtifacts class.
            artifacts._create_volume_file = failure
            self.assertRaises(ExpectedFailure, artifacts.create,
                              *BASE_PARAMS[sc.RAW_FORMAT])
            self.validate_new_image_path(artifacts,
                                         has_md=True, has_lease=True)
            self.validate_domain_has_garbage(env.sd_manifest)

    def test_garbage_image_dir(self):
        # Creating the an artifact using an existing garbage image directory is
        # not allowed.
        with self.fake_env() as env:
            artifacts = env.sd_manifest.get_volume_artifacts(
                self.img_id, self.vol_id)
            artifacts._create_metadata_artifact = failure
            self.assertRaises(ExpectedFailure, artifacts.create,
                              *BASE_PARAMS[sc.RAW_FORMAT])
            self.validate_domain_has_garbage(env.sd_manifest)

    # Invalid use of artifacts

    def test_new_image_commit_without_create(self):
        with self.fake_env() as env:
            artifacts = env.sd_manifest.get_volume_artifacts(
                self.img_id, self.vol_id)
            self.assertRaises(OSError, artifacts.commit)

    def test_new_image_commit_twice(self):
        with self.fake_env() as env:
            artifacts = env.sd_manifest.get_volume_artifacts(
                self.img_id, self.vol_id)
            artifacts.create(*BASE_PARAMS[sc.RAW_FORMAT])
            artifacts.commit()
            self.assertRaises(OSError, artifacts.commit)

    @permutations([[0], [MB]])
    def test_initial_size_not_supported(self, initial_size):
        with self.fake_env() as env:
            artifacts = env.sd_manifest.get_volume_artifacts(
                self.img_id, self.vol_id)
            self.assertRaises(se.InvalidParameterException, artifacts.create,
                              *BASE_PARAMS[sc.RAW_FORMAT],
                              initial_size=initial_size)

    def validate_new_image_path(self, artifacts, has_md=False,
                                has_lease=False, has_volume=False):
        path = artifacts.artifacts_dir
        self.assertTrue(
            os.path.basename(path).startswith(sd.REMOVED_IMAGE_PREFIX))
        self.assertTrue(os.path.exists(path))
        self.assertFalse(os.path.exists(artifacts._image_dir))
        self.assertEqual(has_md, os.path.exists(artifacts.meta_volatile_path))
        self.assertEqual(has_lease, os.path.exists(artifacts.lease_path))
        self.assertEqual(has_volume, os.path.exists(artifacts.volume_path))

    def validate_artifacts(self, artifacts, env):
        self.validate_metadata(env, artifacts)
        self.assertTrue(os.path.exists(artifacts.volume_path))
        self.assertTrue(os.path.exists(artifacts.lease_path))

    def validate_metadata(self, env, artifacts):
        meta_path = artifacts.meta_volatile_path
        self.assertTrue(os.path.exists(meta_path))
        with open(meta_path) as f:
            md_lines = f.readlines()
        md = VolumeMetadata.from_lines(md_lines)

        # Test a few fields just to check that metadata was written
        self.assertEqual(artifacts.sd_manifest.sdUUID, md.domain)
        self.assertEqual(artifacts.img_id, md.image)


class TestFileVolumeArtifactVisibility(VdsmTestCase):

    def setUp(self):
        self.img_id = make_uuid()
        self.vol_id = make_uuid()

    def test_getallimages(self):
        # The current behavior of getAllImages is to report garbage image
        # directories (perhaps this should be changed).
        with fake_file_env() as env:
            garbage_img_id = sd.REMOVED_IMAGE_PREFIX + self.img_id
            self.assertEqual(set(), env.sd_manifest.getAllImages())
            artifacts = env.sd_manifest.get_volume_artifacts(
                self.img_id, self.vol_id)
            artifacts.create(*BASE_PARAMS[sc.RAW_FORMAT])
            self.assertEqual({garbage_img_id}, env.sd_manifest.getAllImages())
            artifacts.commit()
            self.assertEqual({self.img_id}, env.sd_manifest.getAllImages())


@expandPermutations
class TestBlockVolumeArtifacts(VolumeArtifactsTestsMixin, VdsmTestCase):

    def fake_env(self):
        return fake_block_env()

    @permutations([
        [sc.RAW_FORMAT, sc.PREALLOCATED_VOL],
        [sc.COW_FORMAT, sc.SPARSE_VOL]
    ])
    def test_volume_preallocation(self, vol_format, alloc_policy):
        with self.fake_env() as env:
            artifacts = env.sd_manifest.get_volume_artifacts(
                self.img_id, self.vol_id)
            size, vol_format, disk_type, desc = BASE_PARAMS[vol_format]
            artifacts.create(size, vol_format, disk_type, desc)
            artifacts.commit()
            vol = env.sd_manifest.produceVolume(self.img_id, self.vol_id)
            self.assertEqual(alloc_policy, vol.getType())

    @permutations([[0], [sc.BLOCK_SIZE]])
    def test_raw_volume_initial_size(self, initial_size):
        with self.fake_env() as env:
            artifacts = env.sd_manifest.get_volume_artifacts(
                self.img_id, self.vol_id)
            self.assertRaises(se.InvalidParameterException, artifacts.create,
                              *BASE_PARAMS[sc.RAW_FORMAT],
                              initial_size=initial_size)

    @permutations([
        [None, BLOCK_INITIAL_CHUNK_SIZE],
        [MB, sc.VG_EXTENT_SIZE_MB * MB]
    ])
    def test_cow_volume_initial_size(self, requested_size, actual_size):
        test_size = 2 * BLOCK_INITIAL_CHUNK_SIZE
        with self.fake_env() as env:
            artifacts = env.sd_manifest.get_volume_artifacts(
                self.img_id, self.vol_id)
            size, vol_format, disk_type, desc = BASE_PARAMS[sc.COW_FORMAT]
            artifacts.create(test_size, vol_format, disk_type, desc,
                             initial_size=requested_size)
            artifacts.commit()

            # Note: Here we check the size via FakeLVM instead of by using
            # sd_manifest.getVSize.  The qemu-img program sees our fake LV as a
            # file and 'helpfully' truncates it to the minimal size required.
            # Therefore, we cannot use file size for this test.
            lv = env.lvm.getLV(env.sd_manifest.sdUUID, self.vol_id)
            self.assertEqual(actual_size, int(lv.size))
            vol = env.sd_manifest.produceVolume(self.img_id, self.vol_id)
            self.assertEqual(test_size, vol.getSize() * sc.BLOCK_SIZE)

    def test_size_rounded_up(self):
        # If the underlying device is larger the size will be updated
        with fake_block_env() as env:
            sd_id = env.sd_manifest.sdUUID
            vg = env.lvm.getVG(sd_id)
            expected_size = int(vg.extent_size)
            requested_size = expected_size - MB
            artifacts = env.sd_manifest.get_volume_artifacts(
                self.img_id, self.vol_id)
            artifacts.create(requested_size, sc.RAW_FORMAT,
                             image.SYSTEM_DISK_TYPE, 'raw_volume')
            artifacts.commit()
            vol = env.sd_manifest.produceVolume(self.img_id, self.vol_id)
            self.assertEqual(expected_size / sc.BLOCK_SIZE, vol.getSize())
            self.assertEqual(expected_size,
                             int(env.lvm.getLV(sd_id, self.vol_id).size))

    def test_create_fail_creating_lv(self):
        # If we fail to create the LV then storage is clean and we can retry
        with self.fake_env() as env:
            artifacts = env.sd_manifest.get_volume_artifacts(self.img_id,
                                                             self.vol_id)
            with MonkeyPatchScope([[env.lvm, 'createLV', failure]]):
                self.assertRaises(ExpectedFailure, artifacts.create,
                                  *BASE_PARAMS[sc.RAW_FORMAT])
            self.validate_invisibility(env, artifacts, is_garbage=False)

            # Storage is clean so we should be able to retry
            artifacts = env.sd_manifest.get_volume_artifacts(self.img_id,
                                                             self.vol_id)
            artifacts.create(*BASE_PARAMS[sc.RAW_FORMAT])

    def test_create_fail_acquiring_meta_slot(self):
        # If we fail to acquire the meta_slot we have just a garbage LV
        with self.fake_env() as env:
            artifacts = env.sd_manifest.get_volume_artifacts(self.img_id,
                                                             self.vol_id)
            with MonkeyPatchScope([
                [env.sd_manifest, 'acquireVolumeMetadataSlot', failure]
            ]):
                self.assertRaises(ExpectedFailure, artifacts.create,
                                  *BASE_PARAMS[sc.RAW_FORMAT])
            self.validate_invisibility(env, artifacts, is_garbage=True)
            self.validate_domain_has_garbage(env.sd_manifest)

    def test_create_fail_setting_metadata_lvtag(self):
        # If we fail to set the meta_slot in the LV tags that slot remains
        # available for allocation (even without garbage collection)
        with self.fake_env() as env:
            slot_before = self.get_next_free_slot(env)
            artifacts = env.sd_manifest.get_volume_artifacts(
                self.img_id, self.vol_id)
            with MonkeyPatchScope([[env.lvm, 'changeLVTags', failure]]):
                self.assertRaises(ExpectedFailure, artifacts.create,
                                  *BASE_PARAMS[sc.RAW_FORMAT])
            self.assertEqual(slot_before, self.get_next_free_slot(env))
            self.validate_invisibility(env, artifacts, is_garbage=True)
            self.validate_domain_has_garbage(env.sd_manifest)

    def test_create_fail_writing_metadata(self):
        # If we fail to write metadata we will be left with a garbage LV and an
        # allocated metadata slot which is not freed until the LV is removed.
        with self.fake_env() as env:
            slot_before = self.get_next_free_slot(env)
            artifacts = env.sd_manifest.get_volume_artifacts(
                self.img_id, self.vol_id)
            with MonkeyPatchScope([
                [blockVolume.BlockVolumeManifest, 'newMetadata', failure]
            ]):
                self.assertRaises(ExpectedFailure, artifacts.create,
                                  *BASE_PARAMS[sc.RAW_FORMAT])
            self.validate_invisibility(env, artifacts, is_garbage=True)
            self.validate_domain_has_garbage(env.sd_manifest)
            self.assertNotEqual(slot_before, self.get_next_free_slot(env))

    def test_create_fail_creating_lease(self):
        # We leave behind a garbage LV and metadata area
        with self.fake_env() as env:
            artifacts = env.sd_manifest.get_volume_artifacts(
                self.img_id, self.vol_id)
            with MonkeyPatchScope([
                [blockVolume.BlockVolumeManifest, 'newVolumeLease', failure]
            ]):
                self.assertRaises(ExpectedFailure, artifacts.create,
                                  *BASE_PARAMS[sc.RAW_FORMAT])
            self.validate_invisibility(env, artifacts, is_garbage=True)
            self.validate_domain_has_garbage(env.sd_manifest)

    # Invalid use of artifacts

    def test_commit_without_create(self):
        with self.fake_env() as env:
            artifacts = env.sd_manifest.get_volume_artifacts(
                self.img_id, self.vol_id)
            self.assertRaises(se.LogicalVolumeDoesNotExistError,
                              artifacts.commit)

    def test_commit_twice(self):
        with self.fake_env() as env:
            artifacts = env.sd_manifest.get_volume_artifacts(
                self.img_id, self.vol_id)
            artifacts.create(*BASE_PARAMS[sc.RAW_FORMAT])
            artifacts.commit()
            self.assertRaises(se.VolumeAlreadyExists, artifacts.commit)

    def test_unaligned_initial_size_raises(self):
        with fake_block_env() as env:
            artifacts = env.sd_manifest.get_volume_artifacts(
                self.img_id, self.vol_id)
            size, vol_format, disk_type, desc = BASE_PARAMS[sc.COW_FORMAT]
            initial_size = size - 1
            self.assertRaises(se.InvalidParameterException,
                              artifacts.create, size, vol_format, disk_type,
                              desc, initial_size=initial_size)

    def test_oversized_initial_size_raises(self):
        with self.fake_env() as env:
            artifacts = env.sd_manifest.get_volume_artifacts(
                self.img_id, self.vol_id)
            size, vol_format, disk_type, desc = BASE_PARAMS[sc.COW_FORMAT]
            self.assertRaises(se.InvalidParameterException,
                              artifacts.create, size, vol_format, disk_type,
                              desc, initial_size=size + 1)

    def validate_artifacts(self, artifacts, env):
        try:
            lv = env.lvm.getLV(artifacts.sd_manifest.sdUUID, artifacts.vol_id)
        except se.LogicalVolumeDoesNotExistError:
            raise AssertionError("LV missing")

        if sc.TEMP_VOL_LVTAG not in lv.tags:
            raise AssertionError("Missing TEMP_VOL_LVTAG")

        md_slot = int(blockVolume.getVolumeTag(artifacts.sd_manifest.sdUUID,
                                               self.vol_id,
                                               sc.TAG_PREFIX_MD))
        self.validate_metadata(env, md_slot, artifacts)
        # TODO: Validate lease area once we have a SANLock mock

    def validate_metadata(self, env, md_slot, artifacts):
        path = env.lvm.lvPath(artifacts.sd_manifest.sdUUID, sd.METADATA)
        offset = md_slot * sc.METADATA_SIZE
        md_lines = misc.readblock(path, offset, sc.METADATA_SIZE)
        md = VolumeMetadata.from_lines(md_lines)

        # Test a few fields just to check that metadata was written
        self.assertEqual(artifacts.sd_manifest.sdUUID, md.domain)
        self.assertEqual(artifacts.img_id, md.image)

    def validate_invisibility(self, env, artifacts, is_garbage):
        self.assertEqual(is_garbage, artifacts.is_garbage())
        self.assertFalse(artifacts.is_image())
        self.assertRaises(se.VolumeDoesNotExist,
                          env.sd_manifest.produceVolume, artifacts.img_id,
                          artifacts.vol_id)

    def get_next_free_slot(self, env):
        with env.sd_manifest.acquireVolumeMetadataSlot(
                self.vol_id, sc.VOLUME_MDNUMBLKS) as slot:
            return slot


class TestBlockVolumeArtifactVisibility(VdsmTestCase):

    def setUp(self):
        self.img_id = make_uuid()
        self.vol_id = make_uuid()

    def test_getallimages(self):
        # The current behavior of getAllImages for block domains does not
        # report images that contain only artifacts.  This differs from the
        # file implementation.
        with fake_block_env() as env:
            self.assertEqual(set(), env.sd_manifest.getAllImages())
            artifacts = env.sd_manifest.get_volume_artifacts(
                self.img_id, self.vol_id)
            artifacts.create(*BASE_PARAMS[sc.RAW_FORMAT])
            self.assertEqual(set(), env.sd_manifest.getAllImages())
            artifacts.commit()
            self.assertEqual({self.img_id}, env.sd_manifest.getAllImages())
