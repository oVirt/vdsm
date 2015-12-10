# Copyright 2015 Red Hat, Inc.
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

from testlib import VdsmTestCase, make_file, recorded
from storagetestlib import (
    make_file_volume,
    fake_block_env,
    fake_file_env
)

from storage import sd, blockSD, blockVolume

VOLSIZE = 1048576
MB = 1048576


class FileManifestTests(VdsmTestCase):

    def test_getreaddelay(self):
        with fake_file_env() as env:
            self.assertIsInstance(env.sd_manifest.getReadDelay(), float)

    def test_getvsize(self):
        with fake_file_env() as env:
            sd_manifest = env.sd_manifest
            imguuid, voluuid = make_file_volume(sd_manifest.domaindir, VOLSIZE)
            self.assertEqual(VOLSIZE, sd_manifest.getVSize(imguuid, voluuid))

    def test_getvallocsize(self):
        with fake_file_env() as env:
            sd_manifest = env.sd_manifest
            imguuid, voluuid = make_file_volume(sd_manifest.domaindir, VOLSIZE)
            self.assertEqual(0, sd_manifest.getVAllocSize(imguuid, voluuid))

    def test_getisodomainimagesdir(self):
        with fake_file_env() as env:
            isopath = os.path.join(env.sd_manifest.domaindir, sd.DOMAIN_IMAGES,
                                   sd.ISO_IMAGE_UUID)
            self.assertEquals(isopath, env.sd_manifest.getIsoDomainImagesDir())

    def test_getmdpath(self):
        with fake_file_env() as env:
            sd_manifest = env.sd_manifest
            mdpath = os.path.join(sd_manifest.domaindir, sd.DOMAIN_META_DATA)
            self.assertEquals(mdpath, env.sd_manifest.getMDPath())

    def test_getmetaparam(self):
        with fake_file_env() as env:
            sd_manifest = env.sd_manifest
            self.assertEquals(sd_manifest.sdUUID,
                              sd_manifest.getMetaParam(sd.DMDK_SDUUID))

    def test_getallimages(self):
        with fake_file_env() as env:
            self.assertEqual(set(), env.sd_manifest.getAllImages())
            img_uuid = str(uuid.uuid4())
            make_file_volume(env.sd_manifest.domaindir, VOLSIZE, img_uuid)
            self.assertIn(img_uuid, env.sd_manifest.getAllImages())


class BlockManifestTests(VdsmTestCase):

    def test_getreaddelay(self):
        with fake_block_env() as env:
            vg_name = env.sd_manifest.sdUUID
            make_file(env.lvm.lvPath(vg_name, 'metadata'))
            self.assertIsInstance(env.sd_manifest.getReadDelay(), float)

    def test_getvsize_active_lv(self):
        # Tests the path when the device file is present
        with fake_block_env() as env:
            vg_name = env.sd_manifest.sdUUID
            lv_name = str(uuid.uuid4())
            env.lvm.createLV(vg_name, lv_name, VOLSIZE / MB)
            env.lvm.fake_lv_symlink_create(vg_name, lv_name)
            self.assertEqual(VOLSIZE,
                             env.sd_manifest.getVSize('<imgUUID>', lv_name))

    def test_getvsize_inactive_lv(self):
        # Tests the path when the device file is not present
        with fake_block_env() as env:
            lv_name = str(uuid.uuid4())
            env.lvm.createLV(env.sd_manifest.sdUUID, lv_name, VOLSIZE / MB)
            self.assertEqual(VOLSIZE,
                             env.sd_manifest.getVSize('<imgUUID>', lv_name))

    def test_getmetaparam(self):
        with fake_block_env() as env:
            self.assertEquals(env.sd_manifest.sdUUID,
                              env.sd_manifest.getMetaParam(sd.DMDK_SDUUID))

    def test_getblocksize_defaults(self):
        with fake_block_env() as env:
            self.assertEquals(512, env.sd_manifest.logBlkSize)
            self.assertEquals(512, env.sd_manifest.phyBlkSize)

    def test_getblocksize(self):
        with fake_block_env() as env:
            sduuid = env.sd_manifest.sdUUID
            metadata = dict(env.sd_manifest._metadata)
            metadata[blockSD.DMDK_LOGBLKSIZE] = 2048
            metadata[blockSD.DMDK_PHYBLKSIZE] = 1024
            sd_manifest = blockSD.BlockStorageDomainManifest(sduuid, metadata)
            self.assertEquals(2048, sd_manifest.logBlkSize)
            self.assertEquals(1024, sd_manifest.phyBlkSize)


class BlockDomainMetadataSlotTests(VdsmTestCase):

    def test_metaslot_selection(self):
        with fake_block_env() as env:
            lvs = ('0b6287f0-3679-4c4d-8be5-9bbfe3ec9c1f',
                   'ea13af29-b64a-4d1a-b35f-3e6ab15c3b04')
            for lv, offset in zip(lvs, [4, 7]):
                sduuid = env.sd_manifest.sdUUID
                env.lvm.createLV(sduuid, lv, VOLSIZE)
                tag = blockVolume.TAG_PREFIX_MD + str(offset)
                env.lvm.addtag(sduuid, lv, tag)
            with env.sd_manifest.acquireVolumeMetadataSlot(None, 1) as mdSlot:
                self.assertEqual(mdSlot, 5)

    def test_metaslot_lock(self):
        with fake_block_env() as env:
            with env.sd_manifest.acquireVolumeMetadataSlot(None, 1):
                acquired = env.sd_manifest._lvTagMetaSlotLock.acquire(False)
                self.assertFalse(acquired)


class TestingStorageDomainManifest(sd.StorageDomainManifest):
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


class DomainLockTests(VdsmTestCase):

    def test_domainlock_contextmanager(self):
        expected_calls = [("acquireDomainLock", (1,), {}),
                          ("dummy", (), {}),
                          ("releaseDomainLock", (), {})]
        manifest = TestingStorageDomainManifest()
        with manifest.domain_lock(1):
            manifest.dummy()
        self.assertEqual(manifest.__calls__, expected_calls)

    def test_domainlock_contextmanager_exception(self):
        class InjectedFailure(Exception):
            pass

        expected_calls = [("acquireDomainLock", (1,), {}),
                          ("releaseDomainLock", (), {})]
        manifest = TestingStorageDomainManifest()
        with self.assertRaises(InjectedFailure):
            with manifest.domain_lock(1):
                raise InjectedFailure()
        self.assertEqual(manifest.__calls__, expected_calls)
