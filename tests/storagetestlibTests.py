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
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA  02110-1301 USA
#
# Refer to the README and COPYING files for full details of the license
#

import os
import uuid

from testlib import expandPermutations, permutations
from testlib import VdsmTestCase
from testlib import TEMPDIR
from storagetestlib import fake_block_env
from storagetestlib import fake_file_env
from storagetestlib import make_block_volume
from storagetestlib import make_file_volume

from storage import blockSD, fileSD, fileVolume, sd

from vdsm import utils
from vdsm.storage import constants as sc


MB = 1024 ** 2


class FakeFileEnvTests(VdsmTestCase):

    def test_no_fakelvm(self):
        with fake_file_env() as env:
            self.assertIsNone(env.lvm)

    def test_repopath_location(self):
        with fake_file_env() as env:
            self.assertTrue(env.sd_manifest.getRepoPath().startswith(TEMPDIR))

    def test_domain_structure(self):
        with fake_file_env() as env:
            self.assertTrue(os.path.exists(env.sd_manifest.metafile))
            images_dir = os.path.dirname(env.sd_manifest.getImagePath('foo'))
            self.assertTrue(os.path.exists(images_dir))

    def test_domain_metadata_io(self):
        with fake_file_env() as env:
            desc = 'foo'
            set_domain_metaparams(env.sd_manifest, {sd.DMDK_DESCRIPTION: desc})

            # Test that metadata is persisted to our temporary storage area
            domain_dir = env.sd_manifest.domaindir
            manifest = fileSD.FileStorageDomainManifest(domain_dir)
            self.assertEqual(desc, manifest.getMetaParam(sd.DMDK_DESCRIPTION))

    def test_volume_structure(self):
        with fake_file_env() as env:
            img_id = str(uuid.uuid4())
            vol_id = str(uuid.uuid4())
            make_file_volume(env.sd_manifest, 0, img_id, vol_id)
            image_dir = env.sd_manifest.getImagePath(img_id)
            files = (vol_id, vol_id + sc.LEASE_FILEEXT,
                     vol_id + fileVolume.META_FILEEXT)
            for f in files:
                path = os.path.join(image_dir, f)
                self.assertTrue(os.path.exists(path))

    def test_volume_metadata_io(self):
        with fake_file_env() as env:
            size = 1 * MB
            img_id = str(uuid.uuid4())
            vol_id = str(uuid.uuid4())
            make_file_volume(env.sd_manifest, size, img_id, vol_id)
            vol = env.sd_manifest.produceVolume(img_id, vol_id)
            desc = 'foo'
            vol.setDescription(desc)

            # Test that metadata is persisted to our temporary storage area
            vol = env.sd_manifest.produceVolume(img_id, vol_id)
            self.assertEqual(desc, vol.getDescription())


@expandPermutations
class FakeBlockEnvTests(VdsmTestCase):

    def test_repopath_location(self):
        with fake_block_env() as env:
            self.assertTrue(env.sd_manifest.getRepoPath().startswith(TEMPDIR))

    def test_domain_structure(self):
        with fake_block_env() as env:
            vg_name = env.sd_manifest.sdUUID
            md_path = env.lvm.lvPath(vg_name, sd.METADATA)
            self.assertTrue(os.path.exists(md_path))

            for lv in blockSD.SPECIAL_LVS:
                self.assertEqual(lv, env.lvm.getLV(vg_name, lv).name)

            images_dir = os.path.join(env.sd_manifest.domaindir,
                                      sd.DOMAIN_IMAGES)
            self.assertTrue(os.path.exists(images_dir))

            # Check the storage repository
            repo_path = env.sd_manifest.getRepoPath()
            domain_link = os.path.join(repo_path, env.sd_manifest.sdUUID)
            self.assertTrue(os.path.islink(domain_link))
            self.assertEqual(env.sd_manifest.domaindir,
                             os.readlink(domain_link))

    def test_domain_metadata_io(self):
        with fake_block_env() as env:
            desc = 'foo'
            set_domain_metaparams(env.sd_manifest, {sd.DMDK_DESCRIPTION: desc})

            # Test that metadata is persisted to our temporary storage area
            sd_id = env.sd_manifest.sdUUID
            manifest = blockSD.BlockStorageDomainManifest(sd_id)
            self.assertEqual(desc, manifest.getMetaParam(sd.DMDK_DESCRIPTION))

    @permutations((
        (MB,),
        (2 * MB - 1,),
        (1,),
        ((sc.VG_EXTENT_SIZE_MB - 1) * MB,),
        (sc.VG_EXTENT_SIZE_MB * MB + 1,),
    ))
    def test_volume_size_alignment(self, size_param):
        with fake_block_env() as env:
            sd_id = env.sd_manifest.sdUUID
            img_id = str(uuid.uuid4())
            vol_id = str(uuid.uuid4())
            make_block_volume(env.lvm, env.sd_manifest, size_param,
                              img_id, vol_id)
            vol = env.sd_manifest.produceVolume(img_id, vol_id)

            extent_size = sc.VG_EXTENT_SIZE_MB * MB
            expected_size = utils.round(size_param, extent_size)
            self.assertEqual(expected_size / sc.BLOCK_SIZE, vol.getSize())
            self.assertEqual(expected_size,
                             int(env.lvm.getLV(sd_id, vol_id).size))
            lv_file_size = os.stat(env.lvm.lvPath(sd_id, vol_id)).st_size
            self.assertEqual(expected_size, lv_file_size)

    def test_volume_metadata_io(self):
        with fake_block_env() as env:
            sd_id = env.sd_manifest.sdUUID
            img_id = str(uuid.uuid4())
            vol_id = str(uuid.uuid4())
            size_mb = sc.VG_EXTENT_SIZE_MB
            size = size_mb * MB
            size_blk = size_mb * MB / sc.BLOCK_SIZE
            make_block_volume(env.lvm, env.sd_manifest, size,
                              img_id, vol_id)

            self.assertEqual(vol_id, env.lvm.getLV(sd_id, vol_id).name)
            vol = env.sd_manifest.produceVolume(img_id, vol_id)
            self.assertEqual(size_blk, vol.getSize())
            desc = 'foo'
            vol.setDescription(desc)

            # Test that metadata is persisted to our temporary storage area
            vol = env.sd_manifest.produceVolume(img_id, vol_id)
            self.assertEqual(desc, vol.getDescription())

    def test_volume_accessibility(self):
        with fake_block_env() as env:
            sd_id = env.sd_manifest.sdUUID
            img_id = str(uuid.uuid4())
            vol_id = str(uuid.uuid4())
            make_block_volume(env.lvm, env.sd_manifest, 1 * MB, img_id, vol_id)

            self.assertTrue(os.path.isfile(env.lvm.lvPath(sd_id, vol_id)))

            domain_path = os.path.join(env.sd_manifest.domaindir,
                                       sd.DOMAIN_IMAGES,
                                       img_id,
                                       vol_id)
            repo_path = os.path.join(env.sd_manifest.getRepoPath(),
                                     sd_id,
                                     sd.DOMAIN_IMAGES,
                                     img_id,
                                     vol_id)
            self.assertNotEqual(repo_path, domain_path)
            # The links to the dev are created only when producing the volume
            self.assertFalse(os.path.isfile(domain_path))
            self.assertFalse(os.path.isfile(repo_path))

            env.sd_manifest.produceVolume(img_id, vol_id)
            self.assertTrue(os.path.samefile(repo_path, domain_path))


def set_domain_metaparams(manifest, params):
    # XXX: Replace calls to this function with the proper manifest APIs once
    # the set* methods are moved from StorageDomain to StorageDomainManifest.
    manifest._metadata.update(params)
