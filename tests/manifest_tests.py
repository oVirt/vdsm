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

from testlib import VdsmTestCase, namedTemporaryDir, make_file
from monkeypatch import MonkeyPatchScope
from storagefakelib import FakeLVM
from storagetestlib import make_filesd_manifest, make_blocksd_manifest, \
    make_file_volume, make_vg

from storage import sd, blockSD, blockVolume

VOLSIZE = 1048576


class FileManifestTests(VdsmTestCase):

    def test_getreaddelay(self):
        with namedTemporaryDir() as tmpdir:
            manifest = make_filesd_manifest(tmpdir)
            self.assertIsInstance(manifest.getReadDelay(), float)

    def test_getvsize(self):
        with namedTemporaryDir() as tmpdir:
            manifest = make_filesd_manifest(tmpdir)
            imguuid, voluuid = make_file_volume(manifest.domaindir, VOLSIZE)
            self.assertEqual(VOLSIZE, manifest.getVSize(imguuid, voluuid))

    def test_getvallocsize(self):
        with namedTemporaryDir() as tmpdir:
            manifest = make_filesd_manifest(tmpdir)
            imguuid, voluuid = make_file_volume(manifest.domaindir, VOLSIZE)
            self.assertEqual(0, manifest.getVAllocSize(imguuid, voluuid))

    def test_getisodomainimagesdir(self):
        with namedTemporaryDir() as tmpdir:
            manifest = make_filesd_manifest(tmpdir)
            isopath = os.path.join(manifest.domaindir, sd.DOMAIN_IMAGES,
                                   sd.ISO_IMAGE_UUID)
            self.assertEquals(isopath, manifest.getIsoDomainImagesDir())

    def test_getmdpath(self):
        with namedTemporaryDir() as tmpdir:
            manifest = make_filesd_manifest(tmpdir)
            mdpath = os.path.join(manifest.domaindir, sd.DOMAIN_META_DATA)
            self.assertEquals(mdpath, manifest.getMDPath())

    def test_getmetaparam(self):
        with namedTemporaryDir() as tmpdir:
            metadata = {sd.DMDK_VERSION: 3}
            manifest = make_filesd_manifest(tmpdir, metadata)
            metadata[sd.DMDK_SDUUID] = manifest.sdUUID
            self.assertEquals(manifest.sdUUID,
                              manifest.getMetaParam(sd.DMDK_SDUUID))


class BlockManifestTests(VdsmTestCase):

    def test_getreaddelay(self):
        with namedTemporaryDir() as tmpdir:
            lvm = FakeLVM(tmpdir)
            with MonkeyPatchScope([(blockSD, 'lvm', lvm)]):
                manifest = make_blocksd_manifest(tmpdir)
                vg_name = manifest.sdUUID
                make_file(lvm.lvPath(vg_name, 'metadata'))
                self.assertIsInstance(manifest.getReadDelay(), float)

    def test_getvsize_active_lv(self):
        # Tests the path when the device file is present
        with namedTemporaryDir() as tmpdir:
            lvm = FakeLVM(tmpdir)
            with MonkeyPatchScope([(blockSD, 'lvm', lvm)]):
                manifest = make_blocksd_manifest(tmpdir)
                vg_name = make_vg(lvm, manifest)
                lv_name = str(uuid.uuid4())
                lvm.createLV(vg_name, lv_name, VOLSIZE)
                lvm.fake_lv_symlink_create(vg_name, lv_name)
                self.assertEqual(VOLSIZE,
                                 manifest.getVSize('<imgUUID>', lv_name))

    def test_getvsize_inactive_lv(self):
        # Tests the path when the device file is not present
        with namedTemporaryDir() as tmpdir:
            lvm = FakeLVM(tmpdir)
            with MonkeyPatchScope([(blockSD, 'lvm', lvm)]):
                manifest = make_blocksd_manifest(tmpdir)
                vg_name = make_vg(lvm, manifest)
                lv_name = str(uuid.uuid4())
                lvm.createLV(vg_name, lv_name, VOLSIZE)
                self.assertEqual(VOLSIZE,
                                 manifest.getVSize('<imgUUID>', lv_name))

    def test_getmetaparam(self):
        with namedTemporaryDir() as tmpdir:
            lvm = FakeLVM(tmpdir)
            with MonkeyPatchScope([(blockSD, 'lvm', lvm)]):
                metadata = {sd.DMDK_VERSION: 3}
                manifest = make_blocksd_manifest(tmpdir, metadata)
                metadata[sd.DMDK_SDUUID] = manifest.sdUUID
                self.assertEquals(manifest.sdUUID,
                                  manifest.getMetaParam(sd.DMDK_SDUUID))

    def test_getblocksize_defaults(self):
        with namedTemporaryDir() as tmpdir:
            lvm = FakeLVM(tmpdir)
            with MonkeyPatchScope([(blockSD, 'lvm', lvm)]):
                manifest = make_blocksd_manifest(tmpdir)
                self.assertEquals(512, manifest.logBlkSize)
                self.assertEquals(512, manifest.phyBlkSize)

    def test_getblocksize(self):
        with namedTemporaryDir() as tmpdir:
            lvm = FakeLVM(tmpdir)
            with MonkeyPatchScope([(blockSD, 'lvm', lvm)]):
                metadata = {sd.DMDK_VERSION: 3,
                            blockSD.DMDK_LOGBLKSIZE: 2048,
                            blockSD.DMDK_PHYBLKSIZE: 1024}
                manifest = make_blocksd_manifest(tmpdir, metadata)
                self.assertEquals(2048, manifest.logBlkSize)
                self.assertEquals(1024, manifest.phyBlkSize)


class BlockDomainMetadataSlotTests(VdsmTestCase):

    def test_metaslot_selection(self):
        with namedTemporaryDir() as tmpdir:
            lvm = FakeLVM(tmpdir)
            with MonkeyPatchScope([(blockSD, 'lvm', lvm)]):
                manifest = make_blocksd_manifest(tmpdir)
                make_vg(lvm, manifest)
                lvs = ('0b6287f0-3679-4c4d-8be5-9bbfe3ec9c1f',
                       'ea13af29-b64a-4d1a-b35f-3e6ab15c3b04')
                for lv, offset in zip(lvs, [4, 7]):
                    lvm.createLV(manifest.sdUUID, lv, VOLSIZE)
                    tag = blockVolume.TAG_PREFIX_MD + str(offset)
                    lvm.addtag(manifest.sdUUID, lv, tag)
                with manifest.acquireVolumeMetadataSlot(None, 1) as mdSlot:
                    self.assertEqual(mdSlot, 5)

    def test_metaslot_lock(self):
        with namedTemporaryDir() as tmpdir:
            lvm = FakeLVM(tmpdir)
            with MonkeyPatchScope([(blockSD, 'lvm', lvm)]):
                manifest = make_blocksd_manifest(tmpdir)
                make_vg(lvm, manifest)
                with manifest.acquireVolumeMetadataSlot(None, 1):
                    acquired = manifest._lvTagMetaSlotLock.acquire(False)
                    self.assertFalse(acquired)
